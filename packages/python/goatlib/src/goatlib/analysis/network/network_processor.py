import logging
import uuid
import time
from typing import Any, Dict, Tuple

from goatlib.analysis.core.base import AnalysisTool
from goatlib.io.utils import Metadata
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)

# from .super I have only: cleanup, init and import_input


class InMemoryNetworkProcessor(AnalysisTool):
    """
    High-performance in-memory network processor for routing.
    """

    def __init__(self, input_path: str) -> None:
        """Initializes the processor. Requires network parameters to be valid."""
        super().__init__(db_path=input_path)
        self._is_loaded = False
        self._network_table_name: str
        self._meta: Metadata

    def __enter__(self) -> "InMemoryNetworkProcessor":
        """Enters the context, loading the network and returning the processor instance."""
        # Don't load network yet - wait for user to call create_buffered_subset
        # This allows working with only a subset of the network for performance
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exits the context, automatically cleaning up all database resources."""
        super().cleanup()

    @property
    def network_table_name(self) -> str:
        """Get the name of the loaded network table."""
        self._ensure_loaded()
        return self._network_table_name

    @property
    def network_metadata(self) -> Metadata:
        """Get metadata about the loaded network."""
        self._ensure_loaded()
        return self._meta

    def _ensure_loaded(self) -> None:
        """Ensure the network is loaded before performing operations."""
        if not self._is_loaded:
            raise RuntimeError("Network not loaded. Call load_network() first.")

    def get_network_stats(self, table_name: str = None) -> Dict[str, Any]:
        """Get basic statistics about the network.

        Args:
            table_name: Optional table name to get stats for. If None, uses the main network table.
        """
        self._ensure_loaded()
        target_table = table_name if table_name else self._network_table_name
        result = self.con.execute(f"""
            SELECT
                COUNT(*) as edge_count,
                SUM(length_m) as total_length_m,
                AVG(length_m) as avg_length_m,
                MIN(length_m) as min_length_m,
                MAX(length_m) as max_length_m
            FROM {target_table}
        """).fetchone()

        return {
            "edge_count": result[0],
            "total_length_m": float(result[1]) if result[1] else 0,
            "avg_length_m": float(result[2]) if result[2] else 0,
            "min_length_m": float(result[3]) if result[3] else 0,
            "max_length_m": float(result[4]) if result[4] else 0,
        }

    def get_available_tables(self) -> list[str]:
        result = self.con.execute("SHOW TABLES").fetchall()
        return [row[0] for row in result]

    def apply_sql_query(
        self, sql_query: str, result_table: str = "query_result"
    ) -> str:
        """Applies SQL and returns a NEW table, without destroying the input."""
        self._ensure_loaded()
        result_table = f"{result_table}_{uuid.uuid4().hex[:8]}"
        try:
            # WARNING: This does not sanitize input SQL - use with caution in production
            self.con.execute(f"CREATE TABLE {result_table} AS {sql_query}")
            logger.info(f"Created result table: {result_table}")
            return result_table
        except Exception as e:
            logger.error(f"Failed to execute SQL query: {e}")
            raise

    def load_network(
        self,
        center: Coordinates = None,
        buffer_radius: float = None,
        travel_time_minutes: float = 90.0,
        speed_kmh: float = 5.0,
    ) -> str:
        """
        Cut network for routing operations with configurable parameters.

        Returns:
            Tuple of (table_name, buffer_distance_meters)
        """
        self._meta, self._network_table_name = super().import_input(self._db_path)
        logger.info(f"Network loaded into table: {self._network_table_name}")

        # Validate required columns exist
        # TODO check if this is made in import_input
        if not self._meta.geometry_column:
            raise ValueError("Network file must have a geometry column")

        if center is None:
            logger.info("No center provided, loading full network")
            self._is_loaded = True
            return self._network_table_name
        # Calculate buffer distance
        if buffer_radius is not None:
            buffer_distance = buffer_radius
        else:
            # Convert travel time to distance
            # speed_kmh * 1000 / 60 = meters per minute
            buffer_distance = travel_time_minutes * (speed_kmh * 1000 / 60)

        # Convert meters to degrees (approximate at the given latitude)
        # DuckDB spatial doesn't have ST_DWithin_Sphere, so we convert to degrees
        import math

        lat_rad = math.radians(center.lat)
        meters_per_degree_lat = 111320  # roughly constant
        meters_per_degree_lon = 111320 * math.cos(lat_rad)
        # Use average for simplicity
        buffer_degrees = buffer_distance / (
            (meters_per_degree_lat + meters_per_degree_lon) / 2
        )

        logger.info(
            f"Creating buffered network subset with buffer distance: {buffer_distance:.2f} meters "
            f"(~{buffer_degrees:.6f} degrees)"
        )

        # Create buffered network
        subset_table_name = f"routing_network_{uuid.uuid4().hex[:8]}"

        subset_query = f"""
            CREATE TABLE {subset_table_name} AS
            SELECT t.*
            FROM {self._network_table_name} t
            WHERE ST_Intersects(
                t.{self._meta.geometry_column},
                ST_Buffer(
                    ST_Point({center.lon}, {center.lat}),
                    {buffer_degrees}
                )
            )
            """

        import time

        start = time.time()
        self.con.execute(subset_query)
        elapsed = time.time() - start

        logger.info(f"Network subset created in {elapsed:.3f} seconds")
        self._is_loaded = True

        return subset_table_name

    # Network Analysis Methods
    def split_edge_at_point_with_subset(
        self,
        point: Coordinates,
        network_buffer_radius: float = 500.0,
        max_search_radius: float = 20.0,
    ) -> tuple[str, Metadata]:
        """
        Loads a network subset around a point and splits the nearest edge.

        This is memory-efficient as it only loads the network within the buffer radius.

        Args:
            point: Coordinates where to split
            network_buffer_radius: Radius in meters to load network around the point (default: 500m)
            max_search_radius: Maximum search radius in meters for finding closest edge (default: 200m)
            include_stats: Whether to include edge count statistics

        Returns:
            Tuple of (table_name, metadata) with split operation details
        """
        # Load only the network subset around the point
        logger.info(
            f"Loading network subset with {network_buffer_radius}m radius around point"
        )
        subset_table = self.load_network(
            center=point, buffer_radius=network_buffer_radius
        )

        # Now split on this subset
        return self.split_edge_at_point(
            point=point,
            source_table=subset_table,
            max_search_radius=max_search_radius,
        )

    def split_edge_at_point(
        self,
        point: Coordinates,
        source_table: str = None,
        max_search_radius: float = 100.0,
    ) -> tuple[str, Metadata]:
        """
        Finds the closest edge to a point, splits it, and creates a new network table.

        Args:
            latitude: Latitude of the split point
            longitude: Longitude of the split point
            base_table: Source table name (defaults to main network table)
            max_search_radius: Maximum search radius in meters
            include_stats: Whether to include edge count statistics (default: True)

        Returns:
            Tuple of (table_name, metadata) with split operation details in raw_meta
        """
        split_table_name = f"split_network_{uuid.uuid4().hex[:8]}"
        new_node_id = f"split_node_{uuid.uuid4().hex[:8]}"
        point_geom = f"ST_Point({point.lon}, {point.lat})"
        geom_col = self._meta.geometry_column

        # First, find the closest edge using bbox optimization
        info_query = f"""
        WITH search_bbox AS (
            SELECT ST_Envelope(
                ST_Buffer({point_geom}, {max_search_radius})
            ) AS bbox
        ), candidate_edges AS (
            SELECT *
            FROM {source_table}, search_bbox
            WHERE ST_Intersects({geom_col}, search_bbox.bbox)
        ), closest_edge AS (
            SELECT
                edge_id,
                ST_Distance({geom_col}, {point_geom}) AS distance,
                ST_LineLocatePoint({geom_col}, {point_geom}) AS split_fraction,
                {geom_col}
            FROM candidate_edges
            ORDER BY distance ASC
            LIMIT 1
        ), split_point_calc AS (
            SELECT
                edge_id,
                split_fraction,
                distance,
                ST_X(ST_LineInterpolatePoint({geom_col}, split_fraction)) AS split_lon,
                ST_Y(ST_LineInterpolatePoint({geom_col}, split_fraction)) AS split_lat
            FROM closest_edge
        )
        SELECT
            edge_id,
            split_fraction,
            split_lon,
            split_lat,
            distance
        FROM split_point_calc;
        """
        find_start = time.time()
        info_res = self.con.execute(info_query).fetchone()

        # Check if any edge was found
        if not info_res or info_res[0] is None:
            raise ValueError(
                "No edges found. Try increasing max_search_radius or check if the point is near the network."
            )
        find_elapsed = time.time() - find_start
        logger.info(f"Found closest edge in {find_elapsed:.3f}s")

        # Now create the split table using the found edge
        original_edge_id, split_fraction, split_lon, split_lat, distance = info_res

        split_query = f"""
        CREATE TABLE {split_table_name} AS
        WITH target_edge AS (
            -- Select the specific edge we found
            SELECT * FROM {source_table}
            WHERE edge_id = '{original_edge_id}'
        ),
        new_split_parts AS (
            -- Create two new edge segments from the original edge at the split point
            -- Part A: from original source to new split node
            SELECT
                edge_id || '_part_a' as edge_id,
                source,
                '{new_node_id}' as target,
                length_m * {split_fraction} AS length_m,
                cost * {split_fraction} AS cost,
                ST_LineSubstring({geom_col}, 0.0, {split_fraction}) as {geom_col}
            FROM target_edge

            UNION ALL

            -- Part B: from new split node to original target
            SELECT
                edge_id || '_part_b' as edge_id,
                '{new_node_id}' as source,
                target,
                length_m * (1.0 - {split_fraction}) AS length_m,
                cost * (1.0 - {split_fraction}) AS cost,
                ST_LineSubstring({geom_col}, {split_fraction}, 1.0) as {geom_col}
            FROM target_edge
        )
        -- Combine all unchanged edges with the new split edge parts
        SELECT * FROM {source_table}
        WHERE edge_id <> '{original_edge_id}'
        UNION ALL
        SELECT * FROM new_split_parts;
        """

        split_start = time.time()
        self.con.execute(split_query)
        split_elapsed = time.time() - split_start
        logger.info(f"Created split table in {split_elapsed:.3f}s")

        logger.info(
            f"Original edge '{original_edge_id}' split at fraction {split_fraction:.6f} "
            f"({distance:.2f}m from point) into new node '{new_node_id}'"
        )
        # Create metadata for the split table (copy from original)
        split_meta = Metadata(geometry_column=self._meta.geometry_column, raw_meta={})

        # Add split operation details to metadata
        split_operation_info = {
            "operation": "edge_split",
            "method": "bbox_optimization",
            "artificial_node_id": new_node_id,
            "original_edge_split": original_edge_id,
            "split_fraction": split_fraction,
            "distance_to_edge": distance,
            "max_search_radius": max_search_radius,
            "new_node_coords": {
                "lon": split_lon,
                "lat": split_lat,
            },
        }

        split_meta.raw_meta["split_operation"] = split_operation_info

        # Warning for edge cases
        if not (1e-9 < split_fraction < 1.0 - 1e-9):
            logger.warning(
                f"Split point is at or very near an existing node (fraction={split_fraction:.6f}). "
                "The original edge was effectively replaced, not split into two new segments."
            )

        return split_table_name, split_meta

    # def interpolate_long_edges(
    #     self,
    #     max_edge_length: float,
    #     base_table: str = None,
    #     interpolation_distance: float = None,
    # ) -> tuple[str, Metadata]:
    #     """
    #     Interpolate nodes along edges that are longer than the specified threshold.
    #     Creates actual intermediate nodes with coordinates and splits edges accordingly.

    #     Args:
    #         max_edge_length: Maximum allowed edge length in meters
    #         base_table: Table to process (defaults to main network table)
    #         interpolation_distance: Distance between interpolated points (defaults to max_edge_length/2)

    #     Returns:
    #         Tuple of (table_name, metadata) where metadata contains table schema
    #         and interpolation details in raw_meta
    #     """
    #     import time

    #     start_time = time.time()
    #     self._ensure_loaded()
    #     source_table = base_table or self.network_table_name
    #     interpolated_table = self._generate_table_name("interpolated_network")

    #     # Default interpolation distance
    #     if interpolation_distance is None:
    #         interpolation_distance = max_edge_length / 2

    #     # Use metadata geometry column for dynamic column handling
    #     geom_column = self.meta.geometry_column

    #     # Combined query: create table and get statistics in one go
    #     interpolation_query = f"""
    #     CREATE TABLE {interpolated_table} AS
    #     WITH original_stats AS (
    #         SELECT
    #             COUNT(*) as original_edges,
    #             COUNT(*) FILTER (WHERE length_m > {max_edge_length}) as long_edges_count
    #         FROM {source_table}
    #     ),
    #     long_edges AS (
    #         -- Identify edges that need interpolation and calculate segments needed
    #         SELECT *,
    #                CAST(CEIL(length_m / {interpolation_distance}) AS INTEGER) as num_segments
    #         FROM {source_table}
    #         WHERE length_m > {max_edge_length}
    #     ),
    #     interpolated_segments AS (
    #         -- Generate new edges with intermediate nodes
    #         SELECT
    #             edge_id || '_seg_' || CAST(segment_id AS VARCHAR) as edge_id,
    #             CASE
    #                 WHEN segment_id = 1 THEN CAST(source AS VARCHAR)
    #                 ELSE 'interp_' || edge_id || '_' || CAST((segment_id - 1) AS VARCHAR)
    #             END as source,
    #             CASE
    #                 WHEN segment_id = num_segments THEN CAST(target AS VARCHAR)
    #                 ELSE 'interp_' || edge_id || '_' || CAST(segment_id AS VARCHAR)
    #             END as target,
    #             length_m / num_segments as length_m,
    #             cost / num_segments as cost,
    #             ST_LineSubstring(
    #                 {geom_column},
    #                 (segment_id - 1.0) / num_segments,
    #                 segment_id / num_segments
    #             ) as {geom_column}
    #         FROM long_edges
    #         CROSS JOIN generate_series(1, num_segments) as t(segment_id)
    #     )
    #     -- Combine short edges (unchanged) with interpolated segments
    #     SELECT edge_id, source, target, length_m, cost, {geom_column}
    #     FROM {source_table}
    #     WHERE length_m <= {max_edge_length}

    #     UNION ALL

    #     SELECT edge_id, source, target, length_m, cost, {geom_column}
    #     FROM interpolated_segments
    #     ORDER BY edge_id;
    #     """

    #     self.con.execute(interpolation_query)
    #     processing_time = time.time() - start_time

    #     # Get statistics in single optimized query
    #     stats_query = f"""
    #     WITH original_stats AS (
    #         SELECT
    #             COUNT(*) as original_edges,
    #             COUNT(*) FILTER (WHERE length_m > {max_edge_length}) as long_edges_count
    #         FROM {source_table}
    #     ),
    #     new_stats AS (
    #         SELECT COUNT(*) as new_edges FROM {interpolated_table}
    #     ),
    #     node_stats AS (
    #         SELECT
    #             COUNT(DISTINCT source) + COUNT(DISTINCT target) as total_nodes,
    #             COUNT(DISTINCT source) FILTER (WHERE source LIKE 'interp_%') +
    #             COUNT(DISTINCT target) FILTER (WHERE target LIKE 'interp_%') as new_nodes
    #         FROM {interpolated_table}
    #     )
    #     SELECT
    #         o.original_edges,
    #         o.long_edges_count,
    #         n.new_edges,
    #         ns.new_nodes,
    #         ns.total_nodes
    #     FROM original_stats o, new_stats n, node_stats ns;
    #     """

    #     stats_result = self.con.execute(stats_query).fetchone()

    #     # Create metadata for the interpolated table using fast path
    #     interpolated_meta = self._create_metadata_from_template(interpolated_table)

    #     # Embed interpolation details in raw_meta
    #     interpolated_meta.raw_meta = interpolated_meta.raw_meta or {}
    #     interpolated_meta.raw_meta["interpolation_operation"] = {
    #         "original_edge_count": stats_result[0],
    #         "long_edges_processed": stats_result[1],
    #         "final_edge_count": stats_result[2],
    #         "new_intermediate_nodes": stats_result[3],
    #         "total_nodes": stats_result[4],
    #         "edges_added": stats_result[2] - stats_result[0],
    #         "max_edge_length_threshold": max_edge_length,
    #         "interpolation_distance": interpolation_distance,
    #         "processing_time_seconds": processing_time,
    #     }

    #     return interpolated_table, interpolated_meta

    # File I/O Methods
    def save_network(
        self,
        table_name: str,
        output_path: str | None = None,
        format: str = "PARQUET",
    ) -> str:
        import tempfile

        def quote_ident(name: str) -> str:
            return '"' + name.replace('"', '""') + '"'

        format_upper = format.upper()
        table = quote_ident(table_name)

        if output_path is None:
            suffix = ".parquet" if format_upper == "PARQUET" else ".gpkg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                output_path = tmp.name

        if format_upper == "PARQUET":
            self.con.execute(
                f"""
                COPY {table} TO '{output_path}'
                (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    ROW_GROUP_SIZE 1000000
                )
                """
            )
        else:
            self.con.execute(
                f"""
                COPY {table} TO '{output_path}'
                (
                    FORMAT GDAL,
                    DRIVER '{format_upper}'
                )
                """
            )

        return output_path
