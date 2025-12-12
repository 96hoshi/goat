import logging
import math
import time
import uuid
from typing import Any, Dict, Set

from goatlib.analysis.core.base import AnalysisTool
from goatlib.io.utils import Metadata
from goatlib.routing.schemas.base import Coordinates

SPLIT_EPSILON = 1e-6  # Configurable threshold

logger = logging.getLogger(__name__)

# from .super I have only: cleanup, init and import_input


class InMemoryNetworkProcessor(AnalysisTool):
    """
    In-memory network processor for routing.
    """

    def __init__(self, input_path: str) -> None:
        super().__init__(db_path=input_path)
        self._is_loaded = False
        self._network_table_name: str
        self._meta: Metadata
        self._created_tables: Set[str] = set()  # Track tables we create
        self._original_tables: Set[str] = set()  # Tables that existed at init

    def __enter__(self) -> "InMemoryNetworkProcessor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        super().cleanup()

    @property
    def network_table_name(self) -> str:
        self._ensure_loaded()
        return self._network_table_name

    @property
    def network_metadata(self) -> Metadata:
        self._ensure_loaded()
        return self._meta

    def _ensure_loaded(self) -> None:
        """Ensure the network is loaded before performing operations."""
        if not self._is_loaded:
            raise RuntimeError("Network not loaded. Call load_network() first.")

    def _get_all_tables_safe(self) -> list[str]:
        """Safely get all table names."""
        try:
            result = self.con.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'main'
                ORDER BY table_name
            """).fetchall()
            return [row[0] for row in result] if result else []
        except:
            return []

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

    # def _get_available_tables(self) -> list[str]:
    #     """Returns a list of available table names in the DuckDB database. used for testing purposes."""
    #     result = self.con.execute("SHOW TABLES").fetchall()
    #     return [row[0] for row in result]

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

        lat_rad = math.radians(center.lat)
        meters_per_degree_lat = 111320  # roughly constant
        meters_per_degree_lon = 111320 * math.cos(lat_rad)
        buffer_degrees = buffer_distance / (
            (meters_per_degree_lat + meters_per_degree_lon) / 2
        )

        logger.info(
            f"Creating buffered network subset with buffer distance: {buffer_distance:.2f} m"
        )

        # Create buffered network
        subset_table_name = f"routing_network_{uuid.uuid4().hex[:8]}"
        # circular buffer around point
        subset_query = f"""
            CREATE TABLE {subset_table_name} AS
            SELECT t.*
            FROM {self._network_table_name} t
            WHERE ST_DWithin(
                t.{self._meta.geometry_column},
                ST_MakePoint({center.lon}, {center.lat}), {buffer_degrees}
            )
            """

        start = time.time()
        self.con.execute(subset_query)
        elapsed = time.time() - start

        logger.info(f"Network subset created in {round(elapsed * 1000, 1)} ms")
        self._is_loaded = True

        return subset_table_name

    # Network Analysis Methods
    def split_edge_at_point_with_subset(
        self,
        point: Coordinates,
        network_buffer_radius: float = 1000.0,
        max_search_radius_m: float = 20.0,
    ) -> tuple[str, Metadata]:
        """
        Loads a network subset around a point and splits the nearest edge.

        This is memory-efficient as it only loads the network within the buffer radius.

        Args:
            point: Coordinates where to split
            network_buffer_radius: Radius in meters to load network around the point
            max_search_radius_m: Maximum search radius in meters for finding closest edge
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
            max_search_radius_m=max_search_radius_m,
        )

    def split_edge_at_point(
        self,
        point: Coordinates,
        source_table: str = None,
        max_search_radius_m: float = 100.0,
    ) -> tuple[str, Metadata]:
        """
        Finds the closest edge to a point, splits it, and creates a new network table.

        Args:
            point: Coordinates where to start the route
            source_table: Source table name (defaults to main network table)
            max_search_radius_m: Maximum search radius in meters

        Returns:
            Tuple of (table_name, metadata) with split operation details in raw_meta
        """
        # Generate unique IDs
        new_node_id = f"split_node_{uuid.uuid4().hex[:8]}"
        split_table_name = f"split_network_{uuid.uuid4().hex[:8]}"

        # Set source table if not provided
        if source_table is None:
            source_table = self._network_table_name

        # Prepare geometry references
        point_geom = f"ST_Point({point.lon}, {point.lat})"
        geom_col = self._meta.geometry_column

        # Convert meters to degrees for spatial search
        # Approximation: 1 degree ≈ 111.32 km at equator
        search_radius_deg = max_search_radius_m / 111320.0

        # QUERY 1: Find closest edge with all needed info
        find_query = f"""
        WITH closest AS (
            SELECT
                edge_id,
                source,
                target,
                length_m,
                cost,
                {geom_col},
                ST_Distance({geom_col}, {point_geom}) as dist_deg,
                ST_LineLocatePoint({geom_col}, {point_geom}) as frac
            FROM {source_table}
            WHERE ST_DWithin({geom_col}, {point_geom}, {search_radius_deg})
            ORDER BY dist_deg
            LIMIT 1
        )
        SELECT
            *,
            CASE WHEN frac BETWEEN 0.001 AND 0.999 THEN 1 ELSE 0 END as valid_split,
            ST_X(ST_LineInterpolatePoint({geom_col}, frac)) as split_lon,
            ST_Y(ST_LineInterpolatePoint({geom_col}, frac)) as split_lat
        FROM closest;
        """

        # Execute find query
        find_start = time.time()
        result = self.con.execute(find_query).fetchone()
        find_elapsed = time.time() - find_start

        if not result:
            raise ValueError(
                f"No edge found within {max_search_radius_m}m of point ({point.lat}, {point.lon})"
            )

        if result[-3] == 0:  # valid_split column
            raise ValueError(
                f"Edge found but split fraction {result[7]:.6f} is too close to endpoint"
            )

        # Extract values from result
        original_edge_id = result[0]
        source_node = result[1]
        target_node = result[2]
        length_m = result[3]
        cost_val = result[4]
        dist_deg = result[6]
        split_fraction = result[7]
        split_lon = result[9]
        split_lat = result[10]

        # Convert distance to meters
        distance_m = dist_deg * 111320.0

        # QUERY 2: Split the edge efficiently
        split_query = f"""
        CREATE TABLE {split_table_name} AS

        -- All edges except the one being split
        SELECT *
        FROM {source_table}
        WHERE edge_id != '{original_edge_id}'

        UNION ALL

        -- Split into two parts: Source → New Node
        SELECT
            '{original_edge_id}_A' as edge_id,
            source,
            '{new_node_id}' as target,
            ROUND(length_m * {split_fraction}, 3) as length_m,
            ROUND(cost * {split_fraction}, 3) as cost,
            ST_LineSubstring({geom_col}, 0.0, {split_fraction}) as {geom_col}
        FROM {source_table}
        WHERE edge_id = '{original_edge_id}'

        UNION ALL

        -- Split into two parts: New Node → Target
        SELECT
            '{original_edge_id}_B' as edge_id,
            '{new_node_id}' as source,
            target,
            ROUND(length_m * (1.0 - {split_fraction}), 3) as length_m,
            ROUND(cost * (1.0 - {split_fraction}), 3) as cost,
            ST_LineSubstring({geom_col}, {split_fraction}, 1.0) as {geom_col}
        FROM {source_table}
        WHERE edge_id = '{original_edge_id}';
        """

        # Execute split query
        split_start = time.time()
        self.con.execute(split_query)
        split_elapsed = time.time() - split_start

        logger.info(
            f"Edge '{original_edge_id}' split at {split_fraction:.3%} "
            f"(~{distance_m:.1f}m from request) → Node '{new_node_id}' "
            f"[Table: {split_table_name}] in {find_elapsed + split_elapsed:.3f}s"
        )

        # 2. METADATA
        split_meta = Metadata(
            geometry_column=self._meta.geometry_column,
            raw_meta={
                "source_table": source_table,
                "split_operation": {
                    "artificial_node_id": new_node_id,
                    "original_edge": original_edge_id,
                    "split_position": {
                        "fraction": split_fraction,
                        "request_point": {"lat": point.lat, "lon": point.lon},
                        "actual_point": {"lat": split_lat, "lon": split_lon},
                    },
                    "edge_properties": {
                        "original_length_m": length_m,
                        "part_a_length_m": round(length_m * split_fraction, 3),
                        "part_b_length_m": round(length_m * (1 - split_fraction), 3),
                        "original_cost": cost_val,
                        "source_node": source_node,
                        "target_node": target_node,
                    },
                    "search_params": {
                        "max_radius_m": max_search_radius_m,
                        "search_radius_deg": search_radius_deg,
                        "actual_distance_m": round(distance_m, 3),
                    },
                    "performance": {
                        "find_query_ms": round(find_elapsed * 1000, 1),
                        "split_query_ms": round(split_elapsed * 1000, 1),
                        "total_ms": round((find_elapsed + split_elapsed) * 1000, 1),
                    },
                },
            },
        )

        # 3. VALIDATION WARNINGS
        if split_fraction < SPLIT_EPSILON:
            logger.warning(
                f"Split at start of edge (fraction={split_fraction:.6f}). "
                f"Consider using existing node '{source_node}' instead of '{new_node_id}'."
            )
        elif split_fraction > 1.0 - SPLIT_EPSILON:
            logger.warning(
                f"Split at end of edge (fraction={split_fraction:.6f}). "
                f"Consider using existing node '{target_node}' instead of '{new_node_id}'."
            )

        return split_table_name, split_meta

    def interpolate_long_edges(
        self,
        max_edge_length: float,
        base_table: str = None,
        interpolation_distance: float = None,
        include_stats: bool = False,
    ) -> tuple[str, Metadata]:
        """
        Main function - creates interpolated table.
        Stats are optional for performance.
        """
        source_table = base_table or self.network_table_name
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
                cost / num_segments as cost,
                ST_LineSubstring(
                    {self._meta.geometry_column},
                    (segment_id - 1.0) / num_segments,
                    segment_id / num_segments
                ) as {self._meta.geometry_column}
            FROM long_edges
            CROSS JOIN generate_series(1, num_segments) as t(segment_id)
        )
        SELECT edge_id, source, target, length_m, cost, {self._meta.geometry_column}
        FROM {source_table}
        WHERE length_m <= {max_edge_length}
        UNION ALL
        SELECT edge_id, source, target, length_m, cost, {self._meta.geometry_column}
        FROM interpolated_segments
        ORDER BY edge_id;
        """
        start_time = time.time()
        self.con.execute(query)
        processing_time = time.time() - start_time

        logger.info(
            f"MAIN Interpolated network created: {interpolated_table} "
            f"in {processing_time:.3f}s"
        )

        # Create metadata
        meta = Metadata(
            geometry_column=self._meta.geometry_column,
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
        if include_stats:
            stats = self._get_interpolation_stats(table_name=interpolated_table)
            meta.raw_meta["interpolation_operation"].update(stats)

        return interpolated_table, meta

    def _get_interpolation_stats(
        self,
        table_name: str,
    ) -> Dict[str, Any]:
        """Get statistics about the interpolation operation."""
        stats_query = f"""
        WITH stats AS (
            SELECT
                COUNT(*) as total_edges,
                COUNT(*) FILTER (WHERE edge_id LIKE '%_seg_%') as segments_created,
                MAX(length_m) as max_segment_length,
                SUM(length_m) as total_length
            FROM {table_name}
        ),
        node_stats AS (
            SELECT
                COUNT(DISTINCT source) + COUNT(DISTINCT target) as total_nodes,
                COUNT(DISTINCT source) FILTER (WHERE source LIKE 'interp_%') +
                COUNT(DISTINCT target) FILTER (WHERE target LIKE 'interp_%') as new_nodes
            FROM {table_name}
        )
        SELECT
            s.total_edges,
            s.segments_created,
            s.max_segment_length,
            s.total_length,
            ns.new_nodes,
            ns.total_nodes
        FROM stats s, node_stats ns;
        """

        stats_result = self.con.execute(stats_query).fetchone()

        return {
            "final_edge_count": stats_result[0],
            "segments_created": stats_result[1],
            "max_segment_length_m": round(stats_result[2], 2),
            "total_length_m": round(stats_result[3], 2),
            "new_intermediate_nodes": stats_result[4],
            "total_nodes": stats_result[5],
        }

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
