import logging
import math
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

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
        Optimized preparation using pre-loaded network data.

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

        # Spatial parameters for edge splitting
        search_radius_deg = 200.0 / 111320.0  # 200m search radius

        # Output paths
        if output_path is None:
            output_path = (
                f"{self._temp_dir}/routing_network_{uuid.uuid4().hex[:8]}.parquet"
            )

        # Generate unique IDs
        new_node_id = (
            abs(hash(f"split_{start_point.lat}_{start_point.lon}")) % 2147483647
        )

        # Step 2: Process the already-loaded network data for routing
        try:
            # Create routing-ready network with edge splitting
            self.con.execute(f"""
                CREATE TEMP TABLE temp_split_result AS
                WITH
                -- Find closest edge to split (working on pre-loaded data)
                point_ref AS (
                    SELECT ST_MakePoint({start_point.lon}, {start_point.lat})::GEOMETRY as search_point
                ),
                closest AS (
                    SELECT b.*,
                        ST_Distance(b.geometry::GEOMETRY, p.search_point) as dist,
                        ST_LineLocatePoint(b.geometry::GEOMETRY, p.search_point) as frac
                    FROM {subset_table} b, point_ref p
                    WHERE ST_DWithin(b.geometry::GEOMETRY, p.search_point, {search_radius_deg})
                    ORDER BY dist
                    LIMIT 1
                ),
                -- Generate split edges
                split_edges AS (
                    -- Edges not being split
                    SELECT 
                        edge_id,
                        source,
                        target,
                        length_m,
                        geometry
                    FROM {subset_table}
                    WHERE edge_id NOT IN (SELECT edge_id FROM closest)

                    UNION ALL

                    -- First part of split edge (if valid split)
                    SELECT 
                        edge_id || '_A' as edge_id,
                        source,
                        {new_node_id} as target,
                        ROUND(length_m * frac, 3) as length_m,
                        ST_LineSubstring(geometry::GEOMETRY, 0.0, frac) as geometry
                    FROM closest
                    WHERE frac BETWEEN 0.001 AND 0.999

                    UNION ALL

                    -- Second part of split edge (if valid split)
                    SELECT 
                        edge_id || '_B' as edge_id,
                        {new_node_id} as source,
                        target,
                        ROUND(length_m * (1.0 - frac), 3) as length_m,
                        ST_LineSubstring(geometry::GEOMETRY, frac, 1.0) as geometry
                    FROM closest
                    WHERE frac BETWEEN 0.001 AND 0.999
                )
                -- Final selection with renumbered edge IDs
                SELECT 
                    CAST(ROW_NUMBER() OVER (ORDER BY edge_id) AS INTEGER) as edge_id,
                    CAST(source AS INTEGER) as source,
                    CAST(target AS INTEGER) as target,
                    length_m,
                    geometry
                FROM split_edges
            """)

            # Step 3: Export to parquet with geometry converted to WKT for Rust lib
            self.con.execute(f"""
                COPY (SELECT
                    edge_id,
                    source,
                    target,
                    length_m,
                    ST_AsText(geometry) as geometry
                FROM temp_split_result)
                TO '{output_path}' (FORMAT PARQUET)
            """)

            # Step 4: Clean up
            self.con.execute("DROP TABLE IF EXISTS temp_split_result")

        except Exception as e:
            logger.error(f"Failed to prepare routing network: {e}")
            raise

        elapsed = time.time() - start_time
        logger.debug(
            f"Routing network ready in {elapsed:.3f}s, start node: {new_node_id}"
        )

        return output_path, new_node_id

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
