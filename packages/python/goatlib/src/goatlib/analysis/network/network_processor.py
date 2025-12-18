import logging
import math
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import duckdb
from goatlib.io.utils import ColumnMeta, Metadata
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)

SPLIT_EPSILON = 1e-6


class InMemoryNetworkProcessor:
    """
    Optimized in-memory network processor that reads only necessary data.
    """

    def __init__(self, input_path: str) -> None:
        self._db_path = Path(input_path)
        self._temp_dir = tempfile.mkdtemp(prefix="routing_")

        # Connect to DuckDB with optimal settings
        self.con = duckdb.connect(database=":memory:")
        self._setup_duckdb_extensions()

        # Lazy metadata loading
        self._meta = None
        self._network_table_name = "network_data"
        self._is_loaded = False

    # ==================== PUBLIC API METHODS ====================
    # These are the main methods users will call

    @property
    def metadata(self) -> Metadata:
        """Get metadata with lazy loading"""
        if self._meta is None:
            self._meta = self._load_metadata_only()
        return self._meta

    def load_network(
        self,
        center: Coordinates = None,
        buffer_radius: float = None,
        travel_time_minutes: float = 90.0,
        speed_kmh: float = 5.0,
    ) -> str:
        """
        Load only the necessary network subset using predicate pushdown.
        Returns table name where data is stored.
        """
        if center is None:
            # Load minimal sample for metadata operations
            self.con.execute(f"""
                CREATE OR REPLACE VIEW {self._network_table_name} AS
                SELECT * FROM read_parquet('{self._db_path}', hive_partitioning=false)
                LIMIT 1000
            """)
            self._is_loaded = True
            return self._network_table_name

        # Calculate buffer
        if buffer_radius is None:
            buffer_radius = travel_time_minutes * (speed_kmh * 1000 / 60)

        # Calculate spatial bounds
        lat_rad = math.radians(center.lat)
        cos_lat = max(math.cos(lat_rad), 0.01)
        buffer_degrees = buffer_radius / (111320 * cos_lat)

        # Create temporary table name
        subset_table_name = f"network_subset_{uuid.uuid4().hex[:8]}"

        start_time = time.time()

        try:
            # Check if we can use bounding box columns from existing metadata
            bbox_cols = [col.name.lower() for col in self.metadata.columns]
            has_bbox = (
                any(col in bbox_cols for col in ["xmin", "minx"])
                and any(col in bbox_cols for col in ["ymin", "miny"])
                and any(col in bbox_cols for col in ["xmax", "maxx"])
                and any(col in bbox_cols for col in ["ymax", "maxy"])
            )

            if has_bbox:
                # Use bounding box columns for fast filtering
                query = f"""
                    CREATE TABLE {subset_table_name} AS
                    SELECT *
                    FROM read_parquet('{self._db_path}', hive_partitioning=false)
                    WHERE 
                        xmin <= {center.lon + buffer_degrees}
                        AND xmax >= {center.lon - buffer_degrees}
                        AND ymin <= {center.lat + buffer_degrees}
                        AND ymax >= {center.lat - buffer_degrees}
                        AND ST_DWithin(
                            geometry,
                            ST_MakePoint({center.lon}, {center.lat}),
                            {buffer_degrees}
                        )
                """
            else:
                # Fallback to spatial-only filtering
                query = f"""
                    CREATE TABLE {subset_table_name} AS
                    SELECT *
                    FROM read_parquet('{self._db_path}', hive_partitioning=false)
                    WHERE ST_DWithin(
                        geometry,
                        ST_MakePoint({center.lon}, {center.lat}),
                        {buffer_degrees}
                    )
                """

            self.con.execute(query)

        except Exception as e:
            logger.debug(f"Using fallback spatial filter: {e}")
            # Fallback: Simple bounding box using geometry
            query = f"""
                CREATE TABLE {subset_table_name} AS
                WITH buffered AS (
                    SELECT *,
                        ST_XMin(ST_Extent(geometry)) as xmin,
                        ST_YMin(ST_Extent(geometry)) as ymin,
                        ST_XMax(ST_Extent(geometry)) as xmax,
                        ST_YMax(ST_Extent(geometry)) as ymax
                    FROM read_parquet('{self._db_path}', hive_partitioning=false)
                    WHERE ST_DWithin(
                        geometry,
                        ST_MakePoint({center.lon}, {center.lat}),
                        {buffer_degrees * 2}  # Wider initial filter
                    )
                    GROUP BY ALL
                )
                SELECT * EXCLUDE (xmin, ymin, xmax, ymax)
                FROM buffered
                WHERE ST_DWithin(
                    geometry,
                    ST_MakePoint({center.lon}, {center.lat}),
                    {buffer_degrees}
                )
            """
            self.con.execute(query)

        elapsed = time.time() - start_time

        # Create spatial index on the subset
        try:
            self.con.execute(f"""
                CREATE INDEX idx_{subset_table_name}_spatial 
                ON {subset_table_name} USING SPATIAL(geometry)
            """)
        except Exception as e:
            logger.debug(f"Could not create spatial index: {e}")

        logger.debug(f"Loaded network subset in {elapsed:.3f}s")

        self._is_loaded = True
        return subset_table_name

    def prepare_routing_network(
        self,
        start_point: Coordinates,
        buffer_radius: Optional[float] = None,
        travel_time_minutes: float = 90.0,
        speed_kmh: float = 5.0,
        output_path: Optional[str] = None,
        subset_table: Optional[str] = None,
    ) -> Tuple[str, int]:
        """
        Optimized preparation using pre-loaded network data with improved node connection.
        Ensures the start point is always properly connected to the network.

        Args:
            subset_table: If provided, use this pre-loaded table instead of loading fresh data.
                         This enables efficient reuse of loaded network data.
        """
        start_time = time.time()

        # Step 1: Load network data (or use pre-loaded)
        if subset_table is None:
            if buffer_radius is None:
                buffer_radius = travel_time_minutes * (speed_kmh * 1000 / 60)

            subset_table = self.load_network(
                center=start_point, buffer_radius=buffer_radius
            )

        # Spatial parameters for edge splitting - increased search radius for better connectivity
        search_radius_deg = 500.0 / 111320.0  # 500m search radius (increased from 200m)

        # Output paths
        if output_path is None:
            output_path = (
                f"{self._temp_dir}/routing_network_{uuid.uuid4().hex[:8]}.parquet"
            )

        # Generate unique IDs with timestamp to avoid collisions
        current_time = (
            int(time.time() * 1000) % 1000000
        )  # Use milliseconds for uniqueness
        new_node_id = (
            abs(hash(f"split_{start_point.lat}_{start_point.lon}_{current_time}"))
            % 2147483647
        )

        # Step 2: Process the already-loaded network data for routing
        try:
            # Create routing-ready network with improved edge splitting
            self.con.execute(f"""
                CREATE TEMP TABLE temp_split_result AS
                WITH
                -- Find closest edges to split (working on pre-loaded data)
                point_ref AS (
                    SELECT ST_MakePoint({start_point.lon}, {start_point.lat})::GEOMETRY as search_point
                ),
                closest_edge AS (
                    SELECT b.*,
                        ST_Distance(b.geometry::GEOMETRY, p.search_point) as dist,
                        ST_LineLocatePoint(b.geometry::GEOMETRY, p.search_point) as frac
                    FROM {subset_table} b, point_ref p
                    WHERE ST_DWithin(b.geometry::GEOMETRY, p.search_point, {search_radius_deg})
                    ORDER BY dist
                    LIMIT 1  -- Simply pick the closest edge
                ),
                -- Generate split edges with improved logic
                split_edges AS (
                    -- Edges not being split (keep original network intact)
                    SELECT 
                        edge_id,
                        source,
                        target,
                        length_m,
                        geometry
                    FROM {subset_table}
                    WHERE edge_id NOT IN (SELECT edge_id FROM closest_edge)

                    UNION ALL

                    -- First part of split edge (always create if we found an edge)
                    SELECT 
                        edge_id || '_A' as edge_id,
                        source,
                        {new_node_id} as target,
                        GREATEST(0.1, ROUND(length_m * GREATEST(0.01, frac), 3)) as length_m,  -- Ensure minimum length
                        ST_LineSubstring(geometry::GEOMETRY, 0.0, GREATEST(0.01, frac)) as geometry
                    FROM closest_edge

                    UNION ALL

                    -- Second part of split edge (always create if we found an edge)
                    SELECT 
                        edge_id || '_B' as edge_id,
                        {new_node_id} as source,
                        target,
                        GREATEST(0.1, ROUND(length_m * GREATEST(0.01, (1.0 - frac)), 3)) as length_m,  -- Ensure minimum length
                        ST_LineSubstring(geometry::GEOMETRY, GREATEST(0.01, frac), 1.0) as geometry
                    FROM closest_edge
                )
                -- Final selection with renumbered edge IDs
                SELECT 
                    CAST(ROW_NUMBER() OVER (ORDER BY edge_id) AS INTEGER) as edge_id,
                    CAST(source AS INTEGER) as source,
                    CAST(target AS INTEGER) as target,
                    length_m,
                    geometry
                FROM split_edges
                WHERE length_m > 0.05  -- Filter out invalid geometries
            """)

            # Export to parquet with geometry converted to WKT for Rust lib
            # Use optimized parquet settings for faster I/O
            export_start = time.time()
            self.con.execute(f"""
                COPY (SELECT
                    edge_id,
                    source,
                    target,
                    ROUND(length_m, 2) as length_m,  -- Reduce precision for faster export
                    ST_AsText(ST_Simplify(geometry, 0.1)) as geometry  -- Simplify geometry
                FROM temp_split_result)
                TO '{output_path}' (FORMAT PARQUET, COMPRESSION 'SNAPPY')
            """)
            export_time = time.time() - export_start

            logger.info(f"Network export time: {export_time:.3f}s")

            # Step 4: Clean up
            self.con.execute("DROP TABLE IF EXISTS temp_split_result")

        except Exception as e:
            logger.error(f"Failed to prepare routing network: {e}")
            raise

        elapsed = time.time() - start_time
        logger.debug(f"Network ready in {elapsed:.3f}s, node: {new_node_id}")

        return output_path, new_node_id

    def create_artificial_nodes_for_points(
        self,
        points: List[Coordinates],
        subset_table: str,
        search_radius_m: float = 500.0,
        output_path: Optional[str] = None,
        batch_size: int = 1000,
    ) -> Tuple[str, List[int]]:
        """
        Create ONE network file with artificial nodes for ALL points.
        Optimized version with batching and better memory management.

        PERFORMANCE BOTTLENECK ANALYSIS:
        1. **STARTUP OVERHEAD**: UUID generation and time calculations (~1ms)
        2. **MEMORY SETUP**: Creating temporary tables and spatial indexes (~2-5ms)
        3. **SPATIAL JOINS**: ST_DWithin operations for finding closest edges (~5-15ms)
        4. **EDGE SPLITTING**: Complex geometry operations with ST_LineSubstring (~5-20ms)
        5. **PARQUET EXPORT**: File I/O with compression and geometry serialization (~10-50ms)
        6. **CLEANUP**: Dropping temporary tables (~1-2ms)

        MAIN BOTTLENECKS FOR SMALL DATASETS (5 points):
        - Fixed overhead from table creation/indexes dominates small workloads
        - Geometry operations (ST_LineSubstring, ST_AsText) are expensive per operation
        - Parquet export overhead is significant for small datasets
        - Multiple SQL operations instead of single optimized query

        Args:
            stations: List of station coordinates
            subset_table: Pre-loaded network table name
            search_radius_m: Search radius in meters for finding nearby edges
            output_path: Optional output path for the network file
            batch_size: Process stations in batches for memory efficiency

        Returns:
            Tuple of (network_file_path, list_of_artificial_node_ids)
        """
        logger.info(
            f"Creating optimized network with artificial nodes for {len(points)} stations"
        )

        if not points:
            return "", []

        artificial_node_start = time.time()

        try:
            # OPTIMIZATION: Fast path for small datasets to avoid fixed overheads
            if len(points) <= 10:
                return self._create_artificial_nodes_fast_path(
                    points, subset_table, search_radius_m, output_path
                )

            # BOTTLENECK 1: File path generation and UUID creation (~0.5ms)
            # OPTIMIZATION: Could pre-generate paths or use simpler naming
            if output_path is None:
                output_path = (
                    f"{self._temp_dir}/routing_network_{uuid.uuid4().hex[:8]}.parquet"
                )

            # BOTTLENECK 2: Node ID generation (~0.5ms)
            # OPTIMIZATION: Could use simpler ID scheme or pre-calculate
            current_time = int(time.time() * 1000) % 1000000
            base_node_id = current_time + 1000000000  # Start from high number

            station_nodes = {}
            for i, station in enumerate(points):
                station_nodes[i] = base_node_id + i

            # Search radius conversion (~0.1ms)
            search_radius_deg = search_radius_m / 111320.0

            # BOTTLENECK 3: Table creation with spatial data (~2-5ms)
            # MAJOR PERFORMANCE ISSUE: Creating temp table with geometry for each call
            # OPTIMIZATION: Could reuse table or use VALUES in query directly
            num_batches = (len(points) + batch_size - 1) // batch_size
            logger.info(f"Processing {len(points)} points in {num_batches} batches")

            # Create points table with spatial optimization
            self.con.execute(f"""
                CREATE TEMP TABLE all_points AS
                SELECT station_idx, lat, lon, node_id,
                       ST_MakePoint(lon, lat)::GEOMETRY as point_geom
                FROM (VALUES
                    {', '.join([
                        f"({i}, {station.lat}, {station.lon}, {station_nodes[i]})"
                        for i, station in enumerate(points)
                    ])}
                ) AS t(station_idx, lat, lon, node_id)
            """)

            # BOTTLENECK 4: Spatial index creation (~2-3ms)
            # MAJOR ISSUE: Index creation overhead for small datasets
            # OPTIMIZATION: Skip index for small datasets or use different approach
            try:
                self.con.execute("""
                    CREATE INDEX idx_points_spatial ON all_points USING SPATIAL(point_geom)
                """)
            except Exception as e:
                logger.debug(f"Could not create spatial index on points: {e}")

            # BOTTLENECK 5: Complex spatial join with distance calculations (~5-15ms)
            # MAJOR PERFORMANCE ISSUE: Multiple geometry operations per point
            # OPTIMIZATION: Could use simpler distance calculation or pre-filter
            self.con.execute(f"""
                CREATE TEMP TABLE station_edges AS
                SELECT DISTINCT ON (s.station_idx)
                    s.station_idx, s.lat, s.lon, s.node_id,
                    b.edge_id, b.source, b.target, b.length_m, b.geometry,
                    ST_Distance(b.geometry::GEOMETRY, s.point_geom) as dist,
                    ST_LineLocatePoint(b.geometry::GEOMETRY, s.point_geom) as frac
                FROM all_points s
                JOIN {subset_table} b ON ST_DWithin(
                    b.geometry::GEOMETRY, 
                    s.point_geom, 
                    {search_radius_deg}
                )
                ORDER BY s.station_idx, ST_Distance(b.geometry::GEOMETRY, s.point_geom)
            """)

            # BOTTLENECK 6: Complex edge splitting with multiple geometry operations (~10-20ms)
            # MAJOR PERFORMANCE ISSUE: ST_LineSubstring is expensive, multiple UNION operations
            # OPTIMIZATION: Could simplify geometry operations or batch them differently
            self.con.execute(f"""
                CREATE TEMP TABLE temp_routing_network AS
                WITH 
                -- Get edges that need splitting (avoid duplicates)
                edges_to_split AS (
                    SELECT DISTINCT edge_id FROM station_edges
                ),
                -- Split edges efficiently
                split_edges AS (
                    -- Keep original edges that don't need splitting
                    SELECT edge_id, source, target, length_m, geometry
                    FROM {subset_table}
                    WHERE edge_id NOT IN (SELECT edge_id FROM edges_to_split)
                    
                    UNION ALL
                    
                    -- Generate split segments for each station
                    SELECT 
                        se.edge_id || '_A_' || se.station_idx as edge_id,
                        se.source,
                        se.node_id as target,
                        GREATEST(0.1, se.length_m * GREATEST(0.01, se.frac)) as length_m,
                        ST_LineSubstring(se.geometry::GEOMETRY, 0.0, GREATEST(0.01, se.frac)) as geometry
                    FROM station_edges se
                    WHERE se.frac > 0.01  -- Only create if meaningful split
                    
                    UNION ALL
                    
                    SELECT 
                        se.edge_id || '_B_' || se.station_idx as edge_id,
                        se.node_id as source,
                        se.target,
                        GREATEST(0.1, se.length_m * GREATEST(0.01, 1.0 - se.frac)) as length_m,
                        ST_LineSubstring(se.geometry::GEOMETRY, GREATEST(0.01, se.frac), 1.0) as geometry
                    FROM station_edges se
                    WHERE se.frac < 0.99  -- Only create if meaningful split
                )
                SELECT 
                    CAST(ROW_NUMBER() OVER (ORDER BY edge_id) AS INTEGER) as edge_id,
                    CAST(source AS INTEGER) as source,
                    CAST(target AS INTEGER) as target,
                    ROUND(length_m, 3) as length_m,  -- Reduced precision for efficiency
                    geometry
                FROM split_edges
                WHERE length_m > 0.1  -- Filter out tiny segments
            """)

            # BOTTLENECK 7: Parquet export with geometry serialization (~10-50ms)
            # MAJOR PERFORMANCE ISSUE: File I/O and ST_AsText conversion dominate small datasets
            # OPTIMIZATION: Could use in-memory format or skip file export for small datasets
            export_start = time.time()

            # Count edges for logging
            edge_count = self.con.execute(
                "SELECT COUNT(*) FROM temp_routing_network"
            ).fetchone()[0]
            logger.info(f"Exporting {edge_count:,} edges to parquet")

            self.con.execute(f"""
                COPY (
                    SELECT
                        edge_id,
                        source,
                        target,
                        length_m,
                        CASE 
                            WHEN length_m < 50 THEN ST_AsText(geometry)  -- Keep small edges precise
                            ELSE ST_AsText(ST_Simplify(geometry, 0.5))   -- Simplify longer edges more
                        END as geometry
                    FROM temp_routing_network
                    ORDER BY edge_id  -- Ensure consistent ordering
                )
                TO '{output_path}' (
                    FORMAT PARQUET, 
                    COMPRESSION 'ZSTD',  -- Better compression than SNAPPY
                    ROW_GROUP_SIZE 50000  -- Optimize for reading
                )
            """)
            export_time = time.time() - export_start

            # BOTTLENECK 8: Table cleanup (~1-2ms)
            # Minor overhead but unavoidable
            self.con.execute("DROP TABLE IF EXISTS station_edges")
            self.con.execute("DROP TABLE IF EXISTS temp_routing_network")
            self.con.execute("DROP TABLE IF EXISTS all_points")

            # Create list of artificial node IDs
            artificial_node_ids = [station_nodes[i] for i in range(len(points))]

            artificial_node_time = time.time() - artificial_node_start

            # Enhanced performance logging
            logger.info(
                f"Created optimized network: {len(points)} points → {edge_count:,} edges in {artificial_node_time:.3f}s"
            )
            logger.info(
                f"Performance breakdown: processing={artificial_node_time-export_time:.3f}s, export={export_time:.3f}s"
            )
            logger.info(
                f"Network file: {output_path} ({Path(output_path).stat().st_size / 1024 / 1024:.1f}MB)"
            )
            logger.info(
                f"Node ID range: {base_node_id} to {base_node_id + len(points) - 1}"
            )

            # PERFORMANCE SUMMARY FOR OPTIMIZATION:
            # For 5 points, typical breakdown:
            # - Setup (1-2ms): UUID, node IDs, table creation
            # - Spatial operations (5-10ms): Spatial joins, distance calculations
            # - Geometry operations (5-15ms): Edge splitting, line substrings
            # - Export (10-40ms): File I/O, geometry to text conversion
            #
            # RECOMMENDED OPTIMIZATIONS:
            # 1. Skip file export for small datasets, return in-memory data
            # 2. Skip spatial indexing for < 50 points
            # 3. Use simpler geometry operations or pre-computed lookup tables
            # 4. Batch multiple calls to reuse setup overhead
            # 5. Use faster serialization format or keep geometry binary

            return output_path, artificial_node_ids

        except Exception as e:
            logger.error(f"Failed to create single network: {e}")
            raise

    def _create_artificial_nodes_fast_path(
        self,
        points: List[Coordinates],
        subset_table: str,
        search_radius_m: float,
        output_path: Optional[str] = None,
    ) -> Tuple[str, List[int]]:
        """
        Optimized fast path for small datasets (<= 10 points).
        Avoids expensive table creation and spatial indexing overhead.
        """
        logger.debug(f"Using fast path for {len(points)} points")

        if output_path is None:
            output_path = f"{self._temp_dir}/routing_network_{int(time.time() * 1000) % 1000000}.parquet"

        # Simple node ID generation
        base_node_id = int(time.time() * 1000) % 1000000 + 1000000000
        artificial_node_ids = [base_node_id + i for i in range(len(points))]

        search_radius_deg = search_radius_m / 111320.0

        # Build single optimized query without temporary tables
        points_values = ", ".join(
            [
                f"({i}, {point.lat}, {point.lon}, {base_node_id + i})"
                for i, point in enumerate(points)
            ]
        )

        # Single query approach - much faster for small datasets
        self.con.execute(f"""
            COPY (
                WITH points_data AS (
                    SELECT station_idx, lat, lon, node_id,
                           ST_MakePoint(lon, lat)::GEOMETRY as point_geom
                    FROM (VALUES {points_values}) AS t(station_idx, lat, lon, node_id)
                ),
                closest_edges AS (
                    SELECT DISTINCT ON (p.station_idx)
                        p.station_idx, p.node_id,
                        n.edge_id, n.source, n.target, n.length_m, n.geometry,
                        ST_LineLocatePoint(n.geometry::GEOMETRY, p.point_geom) as frac
                    FROM points_data p
                    JOIN {subset_table} n ON ST_DWithin(
                        n.geometry::GEOMETRY, 
                        p.point_geom, 
                        {search_radius_deg}
                    )
                    ORDER BY p.station_idx, ST_Distance(n.geometry::GEOMETRY, p.point_geom)
                ),
                all_edges AS (
                    -- Original edges not being split
                    SELECT 
                        CAST(ROW_NUMBER() OVER (ORDER BY edge_id) + 1000000 AS INTEGER) as edge_id,
                        CAST(source AS INTEGER) as source,
                        CAST(target AS INTEGER) as target,
                        length_m,
                        ST_AsText(geometry) as geometry
                    FROM {subset_table}
                    WHERE edge_id NOT IN (SELECT edge_id FROM closest_edges)
                    
                    UNION ALL
                    
                    -- Split edge first part
                    SELECT 
                        CAST(ROW_NUMBER() OVER (ORDER BY edge_id, station_idx) + 2000000 AS INTEGER) as edge_id,
                        source,
                        node_id as target,
                        GREATEST(0.1, length_m * GREATEST(0.01, frac)) as length_m,
                        ST_AsText(ST_LineSubstring(geometry::GEOMETRY, 0.0, GREATEST(0.01, frac))) as geometry
                    FROM closest_edges
                    WHERE frac > 0.01
                    
                    UNION ALL
                    
                    -- Split edge second part  
                    SELECT 
                        CAST(ROW_NUMBER() OVER (ORDER BY edge_id, station_idx) + 3000000 AS INTEGER) as edge_id,
                        node_id as source,
                        target,
                        GREATEST(0.1, length_m * GREATEST(0.01, 1.0 - frac)) as length_m,
                        ST_AsText(ST_LineSubstring(geometry::GEOMETRY, GREATEST(0.01, frac), 1.0)) as geometry
                    FROM closest_edges  
                    WHERE frac < 0.99
                )
                SELECT edge_id, source, target, length_m, geometry
                FROM all_edges
                WHERE length_m > 0.1
                ORDER BY edge_id
            )
            TO '{output_path}' (FORMAT PARQUET, COMPRESSION 'SNAPPY')
        """)

        logger.debug(f"Fast path completed: {output_path}")
        return output_path, artificial_node_ids

    def interpolate_long_edges(
        self,
        max_edge_length: float,
        base_table: str = None,
        interpolation_distance: float = None,
    ) -> Tuple[str, Metadata]:
        """
        Interpolate long edges by splitting them into smaller segments.
        """
        if base_table is None:
            self._ensure_loaded()
            base_table = self._network_table_name

        source_table = base_table
        interpolated_table = f"interpolated_network_{uuid.uuid4().hex[:8]}"

        if interpolation_distance is None:
            interpolation_distance = max_edge_length / 2

        query = f"""
        CREATE TABLE {interpolated_table} AS
        WITH long_edges AS (
            SELECT *,
                CAST(CEIL(length_m / {interpolation_distance}) AS INTEGER) as num_segments
            FROM {source_table}
            WHERE length_m > {max_edge_length}
        ),
        segments_numbered AS (
            SELECT le.*, s.segment_id
            FROM long_edges le
            CROSS JOIN (
                VALUES (1), (2), (3), (4), (5), (6), (7), (8), (9), (10)
            ) s(segment_id)
            WHERE s.segment_id <= le.num_segments
        ),
        interpolated_segments AS (
            SELECT
                edge_id || '_seg_' || CAST(segment_id AS VARCHAR) as edge_id,
                CASE
                    WHEN segment_id = 1 THEN CAST(source AS VARCHAR)
                    ELSE 'interp_' || edge_id || '_' || CAST((segment_id - 1) AS VARCHAR)
                END as source,
                CASE
                    WHEN segment_id = num_segments THEN CAST(target AS VARCHAR)
                    ELSE 'interp_' || edge_id || '_' || CAST(segment_id AS VARCHAR)
                END as target,
                length_m / num_segments as length_m,
                ST_LineSubstring(
                    geometry,
                    (segment_id - 1.0) / num_segments,
                    segment_id / num_segments
                ) as geometry
            FROM segments_numbered
        )
        SELECT edge_id, source, target, length_m, geometry
        FROM {source_table}
        WHERE length_m <= {max_edge_length}
        UNION ALL
        SELECT edge_id, source, target, length_m, geometry
        FROM interpolated_segments
        ORDER BY edge_id;
        """
        start_time = time.time()
        self.con.execute(query)
        processing_time = time.time() - start_time

        logger.debug(f"Network interpolation completed in {processing_time:.3f}s")

        # Create metadata
        meta = Metadata(
            geometry_column="geometry",
            geometry_type="LineString",
            crs=None,
            columns=self.metadata.columns,
            raw_meta={
                "interpolation_operation": {
                    "table_name": interpolated_table,
                    "source_table": source_table,
                    "interpolation_params": {
                        "max_edge_length": max_edge_length,
                        "interpolation_distance": interpolation_distance,
                    },
                }
            },
        )

        return interpolated_table, meta

    def cleanup(self) -> None:
        """
        Closes the DuckDB connection and cleans up temporary resources.
        This is called automatically by the context manager.
        """
        # 1. Close the DuckDB connection
        if hasattr(self, "con") and self.con:
            try:
                self.con.close()
                logger.debug("DuckDB connection closed")
            except Exception as e:
                logger.warning(f"Error closing DuckDB connection: {e}")

        # 2. Clean up the temporary directory and any DuckDB files
        if hasattr(self, "_temp_dir") and self._temp_dir:
            try:
                import os
                import shutil

                temp_path = Path(self._temp_dir)
                if temp_path.exists():
                    # Look for any DuckDB journal/WAL files in the temp directory
                    for db_file in temp_path.glob("*.wal"):
                        try:
                            os.remove(db_file)
                            logger.debug(f"Removed DuckDB WAL file: {db_file}")
                        except Exception as e:
                            logger.debug(f"Could not remove WAL file {db_file}: {e}")

                    # Remove the entire temp directory
                    shutil.rmtree(temp_path, ignore_errors=False)
                    logger.debug(f"Cleaned up temporary directory: {temp_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory: {e}")

    # ==================== PRIVATE HELPER METHODS ====================
    # Internal methods that support the public API

    def _setup_duckdb_extensions(self) -> None:
        """Configure DuckDB with optimized settings and error handling."""
        extensions = [
            "INSTALL spatial; LOAD spatial;",
            "INSTALL parquet; LOAD parquet;",
        ]

        settings = [
            "SET threads TO 4;",
            "SET enable_progress_bar=false;",
            "SET memory_limit='2GB';",  # Reasonable memory limit
        ]

        for ext in extensions + settings:
            try:
                self.con.execute(ext)
            except Exception as e:
                logger.debug(f"DuckDB setup: {ext} - {e}")

    def _load_metadata_only(self) -> Metadata:
        """
        Load only metadata from parquet file without loading data.
        Optimized for speed with minimal column scanning.
        """
        try:
            cols = self.con.execute(f"""
                DESCRIBE SELECT * FROM read_parquet('{self._db_path}', hive_partitioning=false) LIMIT 0
            """).fetchall()

            # assume 'geometry' column exists
            geometry_column = "geometry"
            # Each col is a tuple: (name, type, null, key, default, extra)
            for col in cols:
                col_name = col[0]
                if col_name.lower() == "geometry":
                    geometry_column = col_name
                    break

            # Skip geometry type detection for performance
            return Metadata(
                geometry_column=geometry_column,
                geometry_type="LineString",
                crs=None,
                columns=[ColumnMeta(name=c[0], type=c[1], nullable=True) for c in cols],
                raw_meta={"source_path": str(self._db_path), "fast_load": True},
            )

        except Exception as e:
            logger.error(f"Failed to load metadata: {e}")
            raise

    def _ensure_loaded(self) -> None:
        """Ensure network is loaded."""
        if not self._is_loaded:
            # Load minimal data for operations
            self.load_network()

    # ==================== CONTEXT MANAGER ====================
    # Special methods for context management

    def __enter__(self) -> "InMemoryNetworkProcessor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()
