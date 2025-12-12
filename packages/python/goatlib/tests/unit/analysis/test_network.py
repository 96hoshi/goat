import logging
from pathlib import Path

import pytest
from goatlib.analysis.network.network_processor import (
    InMemoryNetworkProcessor,
)
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)


@pytest.fixture
def processor(network_file: Path) -> InMemoryNetworkProcessor:
    """A pytest fixture that yields a processor within a context manager."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        proc.load_network()
        yield proc
    # Cleanup is handled automatically as the 'with' block exits


# ------------ Test Cases ------------


def test_network_loading(
    processor: InMemoryNetworkProcessor,
) -> None:
    """Tests chaining non-destructive operations and verifies intermediate results."""
    with InMemoryNetworkProcessor(input_path=processor._db_path) as proc:
        table_name = proc.load_network()

        metadata = processor.network_metadata
        assert metadata is not None

        stats = processor.get_network_stats()
        assert stats["edge_count"] > 0
        assert stats["total_length_m"] > 0.0
        logger.info(
            f"Network table '{table_name}' has {stats['edge_count']} edges, total length {stats['total_length_m']:.1f}m"
        )


def test_network_loading_with_point(
    processor: InMemoryNetworkProcessor,
) -> None:
    """Tests chaining non-destructive operations and verifies intermediate results."""
    with InMemoryNetworkProcessor(input_path=processor._db_path) as proc:
        table_name = proc.load_network(
            center=Coordinates(lat=48.137154, lon=11.576124),
            buffer_radius=1000.0,
            travel_time_minutes=15.0,
            speed_kmh=5.0,
        )
    cut_stats = processor.get_network_stats(table_name)
    assert cut_stats["edge_count"] > 0
    assert cut_stats["total_length_m"] > 0.0
    logger.info(
        f"Cut network table '{table_name}' has {cut_stats['edge_count']} edges, total length {cut_stats['total_length_m']:.1f}m"
    )

    output_path = "/app/packages/python/goatlib/tests/data/network/test.parquet"
    # save table name for confirmation
    processor.save_network(table_name, output_path)


def test_save_to_file(processor: InMemoryNetworkProcessor, data_root: Path) -> None:
    """Test saving a table to a parquet file."""
    output_file = data_root / "network" / "network_output.parquet"
    processor.save_network(processor.network_table_name, output_path=str(output_file))

    # Verify the file was created
    assert output_file.exists()
    assert output_file.stat().st_size > 0


def test_save_to_tmp(processor: InMemoryNetworkProcessor) -> None:
    """Test saving a table to a temporary parquet file."""
    tmp_file_path = processor.save_network(processor.network_table_name)
    # Verify the file was created
    from pathlib import Path

    tmp_file = Path(tmp_file_path)
    assert tmp_file.exists()
    assert tmp_file.stat().st_size > 0
    logger.info(f"Temporary network file created at: {tmp_file_path}")


def test_network_is_wkb_format(processor: InMemoryNetworkProcessor) -> None:
    """Test that the network geometries are in WKB format."""
    sample_geometry = processor.con.execute(
        f"SELECT geometry FROM {processor.network_table_name} LIMIT 1"
    ).fetchone()[0]

    assert isinstance(
        sample_geometry, bytes
    ), f"Geometry should be in WKB format (bytes), got {type(sample_geometry)}"


def test_get_available_tables(
    processor: InMemoryNetworkProcessor,
) -> None:
    """Test listing available tables in the in-memory database."""
    tables = processor.get_available_tables()
    assert isinstance(tables, list)
    assert processor.network_table_name in tables
    logger.info(f"Network table: {processor.network_table_name}")
    logger.info(f"Available tables: {tables}")


def test_split_with_subset_basic(network_file: Path) -> None:
    """Basic test that splitting works."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Try to split
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.137154, lon=11.576124),
            network_buffer_radius=500.0,
            max_search_radius_m=100.0,
        )

        # Basic assertions
        assert split_table in proc.get_available_tables()

        stats = proc.get_network_stats(split_table)
        assert 0 < stats["edge_count"] < 375164

        split_info = split_meta.raw_meta.get("split_operation", {})
        assert split_info.get("original_edge")
        assert split_info.get("artificial_node_id")

        # Quick validation of split
        fraction = split_info["split_position"]["fraction"]
        assert 0.0 <= fraction <= 1.0

        logger.info(f"Basic test passed: split {split_info['original_edge']}")


