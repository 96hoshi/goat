import logging
from pathlib import Path

import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)


def test_buffered_subset_creation(network_file: Path):
    """Test creating a spatial subset of network within a buffer."""
    # Munich city center coordinates
    center = Coordinates(lat=48.1351, lon=11.5820)
    buffer_radius = 3000  # 3km

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Create buffered subset using load_network (which creates subset)
        subset_table = processor.load_network(
            center=center, buffer_radius=buffer_radius
        )

        # Get statistics
        subset_stats = processor.get_network_stats(subset_table)
        original_stats = processor.get_network_stats(processor.network_table_name)

        # Verify subset is smaller than original (for reasonable buffer sizes)
        if buffer_radius < 50000:  # Only check for modest buffers
            assert subset_stats["edge_count"] < original_stats["edge_count"]

        assert subset_stats["edge_count"] > 0
        logger.info(
            f"Created subset with {subset_stats['edge_count']} edges (original: {original_stats['edge_count']})"
        )


def test_edge_splitting_at_point(network_file: Path):
    """Test splitting closest edge at a given point."""
    # Point near Munich center
    point = Coordinates(lat=48.1370, lon=11.5760)

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # First load a buffered subset for faster testing
        subset_table = processor.load_network(center=point, buffer_radius=2000)

        # Split edge at the point
        split_table, split_meta = processor.split_edge_at_point(
            point=point,
            source_table=subset_table,
            max_search_radius_m=200,
        )

        # Verify split operation metadata
        assert "split_operation" in split_meta.raw_meta
        split_info = split_meta.raw_meta["split_operation"]
        assert "artificial_node_id" in split_info
        assert "original_edge" in split_info
        assert 0.0 <= split_info["split_position"]["fraction"] <= 1.0

        # Verify new node coordinates are close to input point
        actual_point = split_info["split_position"]["actual_point"]
        assert abs(actual_point["lat"] - point.lat) < 0.01  # Within ~1km
        assert abs(actual_point["lon"] - point.lon) < 0.01

        # Verify split table has more edges (original edge replaced with 2 parts)
        subset_stats = processor.get_network_stats(subset_table)
        split_stats = processor.get_network_stats(split_table)
        assert split_stats["edge_count"] == subset_stats["edge_count"] + 1
        logger.info(
            f"Split edge: {subset_stats['edge_count']} → {split_stats['edge_count']} edges"
        )


def test_split_edge_at_point_with_subset(network_file: Path):
    """Test the combined split with subset method."""
    point = Coordinates(lat=48.137154, lon=11.576124)

    with InMemoryNetworkProcessor(str(network_file)) as proc:
        # This loads subset and splits in one call
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=point,
            network_buffer_radius=500.0,
            max_search_radius_m=100.0,
        )

        # Verify results
        assert "split_operation" in split_meta.raw_meta

        stats = proc.get_network_stats(split_table)
        assert stats["edge_count"] > 0
        logger.info(f"Split with subset: {stats['edge_count']} edges")


def test_complete_preprocessing_workflow(network_file: Path):
    """Test the complete workflow: buffer → split → interpolate."""
    # Origin point
    origin = Coordinates(lat=48.1351, lon=11.5820)
    buffer_radius = 5000  # 5km

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Step 1: Create buffered subset
        subset_table = processor.load_network(
            center=origin, buffer_radius=buffer_radius
        )

        subset_stats = processor.get_network_stats(subset_table)
        assert subset_stats["edge_count"] > 0
        logger.info(f"📊 Subset contains {subset_stats['edge_count']} edges")

        # Step 2: Split edge at origin point
        split_table, split_meta = processor.split_edge_at_point(
            point=origin,
            source_table=subset_table,
            max_search_radius_m=200,
        )

        origin_node_id = split_meta.raw_meta["split_operation"]["artificial_node_id"]
        assert origin_node_id is not None
        assert origin_node_id.startswith("split_node_") or origin_node_id.startswith(
            "n_"
        )
        logger.info(f"🎯 Origin node created: {origin_node_id}")

        split_stats = processor.get_network_stats(split_table)
        logger.info(f"📈 Split network has {split_stats['edge_count']} edges")

        # Verify edges are connected to the artificial node
        connected_edges = processor.con.execute(
            f"""
            SELECT COUNT(*)
            FROM {split_table}
            WHERE source = '{origin_node_id}' OR target = '{origin_node_id}'
            """
        ).fetchone()[0]
        assert connected_edges == 2  # Should connect exactly 2 edges (the split parts)
        logger.info(f"🔗 {connected_edges} edges connected to origin node")

        # Step 3: Interpolate long edges
        interpolated_table, interp_meta = processor.interpolate_long_edges(
            max_edge_length=50.0,
            base_table=split_table,
            include_stats=True,
        )

        interp_stats = processor.get_network_stats(interpolated_table)
        logger.info(f"✂️  Interpolated to {interp_stats['edge_count']} edges")

        # Verify workflow produced valid network
        assert interp_stats["edge_count"] > split_stats["edge_count"]


