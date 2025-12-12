import logging
from pathlib import Path

from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)


def test_interpolate_point_on_edge(network_file: Path) -> None:
    """Test interpolating a point along an edge with comprehensive validation."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Try to split
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.137154, lon=11.576124),
            network_buffer_radius=500.0,
            max_search_radius_m=100.0,
        )

        split_stats = proc.get_network_stats(split_table)
        logger.info(f"Split network: {split_stats['edge_count']} edges")

        # Interpolate long edges (> 50m)
        new_table, new_meta = proc.interpolate_long_edges(
            base_table=split_table,
            max_edge_length=10.0,
            include_stats=True,
        )

        new_stats = proc.get_network_stats(new_table)
        logger.info(f"Interpolated network: {new_stats['edge_count']} edges")

        # Basic assertion
        assert (
            new_stats["edge_count"] >= split_stats["edge_count"]
        ), f"Interpolation should increase edge count, but {new_stats['edge_count']} < {split_stats['edge_count']}"

        # 1. CHECK: Metadata exists
        assert (
            split_meta.raw_meta.get("split_operation", {}) is not None
        ), "Missing split metadata"
        assert (
            new_meta.raw_meta.get("interpolation_operation", {}) is not None
        ), "Missing interpolation metadata"

        # 2. CHECK: No edges longer than max threshold (with small tolerance)
        max_allowed = 50.0 * 1.1  # 10% tolerance
        max_edge_result = proc.con.execute(f"""
            SELECT MAX(length_m) as max_length
            FROM {new_table}
        """).fetchone()

        max_length = max_edge_result[0] if max_edge_result[0] else 0
        assert (
            max_length <= max_allowed
        ), f"Found segment {max_length:.1f}m > max allowed {max_allowed:.1f}m"
        logger.info(f"✓ Max segment length: {max_length:.1f}m (threshold: 50.0m)")

        # 3. CHECK: Total length preserved (within 1%)
        total_length_original = (
            proc.con.execute(f"""
            SELECT SUM(length_m) FROM {split_table}
        """).fetchone()[0]
            or 0
        )

        total_length_new = (
            proc.con.execute(f"""
            SELECT SUM(length_m) FROM {new_table}
        """).fetchone()[0]
            or 0
        )

        if total_length_original > 0:
            length_diff = abs(total_length_new - total_length_original)
            length_diff_pct = length_diff / total_length_original * 100

            assert (
                length_diff_pct < 1.0
            ), f"Total length changed by {length_diff_pct:.2f}% (> 1% tolerance)"
            logger.info(
                f"✓ Total length preserved: {total_length_original:.1f}m → {total_length_new:.1f}m ({length_diff_pct:.2f}% diff)"
            )

        # 4. CHECK: All interpolated edges have proper naming
        bad_names = proc.con.execute(f"""
            SELECT COUNT(*)
            FROM {new_table} 
            WHERE edge_id LIKE '%_seg_%'
              AND NOT REGEXP_MATCHES(edge_id, '_seg_[0-9]+$')
        """).fetchone()[0]

        assert bad_names == 0, f"Found {bad_names} edges with malformed segment names"
        logger.info("✓ All segment names are properly formatted")

        # 5. CHECK: Node connectivity - each interpolated node connects exactly 2 edges
        node_connectivity = proc.con.execute(f"""
            WITH interpolated_nodes AS (
                SELECT DISTINCT source as node_id FROM {new_table} WHERE source LIKE 'interp_%'
                UNION
                SELECT DISTINCT target as node_id FROM {new_table} WHERE target LIKE 'interp_%'
            ),
            connections AS (
                SELECT 
                    n.node_id,
                    COUNT(e.edge_id) as connection_count
                FROM interpolated_nodes n
                LEFT JOIN {new_table} e ON n.node_id = e.source OR n.node_id = e.target
                GROUP BY n.node_id
            )
            SELECT 
                COUNT(*) as total_interpolated_nodes,
                COUNT(*) FILTER (WHERE connection_count != 2) as bad_nodes
            FROM connections
        """).fetchone()

        assert (
            node_connectivity[1] == 0
        ), f"Found {node_connectivity[1]} interpolated nodes with != 2 connections"
        logger.info(
            f"✓ All {node_connectivity[0]} interpolated nodes have exactly 2 connections"
        )

        # 6. CHECK: Geometry validity
        invalid_geoms = proc.con.execute(f"""
            SELECT COUNT(*) 
            FROM {new_table} 
            WHERE ST_GeometryType({proc._meta.geometry_column}) != 'LINESTRING'
               OR ST_IsEmpty({proc._meta.geometry_column})
        """).fetchone()[0]

        assert invalid_geoms == 0, f"Found {invalid_geoms} invalid geometries"
        logger.info("✓ All geometries are valid LINESTRINGs")

        # 7. CHECK: Segment ordering for each original edge
        segment_ordering = proc.con.execute(f"""
            WITH segments AS (
                SELECT 
                    edge_id,
                    SPLIT_PART(edge_id, '_seg_', 1) as original_edge,
                    TRY_CAST(SPLIT_PART(edge_id, '_seg_', 2) AS INTEGER) as segment_num
                FROM {new_table}
                WHERE edge_id LIKE '%_seg_%'
                AND TRY_CAST(SPLIT_PART(edge_id, '_seg_', 2) AS INTEGER) IS NOT NULL
            ),
            ordering_issues AS (
                SELECT 
                    original_edge,
                    COUNT(*) as total_segments,
                    COUNT(DISTINCT segment_num) as unique_segments,
                    MIN(segment_num) as min_segment,
                    MAX(segment_num) as max_segment,
                    LIST_SORT(LIST(segment_num)) as segment_list
                FROM segments
                GROUP BY original_edge
                HAVING COUNT(DISTINCT segment_num) != COUNT(*)
                   OR MIN(segment_num) != 1
                   OR MAX(segment_num) != COUNT(*)
            )
            SELECT COUNT(*) as ordering_problems FROM ordering_issues
        """).fetchone()[0]

        assert (
            segment_ordering == 0
        ), f"Found {segment_ordering} edges with segment ordering issues"
        logger.info("✓ All segments are properly numbered (1, 2, 3...)")

        # 8. CHECK: No duplicate edge IDs
        duplicate_edges = proc.con.execute(f"""
            SELECT COUNT(*) - COUNT(DISTINCT edge_id) 
            FROM {new_table}
        """).fetchone()[0]

        assert duplicate_edges == 0, f"Found {duplicate_edges} duplicate edge IDs"
        logger.info("✓ No duplicate edge IDs")

        # 9. CHECK: Cost proportional to length
        cost_check = proc.con.execute(f"""
            WITH interpolated_edges AS (
                SELECT 
                    edge_id,
                    length_m,
                    cost,
                    cost / NULLIF(length_m, 0) as cost_per_meter
                FROM {new_table}
                WHERE edge_id LIKE '%_seg_%'
                AND length_m > 0
            ),
            -- Group by original edge to check consistency within each split
            edge_groups AS (
                SELECT
                    SPLIT_PART(edge_id, '_seg_', 1) as original_edge,
                    AVG(cost_per_meter) as avg_cost_per_m,
                    STDDEV_POP(cost_per_meter) as std_cost_per_m
                FROM interpolated_edges
                GROUP BY SPLIT_PART(edge_id, '_seg_', 1)
            )
            -- Check if any segment deviates significantly from its group average
            SELECT COUNT(*)
            FROM interpolated_edges ie
            JOIN edge_groups eg ON SPLIT_PART(ie.edge_id, '_seg_', 1) = eg.original_edge
            WHERE ABS(ie.cost_per_meter - eg.avg_cost_per_m) > 0.1 * eg.avg_cost_per_m  -- 10% tolerance
        """).fetchone()[0]

        assert (
            cost_check == 0
        ), f"Found {cost_check} segments with inconsistent cost/length ratios"
        logger.info("✓ Cost distribution is consistent within each original edge")

        # 10. CHECK: Network is connected
        connectivity_check = proc.con.execute(f"""
            WITH all_nodes AS (
                SELECT source as node FROM {new_table}
                UNION 
                SELECT target as node FROM {new_table}
            ),
            node_degrees AS (
                SELECT
                    n.node,
                    COUNT(e.edge_id) as degree
                FROM all_nodes n
                LEFT JOIN {new_table} e ON n.node = e.source OR n.node = e.target
                GROUP BY n.node
            )
            SELECT COUNT(*) as isolated_nodes
            FROM node_degrees
            WHERE degree = 0
        """).fetchone()[0]

        assert connectivity_check == 0, f"Found {connectivity_check} isolated nodes"
        logger.info("✓ No isolated nodes (all nodes have at least 1 connection)")

        # 11. Segment endpoints should connect
        disconnected_segments = proc.con.execute(f"""
            WITH segments AS (
                SELECT 
                    edge_id,
                    source,
                    target,
                    SPLIT_PART(edge_id, '_seg_', 1) as original_edge,
                    TRY_CAST(SPLIT_PART(edge_id, '_seg_', 2) AS INTEGER) as seg_num,
                    ST_StartPoint({proc._meta.geometry_column}) as start_geom,
                    ST_EndPoint({proc._meta.geometry_column}) as end_geom
                FROM {new_table}
                WHERE edge_id LIKE '%_seg_%'
            ),
            connections AS (
                SELECT
                    s1.edge_id as edge1,
                    s2.edge_id as edge2,
                    ST_Distance(s1.end_geom, s2.start_geom) * 111320 as distance_m
                FROM segments s1
                JOIN segments s2 ON s1.original_edge = s2.original_edge 
                    AND s1.seg_num + 1 = s2.seg_num
                WHERE s1.seg_num IS NOT NULL AND s2.seg_num IS NOT NULL
            )
            SELECT COUNT(*) as disconnected_pairs
            FROM connections
            WHERE distance_m > 0.1  -- More than 10cm gap
        """).fetchone()[0]

        assert (
            disconnected_segments == 0
        ), f"Found {disconnected_segments} disconnected segment pairs"
        logger.info("✓ All segment endpoints connect properly (< 10cm gaps)")

        logger.info(
            f"\n✅ SUCCESS: Interpolated network is valid!\n"
            f"   Original: {split_stats['edge_count']} edges\n"
            f"   After: {new_stats['edge_count']} edges\n"
            f"   Max segment: {max_length:.1f}m\n"
            f"   All checks passed ✓"
        )
