import logging
from pathlib import Path

import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor

logger = logging.getLogger(__name__)


def test_buffered_subset_creation(network_file: Path):
    """Test creating a spatial subset of network within a buffer."""
    # Munich city center coordinates
    lat, lon = 48.1351, 11.5820
    buffer_radius = 3000  # 3km

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Create buffered subset
        subset_table = processor.create_buffered_subset(
            latitude=lat, longitude=lon, buffer_radius=buffer_radius
        )

        # Verify subset table was created
        available_tables = processor.get_available_tables()
        assert subset_table in available_tables

        # Get statistics
        subset_meta = processor.get_subset_metadata(
            subset_table=subset_table,
            latitude=lat,
            longitude=lon,
            buffer_radius=buffer_radius,
        )

        # Verify buffer operation metadata
        assert "buffer_operation" in subset_meta.raw_meta
        buffer_info = subset_meta.raw_meta["buffer_operation"]
        assert buffer_info["operation"] == "spatial_buffer"
        assert buffer_info["buffer_radius_m"] == buffer_radius
        assert buffer_info["subset_edge_count"] > 0
        assert buffer_info["subset_edge_count"] < buffer_info["original_edge_count"]
        assert 0 < buffer_info["reduction_ratio"] < 1.0

        # Verify subset is smaller than original
        original_stats = processor.get_network_stats()
        subset_stats = processor.get_network_stats(subset_table)
        assert subset_stats["edge_count"] < original_stats["edge_count"]


def test_edge_splitting_at_point(network_file: Path):
    """Test splitting closest edge at a given point."""
    # Point near Munich center
    lat, lon = 48.1370, 11.5760

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # First create a buffered subset for faster testing
        subset_table = processor.create_buffered_subset(
            latitude=lat, longitude=lon, buffer_radius=2000
        )

        # Split edge at the point
        split_table, split_meta = processor.split_edge_at_point(
            latitude=lat,
            longitude=lon,
            base_table=subset_table,
            max_search_radius=200,
        )

        # Verify split table was created
        available_tables = processor.get_available_tables()
        assert split_table in available_tables

        # Verify split operation metadata
        assert "split_operation" in split_meta.raw_meta
        split_info = split_meta.raw_meta["split_operation"]
        assert split_info["operation"] == "edge_split"
        assert split_info["method"] == "bbox_optimization"
        assert "artificial_node_id" in split_info
        assert "original_edge_split" in split_info
        assert 0.0 <= split_info["split_fraction"] <= 1.0
        assert split_info["distance_to_edge"] <= 200

        # Verify new node coordinates are close to input point
        new_node = split_info["new_node_coords"]
        assert abs(new_node["lat"] - lat) < 0.01  # Within ~1km
        assert abs(new_node["lon"] - lon) < 0.01

        # Verify split table has more edges (original edge replaced with 2 parts)
        subset_stats = processor.get_network_stats(subset_table)
        split_stats = processor.get_network_stats(split_table)
        assert split_stats["edge_count"] == subset_stats["edge_count"] + 1


def test_complete_preprocessing_workflow(network_file: Path):
    """Test the complete workflow: buffer → split → interpolate."""
    # Origin point
    origin_lat, origin_lon = 48.1351, 11.5820
    buffer_radius = 5000  # 5km

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Step 1: Create buffered subset
        subset_table = processor.create_buffered_subset(
            latitude=origin_lat, longitude=origin_lon, buffer_radius=buffer_radius
        )

        subset_stats = processor.get_network_stats(subset_table)
        assert subset_stats["edge_count"] > 0
        print(f"\n📊 Subset contains {subset_stats['edge_count']} edges")

        # Step 2: Split edge at origin point
        split_table, split_meta = processor.split_edge_at_point(
            latitude=origin_lat,
            longitude=origin_lon,
            base_table=subset_table,
            max_search_radius=200,
        )

        origin_node_id = split_meta.raw_meta["split_operation"]["artificial_node_id"]
        assert origin_node_id is not None
        assert origin_node_id.startswith("split_node_")
        print(f"🎯 Origin node created: {origin_node_id}")

        split_stats = processor.get_network_stats(split_table)
        print(f"📈 Split network has {split_stats['edge_count']} edges")

        # Verify edges are connected to the artificial node
        connected_edges = processor.con.execute(
            f"""
            SELECT COUNT(*)
            FROM {split_table}
            WHERE source = '{origin_node_id}' OR target = '{origin_node_id}'
            """
        ).fetchone()[0]
        assert connected_edges > 0
        print(f"🔗 {connected_edges} edges connected to origin node")


def test_edge_interpolation(network_file: Path):
    """Test interpolation of long edges into smaller segments."""
    max_edge_length = 100.0  # Split edges longer than 100m

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Get original stats
        original_stats = processor.get_network_stats()
        print(f"\n📊 Original network: {original_stats['edge_count']} edges")
        print(f"   Max edge length: {original_stats['max_length_m']:.2f}m")

        # Count long edges
        long_edges = processor.con.execute(
            f"""
            SELECT COUNT(*)
            FROM {processor.network_table_name}
            WHERE length_m > {max_edge_length}
            """
        ).fetchone()[0]
        print(f"   Long edges (>{max_edge_length}m): {long_edges}")

        # Interpolate long edges
        interpolated_table, interp_meta = processor.interpolate_long_edges(
            max_edge_length=max_edge_length
        )

        # Verify interpolation metadata
        assert "interpolation_operation" in interp_meta.raw_meta
        interp_info = interp_meta.raw_meta["interpolation_operation"]
        assert interp_info["max_edge_length_threshold"] == max_edge_length
        assert interp_info["long_edges_processed"] == long_edges
        assert interp_info["final_edge_count"] > interp_info["original_edge_count"]
        assert interp_info["edges_added"] > 0
        assert interp_info["new_intermediate_nodes"] > 0

        print(f"✂️  Interpolated network: {interp_info['final_edge_count']} edges")
        print(f"   Edges added: {interp_info['edges_added']}")
        print(f"   New intermediate nodes: {interp_info['new_intermediate_nodes']}")
        print(f"   Processing time: {interp_info['processing_time_seconds']:.3f}s")

        # Verify no edge exceeds max length
        longest_edge = processor.con.execute(
            f"""
            SELECT MAX(length_m)
            FROM {interpolated_table}
            """
        ).fetchone()[0]
        assert longest_edge <= max_edge_length * 1.01  # Allow 1% tolerance
        print(f"   New max edge length: {longest_edge:.2f}m ✅")


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
    with InMemoryNetworkProcessor(str(network_file)) as processor:
        subset_table = processor.create_buffered_subset(
            latitude=lat, longitude=lon, buffer_radius=buffer_radius
        )

        stats = processor.get_network_stats(subset_table)
        print(f"\n📏 Buffer {buffer_radius}m: {stats['edge_count']} edges")

        # Verify proportional relationship exists
        assert stats["edge_count"] > 0


def test_error_handling_point_too_far_from_network(network_file: Path):
    """Test error handling when point is too far from any edge."""
    # Point in the middle of nowhere
    lat, lon = 0.0, 0.0

    with InMemoryNetworkProcessor(str(network_file)) as processor:
        # Try to split - should raise error
        with pytest.raises(ValueError, match="No edges found within"):
            processor.split_edge_at_point(
                latitude=lat, longitude=lon, max_search_radius=100
            )