def test_split_with_subset_advanced(network_file: Path) -> None:
    """Test splitting edge on a network subset without loading full network."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # This loads only ~500m radius around the point, not the full 375k edges
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.137154, lon=11.576124),
            network_buffer_radius=500.0,
            max_search_radius_m=100.0,
        )

        # Get available tables
        tables = proc.get_available_tables()
        logger.info(f"Available tables after split: {tables}")

        # Check that split table exists
        assert split_table in tables, f"Split table {split_table} not found in {tables}"

        # Get stats for split table
        stats = proc.get_network_stats(split_table)
        logger.info(f"Split table stats: {stats}")

        # Verify the subset is smaller than full network
        assert (
            stats["edge_count"] < 375164
        ), "Subset should be smaller than full network"
        assert stats["edge_count"] > 0, "Subset should have at least one edge"

        # Verify the split worked
        split_info = split_meta.raw_meta.get("split_operation", {})  # Fixed key name
        assert split_info.get("original_edge") is not None, "Missing original edge ID"
        assert split_info.get("artificial_node_id") is not None, "Missing new node ID"
        assert (
            split_info.get("split_position", {}).get("fraction") is not None
        ), "Missing split fraction"

        # Verify split fraction is reasonable
        fraction = split_info["split_position"]["fraction"]
        assert (
            0.001 <= fraction <= 0.999
        ), f"Split fraction {fraction} should be between 0.001 and 0.999"

        # Verify distance is within search radius
        distance_m = split_info["split_position"]["distance_m"]
        assert (
            distance_m <= 100.0
        ), f"Distance {distance_m}m should be <= search radius 100m"

        # Additional useful checks:

        # 1. Check that split edge appears twice (parts A and B)
        result = proc.con.execute(f"""
            SELECT COUNT(*) 
            FROM {split_table} 
            WHERE edge_id LIKE '%_A' OR edge_id LIKE '%_B'
        """).fetchone()
        split_edge_count = result[0]
        assert (
            split_edge_count == 2
        ), f"Should have 2 split edges, got {split_edge_count}"

        # 2. Check new node connectivity
        node_id = split_info["artificial_node_id"]
        result = proc.con.execute(f"""
            SELECT 
                COUNT(*) as connections,
                SUM(CASE WHEN source = '{node_id}' THEN 1 ELSE 0 END) as as_source,
                SUM(CASE WHEN target = '{node_id}' THEN 1 ELSE 0 END) as as_target
            FROM {split_table}
            WHERE source = '{node_id}' OR target = '{node_id}'
        """).fetchone()

        assert result[0] == 2, f"New node should connect 2 edges, connects {result[0]}"
        assert (
            result[1] == 1
        ), f"New node should be source for 1 edge, is source for {result[1]}"
        assert (
            result[2] == 1
        ), f"New node should be target for 1 edge, is target for {result[2]}"

        # 3. Check edge lengths sum correctly
        original_length = split_info["edge_properties"]["original_length_m"]
        part_a_length = split_info["edge_properties"]["part_a_length_m"]
        part_b_length = split_info["edge_properties"]["part_b_length_m"]

        # Allow small floating point tolerance
        total_split_length = part_a_length + part_b_length
        length_diff = abs(original_length - total_split_length)
        assert (
            length_diff < 0.01
        ), f"Split lengths don't sum to original: {original_length} != {total_split_length}"

        logger.info(
            f"✅ Test passed: Split {split_info['original_edge']} at {fraction:.3%}"
        )
        logger.info(f"   New node: {node_id}, Distance: {distance_m:.1f}m")
        logger.info(f"   Part A: {part_a_length:.1f}m, Part B: {part_b_length:.1f}m")


def test_interpolate_point_on_edge(network_file: Path) -> None:
    """Test interpolating a point along an edge."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Try to split
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.137154, lon=11.576124),
            network_buffer_radius=500.0,
            max_search_radius_m=100.0,
        )
        stats = proc.get_network_stats(split_table)
        new_table, new_meta = proc.interpolate_long_edges(
            base_table=split_table,
            max_edge_length=50.0,
        )
        new_stats = proc.get_network_stats(new_table)

        assert new_stats["edge_count"] >= stats["edge_count"]
        logger.info(
            f"Interpolated long edges: {stats['edge_count']} → {new_stats['edge_count']} edges"
        )
        assert split_meta.raw_meta.get("split_operation", {}) is not None
        assert new_meta.raw_meta.get("interpolation_operation", {}) is not None
