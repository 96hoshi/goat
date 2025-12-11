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


def test_split_with_subset(network_file: Path) -> None:
    """Test splitting edge on a network subset without loading full network."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # This loads only ~500m radius around the point, not the full 375k edges
        split_table, split_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.137154, lon=11.576124),
            network_buffer_radius=500.0,
            max_search_radius=100.0,
        )
        tables = proc.get_available_tables()
        logger.info(f"Available tables after split: {tables}")

        stats = proc.get_network_stats(split_table)
        assert stats["edge_count"] < 375164

        # Verify the split worked
        assert split_meta.raw_meta["split_operation"]["artificial_node_id"] is not None
        logger.info(f"Split edge on subset: {split_meta.raw_meta['split_operation']}")


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


# `------------ Complex Operation Tests ------------


def test_interpolate_long_edges(processor: InMemoryNetworkProcessor) -> None:
    """Test edge interpolation functionality."""
    # Get original network stats
    original_stats = processor.get_network_stats()

    # Find a reasonable threshold - use 75th percentile of edge lengths
    edge_lengths = processor.con.execute(f"""
        SELECT length_m FROM {processor.network_table_name}
        ORDER BY length_m DESC
    """).fetchall()

    if len(edge_lengths) < 4:
        # Skip test if network is too small
        return

    # Use a threshold that will catch some but not all edges
    max_length = edge_lengths[len(edge_lengths) // 4][0]  # 75th percentile
    interpolation_distance = max_length / 3  # Create multiple segments

    # Perform interpolation
    interpolated_table, interpolated_meta = processor.interpolate_long_edges(
        max_edge_length=max_length, interpolation_distance=interpolation_distance
    )

    # Extract interpolation info from metadata
    info = interpolated_meta.raw_meta["interpolation_operation"]

    # Verify interpolation info
    assert info["original_edge_count"] == original_stats["edge_count"]
    assert info["max_edge_length_threshold"] == max_length
    assert info["interpolation_distance"] == interpolation_distance
    assert (
        info["final_edge_count"] >= info["original_edge_count"]
    )  # Should have more edges
    assert info["processing_time_seconds"] > 0

    # Verify the interpolated network has valid stats
    interpolated_stats = processor.get_network_stats(interpolated_table)
    assert interpolated_stats["edge_count"] == info["final_edge_count"]
    assert interpolated_stats["edge_count"] > 0

    # Check that no edge in the interpolated network exceeds the threshold
    long_edges_count = processor.con.execute(f"""
        SELECT COUNT(*) FROM {interpolated_table} WHERE length_m > {max_length}
    """).fetchone()[0]
    assert (
        long_edges_count == 0
    ), f"Found {long_edges_count} edges still longer than {max_length}m"

    # Verify intermediate nodes were created
    if info["new_intermediate_nodes"] > 0:
        intermediate_nodes = processor.con.execute(f"""
            SELECT COUNT(DISTINCT node_id) FROM (
                SELECT source as node_id FROM {interpolated_table} WHERE source LIKE 'interp_%'
                UNION 
                SELECT target as node_id FROM {interpolated_table} WHERE target LIKE 'interp_%'
            )
        """).fetchone()[0]
        assert intermediate_nodes > 0, "Should have created intermediate nodes"

    # Verify total length is preserved (approximately)
    original_total_length = original_stats["total_length_m"]
    interpolated_total_length = interpolated_stats["total_length_m"]
    length_diff = abs(original_total_length - interpolated_total_length)
    assert (
        length_diff / original_total_length < 0.01
    ), f"Total length changed too much: {length_diff}m"

    logger.info("Interpolation test completed:")
    logger.info(f"  Original edges: {info['original_edge_count']}")
    logger.info(f"  Long edges processed: {info['long_edges_processed']}")
    logger.info(f"  Final edges: {info['final_edge_count']}")
    logger.info(f"  New intermediate nodes: {info['new_intermediate_nodes']}")
    logger.info(f"  Max edge length threshold: {max_length:.1f}m")
    logger.info(f"  Processing time: {info['processing_time_seconds']:.2f}s")