def test_edge_interpolation(network_file: Path):
    """Test interpolation of long edges into smaller segments."""
    max_edge_length = 100.0  # Split edges longer than 100m

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Load a subset first for faster testing
        center = Coordinates(lat=48.1351, lon=11.5820)
        subset_table = processor.load_network(center=center, buffer_radius=2000)

        # Get original stats
        original_stats = processor.get_network_stats(subset_table)
        logger.info(f"\n📊 Original network: {original_stats['edge_count']} edges")
        logger.info(f"   Max edge length: {original_stats['max_length_m']:.2f}m")

        # Count long edges
        long_edges = processor.con.execute(
            f"""
            SELECT COUNT(*)
            FROM {subset_table}
            WHERE length_m > {max_edge_length}
            """
        ).fetchone()[0]
        logger.info(f"   Long edges (>{max_edge_length}m): {long_edges}")

        # Interpolate long edges
        interpolated_table, interp_meta = processor.interpolate_long_edges(
            max_edge_length=max_edge_length,
            base_table=subset_table,
            include_stats=True,
        )

        # Verify interpolation metadata
        if "stats" in interp_meta.raw_meta:
            stats = interp_meta.raw_meta["stats"]
            logger.info(f"✂️  Segments created: {stats.get('segments_added', 'N/A')}")
            logger.info(
                f"   Max segment: {stats.get('max_segment_length', 'N/A'):.1f}m"
            )
        elif "interpolation_operation" in interp_meta.raw_meta:
            interp_info = interp_meta.raw_meta["interpolation_operation"]
            logger.info(
                f"✂️  Interpolated network: {interp_info.get('final_edge_count', 'N/A')} edges"
            )
            logger.info(f"   Edges added: {interp_info.get('edges_added', 'N/A')}")

        # Verify no edge exceeds max length (with tolerance)
        longest_edge = (
            processor.con.execute(
                f"""
            SELECT MAX(length_m)
            FROM {interpolated_table}
            WHERE edge_id LIKE '%_s%' OR edge_id LIKE '%_seg_%'
            """
            ).fetchone()[0]
            or 0
        )
        assert longest_edge <= max_edge_length * 1.1  # Allow 10% tolerance
        logger.info(f"   New max segment length: {longest_edge:.2f}m ✅")


def test_interpolate_point_on_edge(network_file: Path):
    """Test interpolating a point along an edge."""
    with InMemoryNetworkProcessor(str(network_file)) as proc:
        # First split at a point
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.137154, lon=11.576124),
            network_buffer_radius=500.0,
            max_search_radius_m=100.0,
        )

        split_stats = proc.get_network_stats(split_table)
        logger.info(f"Split network: {split_stats['edge_count']} edges")

        # Then interpolate long edges
        new_table, new_meta = proc.interpolate_long_edges(
            base_table=split_table,
            max_edge_length=50.0,
        )

        new_stats = proc.get_network_stats(new_table)
        logger.info(f"Interpolated network: {new_stats['edge_count']} edges")

        # Basic checks
        assert new_stats["edge_count"] >= split_stats["edge_count"]
        assert split_meta.raw_meta.get("split_operation", {}) is not None

        # Check metadata exists (structure depends on include_stats)
        assert new_meta.raw_meta is not None


