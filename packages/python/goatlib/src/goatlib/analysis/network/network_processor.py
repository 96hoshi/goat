import logging
import uuid
from typing import Any, Dict

from goatlib.analysis.core.base import AnalysisTool
from goatlib.io.utils import Metadata

logger = logging.getLogger(__name__)

# from .super I have only: cleanup, init and import_input
# TODO make it dependent by a coordinate pair to buffer the network area around it


class InMemoryNetworkProcessor(AnalysisTool):
    """
    High-performance in-memory network processor for routing.
    """

    def __init__(self, input_path: str) -> None:
        """Initializes the processor. Requires network parameters to be valid."""
        super().__init__(db_path=input_path)
        self.input_path = input_path
        self.network_table_name = None
        self._is_loaded = False

    def __enter__(self) -> "InMemoryNetworkProcessor":
        """Enters the context, loading the network and returning the processor instance."""
        # Don't load network yet - wait for user to call create_buffered_subset
        # This allows working with only a subset of the network for performance
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exits the context, automatically cleaning up all database resources."""
        super().cleanup()

    # Public API Methods
    def get_network_metadata(self) -> Metadata:
        """Get metadata about the loaded network using AnalysisTool metadata functionality."""
        self._ensure_loaded()
        return self.meta

    def get_network_stats(self, table_name: str = None) -> Dict[str, Any]:
        """Get basic statistics about the network."""
        self._ensure_loaded()
        target_table = table_name or self.network_table_name
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

    def create_buffered_subset(
        self,
        latitude: float,
        longitude: float,
        buffer_radius: float = 5000.0,
        base_table: str = None,
    ) -> str:
        """
        Create a subset of the network within a buffer around a point.
        This dramatically reduces memory and processing time for local operations.

        Use this method BEFORE performing expensive operations like splitting or
        interpolation to work only with relevant network edges.

        Args:
            latitude: Center point latitude
            longitude: Center point longitude
            buffer_radius: Buffer distance in meters (default: 5km)
            base_table: Source table (defaults to main network table)

        Returns:
            Name of the created subset table

        Example:
            >>> processor = InMemoryNetworkProcessor("network.parquet")
            >>> # Load network and create 3km subset around Munich center
            >>> subset = processor.create_buffered_subset(48.1351, 11.5820, 3000)
            >>> # Get metadata with statistics
            >>> meta = processor.get_subset_metadata(subset, 48.1351, 11.5820, 3000)
            >>> # Now work only on the subset (much faster!)
            >>> split, _ = processor.split_edge_at_point(48.135, 11.582, base_table=subset)
        """
        self._ensure_loaded()
        source_table = base_table or self.network_table_name
        subset_table_name = f"buffered_network_{uuid.uuid4().hex[:8]}"
        geom_col = self.meta.geometry_column

        # Create point and buffer using DuckDB spatial functions
        subset_query = f"""
        CREATE TABLE {subset_table_name} AS
        WITH buffer_geom AS (
            SELECT ST_Buffer(
                ST_Point({longitude}, {latitude}),
                {buffer_radius}
            ) AS buffer
        )
        SELECT t.*
        FROM {source_table} t, buffer_geom
        WHERE ST_Intersects(t.{geom_col}, buffer_geom.buffer)
        """

        self.con.execute(subset_query)

        # Get basic edge count for logging
        edge_count = self.con.execute(
            f"SELECT COUNT(*) FROM {subset_table_name}"
        ).fetchone()[0]
        original_count = self.con.execute(
            f"SELECT COUNT(*) FROM {source_table}"
        ).fetchone()[0]

        logger.info(
            f"Created buffered subset: {edge_count}/{original_count} edges "
            f"({edge_count/original_count*100:.1f}% of original) "
            f"within {buffer_radius}m of ({latitude}, {longitude})"
        )

        return subset_table_name

    def get_subset_metadata(
        self,
        subset_table: str,
        latitude: float,
        longitude: float,
        buffer_radius: float,
        source_table: str = None,
    ) -> Metadata:
        """
        Get metadata for a buffered subset table with detailed statistics.

        Args:
            subset_table: Name of the subset table
            latitude: Center point latitude used for buffer
            longitude: Center point longitude used for buffer
            buffer_radius: Buffer radius in meters
            source_table: Original source table (defaults to main network table)

        Returns:
            Metadata object with buffer operation details in raw_meta
        """
        self._ensure_loaded()
        source_table = source_table or self.network_table_name
        geom_col = self.meta.geometry_column

        # Create metadata for subset table
        subset_meta = self._create_metadata_from_template(subset_table)

        # Get statistics about the subset
        stats_query = f"""
        SELECT
            COUNT(*) as subset_edges,
            (SELECT COUNT(*) FROM {source_table}) as original_edges,
            SUM(length_m) as total_length_m,
            MIN(ST_Distance({geom_col}, ST_Point({longitude}, {latitude}))) as min_distance,
            MAX(ST_Distance({geom_col}, ST_Point({longitude}, {latitude}))) as max_distance
        FROM {subset_table}
        """

        stats_result = self.con.execute(stats_query).fetchone()

        # Add buffer operation details to metadata
        subset_meta.raw_meta = subset_meta.raw_meta or {}
        subset_meta.raw_meta["buffer_operation"] = {
            "operation": "spatial_buffer",
            "center_point": {"lat": latitude, "lon": longitude},
            "buffer_radius_m": buffer_radius,
            "original_edge_count": stats_result[1],
            "subset_edge_count": stats_result[0],
            "reduction_ratio": stats_result[0] / stats_result[1]
            if stats_result[1] > 0
            else 0,
            "total_length_m": float(stats_result[2]) if stats_result[2] else 0,
            "min_distance_m": float(stats_result[3]) if stats_result[3] else 0,
            "max_distance_m": float(stats_result[4]) if stats_result[4] else 0,
        }

        return subset_meta

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

    # Network Analysis Methods
    def split_edge_at_point(
        self,
        latitude: float,
        longitude: float,
        base_table: str = None,
        max_search_radius: float = 200.0,
        include_stats: bool = True,
    ) -> tuple[str, Metadata]:
        """
        Finds the closest edge to a point, splits it, and creates a new network table.

        Uses bbox optimization with spatial indexing for efficient edge searching.

        Args:
            latitude: Latitude of the split point
            longitude: Longitude of the split point
            base_table: Source table name (defaults to main network table)
            max_search_radius: Maximum search radius in meters
            include_stats: Whether to include edge count statistics (default: True)

        Returns:
            Tuple of (table_name, metadata) with split operation details in raw_meta
        """
        self._ensure_loaded()
        source_table = base_table or self.network_table_name
        split_table_name = f"split_network_{uuid.uuid4().hex[:8]}"
        new_node_id = f"split_node_{uuid.uuid4().hex[:8]}"
        point_geom = f"ST_Point({longitude}, {latitude})"
        geom_col = self.meta.geometry_column

        # Calculate rough bbox around the point (in degrees, approximate)
        bbox_size = max_search_radius / 111000.0  # rough meters to degrees conversion

        info_query = f"""
        SELECT
            edge_id,
            ST_LineLocatePoint({geom_col}, {point_geom}) as split_fraction,
            ST_X(ST_LineInterpolatePoint({geom_col}, ST_LineLocatePoint({geom_col}, {point_geom}))) as split_lon,
            ST_Y(ST_LineInterpolatePoint({geom_col}, ST_LineLocatePoint({geom_col}, {point_geom}))) as split_lat,
            ST_Distance({geom_col}, {point_geom}) as distance
        FROM {source_table}
        WHERE ST_Intersects({geom_col}, ST_MakeEnvelope(
            {longitude - bbox_size}, {latitude - bbox_size},
            {longitude + bbox_size}, {latitude + bbox_size}
        ))
        AND ST_Distance({geom_col}, {point_geom}) <= {max_search_radius}
        ORDER BY ST_Distance({geom_col}, {point_geom}) ASC
        LIMIT 1
        """

        info_res = self.con.execute(info_query).fetchone()

        # Check if any edge was found
        if not info_res or info_res[0] is None:
            raise ValueError(
                f"No edges found within {max_search_radius}m of point ({latitude}, {longitude}). "
                f"Try increasing max_search_radius or check if the point is near the network."
            )

        # Extract info for later use
        original_edge_id, split_fraction, split_lon, split_lat, distance = info_res

        # Now create the split table using the found edge
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
        self.con.execute(split_query)

        # Create metadata for the split table using fast path (same schema as original)
        split_meta = self._create_metadata_from_template(split_table_name)

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

        # Optionally include statistics (can be expensive for large networks)
        if include_stats:
            split_operation_info.update(
                {
                    "original_edge_count": self.get_network_stats()["edge_count"],
                    "split_edge_count": self.get_network_stats(split_table_name)[
                        "edge_count"
                    ],
                }
            )

        split_meta.raw_meta["split_operation"] = split_operation_info

        # Warning for edge cases
        if not (1e-9 < split_fraction < 1.0 - 1e-9):
            logger.warning(
                f"Split point is at or very near an existing node (fraction={split_fraction:.6f}). "
                "The original edge was effectively replaced, not split into two new segments."
            )

        return split_table_name, split_meta

    def interpolate_long_edges(
        self,
        max_edge_length: float,
        base_table: str = None,
        interpolation_distance: float = None,
    ) -> tuple[str, Metadata]:
        """
        Interpolate nodes along edges that are longer than the specified threshold.
        Creates actual intermediate nodes with coordinates and splits edges accordingly.

        Args:
            max_edge_length: Maximum allowed edge length in meters
            base_table: Table to process (defaults to main network table)
            interpolation_distance: Distance between interpolated points (defaults to max_edge_length/2)

        Returns:
            Tuple of (table_name, metadata) where metadata contains table schema
            and interpolation details in raw_meta
        """
        import time

        start_time = time.time()
        self._ensure_loaded()
        source_table = base_table or self.network_table_name
        interpolated_table = self._generate_table_name("interpolated_network")

        # Default interpolation distance
        if interpolation_distance is None:
            interpolation_distance = max_edge_length / 2

        # Use metadata geometry column for dynamic column handling
        geom_column = self.meta.geometry_column

        # Combined query: create table and get statistics in one go
        interpolation_query = f"""
        CREATE TABLE {interpolated_table} AS
        WITH original_stats AS (
            SELECT
                COUNT(*) as original_edges,
                COUNT(*) FILTER (WHERE length_m > {max_edge_length}) as long_edges_count
            FROM {source_table}
        ),
        long_edges AS (
            -- Identify edges that need interpolation and calculate segments needed
            SELECT *,
                   CAST(CEIL(length_m / {interpolation_distance}) AS INTEGER) as num_segments
            FROM {source_table}
            WHERE length_m > {max_edge_length}
        ),
        interpolated_segments AS (
            -- Generate new edges with intermediate nodes
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
                cost / num_segments as cost,
                ST_LineSubstring(
                    {geom_column},
                    (segment_id - 1.0) / num_segments,
                    segment_id / num_segments
                ) as {geom_column}
            FROM long_edges
            CROSS JOIN generate_series(1, num_segments) as t(segment_id)
        )
        -- Combine short edges (unchanged) with interpolated segments
        SELECT edge_id, source, target, length_m, cost, {geom_column}
        FROM {source_table}
        WHERE length_m <= {max_edge_length}

        UNION ALL

        SELECT edge_id, source, target, length_m, cost, {geom_column}
        FROM interpolated_segments
        ORDER BY edge_id;
        """

        self.con.execute(interpolation_query)
        processing_time = time.time() - start_time

        # Get statistics in single optimized query
        stats_query = f"""
        WITH original_stats AS (
            SELECT
                COUNT(*) as original_edges,
                COUNT(*) FILTER (WHERE length_m > {max_edge_length}) as long_edges_count
            FROM {source_table}
        ),
        new_stats AS (
            SELECT COUNT(*) as new_edges FROM {interpolated_table}
        ),
        node_stats AS (
            SELECT
                COUNT(DISTINCT source) + COUNT(DISTINCT target) as total_nodes,
                COUNT(DISTINCT source) FILTER (WHERE source LIKE 'interp_%') +
                COUNT(DISTINCT target) FILTER (WHERE target LIKE 'interp_%') as new_nodes
            FROM {interpolated_table}
        )
        SELECT
            o.original_edges,
            o.long_edges_count,
            n.new_edges,
            ns.new_nodes,
            ns.total_nodes
        FROM original_stats o, new_stats n, node_stats ns;
        """

        stats_result = self.con.execute(stats_query).fetchone()

        # Create metadata for the interpolated table using fast path
        interpolated_meta = self._create_metadata_from_template(interpolated_table)

        # Embed interpolation details in raw_meta
        interpolated_meta.raw_meta = interpolated_meta.raw_meta or {}
        interpolated_meta.raw_meta["interpolation_operation"] = {
            "original_edge_count": stats_result[0],
            "long_edges_processed": stats_result[1],
            "final_edge_count": stats_result[2],
            "new_intermediate_nodes": stats_result[3],
            "total_nodes": stats_result[4],
            "edges_added": stats_result[2] - stats_result[0],
            "max_edge_length_threshold": max_edge_length,
            "interpolation_distance": interpolation_distance,
            "processing_time_seconds": processing_time,
        }

        return interpolated_table, interpolated_meta

    # File I/O Methods
    def save_table(
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

    # Private Helper Methods
    def _ensure_loaded(self) -> None:
        if not self._is_loaded:
            self._load_network()

    def _load_network(self) -> None:
        """Load the network file using the parent class import functionality."""
        if self._is_loaded:
            return

        # Import using the parent class method which handles metadata correctly
        self.meta, self.network_table_name = super().import_input(self.input_path)

        # Network loaded - use create_buffered_subset() to work with a subset for performance
        self._is_loaded = True

        # Validate required columns exist
        required_columns = {"edge_id", "source", "target", "geometry"}

        # Get actual column names from metadata
        actual_columns = {col.name for col in self.meta.columns}

        missing_columns = required_columns - actual_columns
        if missing_columns:
            raise ValueError(
                f"Network file missing required columns: {missing_columns}. "
                f"Available columns: {actual_columns}"
            )

        # Validate geometry column exists
        if not self.meta.geometry_column:
            raise ValueError("Network file must have a geometry column")