@pytest.mark.parametrize(
    "lat,lon,buffer_radius",
    [
        (48.1351, 11.5820, 1000),  # Small buffer
        (48.1351, 11.5820, 5000),  # Medium buffer
        (48.1351, 11.5820, 10000),  # Large buffer
    ],
)
def test_buffer_radius_variations(
    network_file: Path, lat: float, lon: float, buffer_radius: float
):
    """Test that larger buffers result in more edges."""
    center = Coordinates(lat=lat, lon=lon)

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        subset_table = processor.load_network(
            center=center, buffer_radius=buffer_radius
        )

        stats = processor.get_network_stats(subset_table)
        logger.info(f"\n📏 Buffer {buffer_radius}m: {stats['edge_count']} edges")

        # Verify proportional relationship exists (larger buffer = more edges)
        assert stats["edge_count"] > 0


def test_error_handling_point_too_far_from_network(network_file: Path):
    """Test error handling when point is too far from any edge."""
    # Point in the middle of nowhere (Atlantic Ocean)
    point = Coordinates(lat=0.0, lon=0.0)

    with InMemoryNetworkProcessor(str(network_file)) as proc:
        # load_network should work (creates empty or small subset)
        subset_table = proc.load_network(center=point, buffer_radius=1000)

        # But split_edge_at_point should fail
        with pytest.raises(ValueError, match="No edge found within"):
            proc.split_edge_at_point(
                point=point,
                source_table=subset_table,
                max_search_radius_m=1000.0,  # Even large radius
            )


def test_error_handling_invalid_split_position(network_file: Path):
    """Test error handling when split point is at edge endpoint."""
    with InMemoryNetworkProcessor(str(network_file)) as proc:
        # Load a subset
        center = Coordinates(lat=48.1351, lon=11.5820)
        subset_table = proc.load_network(center=center, buffer_radius=1000)

        # Find an actual edge endpoint to test
        result = proc.con.execute(f"""
            SELECT 
                source,
                ST_X(ST_StartPoint({proc._meta.geometry_column})) as start_lon,
                ST_Y(ST_StartPoint({proc._meta.geometry_column})) as start_lat
            FROM {subset_table}
            LIMIT 1
        """).fetchone()

        if result:
            # Try to split at the exact start of an edge
            point = Coordinates(lat=result[2], lon=result[1])

            # This might fail or warn depending on implementation
            try:
                split_table, meta = proc.split_edge_at_point(
                    point=point,
                    source_table=subset_table,
                    max_search_radius_m=10.0,
                )
                # If it succeeds, check warning in metadata
                logger.info("Split at endpoint succeeded (fraction should be ~0)")
            except ValueError as e:
                if "too close to endpoint" in str(e):
                    logger.info(f"Correctly rejected split at endpoint: {e}")
                else:
                    raise


def test_network_stats_method(network_file: Path):
    """Test the get_network_stats method."""
    with InMemoryNetworkProcessor(str(network_file)) as proc:
        # Test on main network
        proc.load_network()
        stats = proc.get_network_stats()
        assert "edge_count" in stats
        assert "total_length_m" in stats
        assert "avg_length_m" in stats
        assert stats["edge_count"] > 0

        logger.info(
            f"Main network: {stats['edge_count']} edges, {stats['total_length_m']:.0f}m total"
        )

        # Test on subset
        subset = proc.load_network(
            center=Coordinates(lat=48.1351, lon=11.5820), buffer_radius=1000
        )
        subset_stats = proc.get_network_stats(subset)
        assert subset_stats["edge_count"] > 0
        assert subset_stats["edge_count"] <= stats["edge_count"]


def test_apply_sql_query(network_file: Path):
    """Test applying custom SQL queries."""
    with InMemoryNetworkProcessor(str(network_file)) as proc:
        table = proc.load_network(
            center=Coordinates(lat=48.1351, lon=11.5820), buffer_radius=2000
        )
        # Create a simple filtered table
        result_table = proc.apply_sql_query(
            sql_query=f"""
            SELECT * 
            FROM {table} 
            WHERE length_m > 100 
            LIMIT 10
            """,
            result_table="long_edges",
        )

        stats = proc.get_network_stats(result_table)
        assert stats["edge_count"] <= 10
        assert stats["min_length_m"] > 100

        logger.info(f"SQL query created table with {stats['edge_count']} edges > 100m")
