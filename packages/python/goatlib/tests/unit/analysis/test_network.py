import logging
from pathlib import Path

import pytest
from goatlib.analysis.network.network_processor import (
    InMemoryNetworkProcessor,
)

logger = logging.getLogger(__name__)


@pytest.fixture
def processor(network_file: Path) -> InMemoryNetworkProcessor:
    """A pytest fixture that yields a processor within a context manager."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        yield proc
    # Cleanup is handled automatically as the 'with' block exits


# ------------ Test Cases ------------


def test_network_operations(
    processor: InMemoryNetworkProcessor,
) -> None:
    """Tests chaining non-destructive operations and verifies intermediate results."""
    base_table_name = processor.network_table_name
    filtered = processor.apply_sql_query(
        f"SELECT * FROM {base_table_name} WHERE length_m > 150"
    )

    # 2. Use the result of the previous step ('filtered') directly in the next query
    #    The 'base_table' argument is no longer needed.
    transformed = processor.apply_sql_query(
        f"SELECT *, length_m * 1.1 as adjusted_length FROM {filtered}"
    )

    # 3. Use the result of the previous step ('transformed') directly in the next query
    summary = processor.apply_sql_query(
        f"SELECT COUNT(*) as total_edges FROM {transformed}"
    )

    filtered_stats = processor.get_network_stats(filtered)
    transformed_stats = processor.get_network_stats(transformed)
    summary_count = processor.con.execute(
        f"SELECT total_edges FROM {summary}"
    ).fetchone()[0]

    # Assert that intermediate tables still exist and are correct
    assert filtered_stats["edge_count"] > 0
    assert transformed_stats["edge_count"] == filtered_stats["edge_count"]
    assert summary_count == transformed_stats["edge_count"]


def test_save_to_file(processor: InMemoryNetworkProcessor, data_root: Path) -> None:
    """Test saving a table to a parquet file."""
    output_file = data_root / "network" / "network_output.parquet"
    processor.save_table(processor.network_table_name, str(output_file))

    # Verify the file was created
    assert output_file.exists()
    assert output_file.stat().st_size > 0


def test_save_to_tmp(processor: InMemoryNetworkProcessor) -> None:
    """Test saving a table to a temporary parquet file."""
    tmp_file_path = processor.save_table(processor.network_table_name)
    # Verify the file was created
    from pathlib import Path

    tmp_file = Path(tmp_file_path)
    assert tmp_file.exists()
    assert tmp_file.stat().st_size > 0


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
    assert (
        processor.network_table_name in tables
    )  # At least the network table should be present


def test_edge_split(
    processor: InMemoryNetworkProcessor,
) -> None:
    """
    Comprehensive test for split operation correctness:
    - Network metrics are preserved (length, edge count +1)
    - Topology is correct (original edge removed, 2 new edges added)
    - New edges have correct naming and connectivity
    """
    original_stats = processor.get_network_stats()
    original_table_name = processor.network_table_name

    split_table, split_meta = processor.split_edge_at_point(
        latitude=48.13, longitude=11.58
    )

    # Extract split info from metadata
    split_info = split_meta.raw_meta["split_operation"]
    split_stats = processor.get_network_stats(split_table)
    original_edge_id = split_info["original_edge_split"]
    new_node_id = split_info["artificial_node_id"]

    # 1. Test Network Metrics Invariance
    assert split_stats["edge_count"] == original_stats["edge_count"] + 1
    assert abs(split_stats["total_length_m"] - original_stats["total_length_m"]) < 1.0
    assert split_stats["avg_length_m"] < original_stats["avg_length_m"]

    # 2. Test Original Edge Removal
    original_edge_count = processor.con.execute(
        f"SELECT COUNT(*) FROM {split_table} WHERE edge_id = '{original_edge_id}'"
    ).fetchone()[0]
    assert original_edge_count == 0

    # 3. Test New Edge Creation and Naming
    split_edges = processor.con.execute(f"""
        SELECT edge_id, source, target FROM {split_table}
        WHERE edge_id LIKE '{original_edge_id}_part_%' ORDER BY edge_id
    """).fetchall()

    assert len(split_edges) == 2
    edge_a, edge_b = split_edges

    # Check naming pattern
    assert edge_a[0] == f"{original_edge_id}_part_a"
    assert edge_b[0] == f"{original_edge_id}_part_b"

    # Check connectivity topology
    assert edge_a[2] == new_node_id  # target of part_a
    assert edge_b[1] == new_node_id  # source of part_b

    # 4. Test Edge Set Differences (verify exactly what changed)
    removed_edges = processor.con.execute(f"""
        SELECT edge_id FROM {original_table_name}
        EXCEPT SELECT edge_id FROM {split_table}
    """).fetchall()
    assert len(removed_edges) == 1
    assert str(removed_edges[0][0]) == str(original_edge_id)

    added_edges = processor.con.execute(f"""
        SELECT edge_id FROM {split_table}
        EXCEPT SELECT edge_id FROM {original_table_name}
    """).fetchall()
    added_edge_ids = {row[0] for row in added_edges}
    assert len(added_edge_ids) == 2
    assert f"{original_edge_id}_part_a" in added_edge_ids
    assert f"{original_edge_id}_part_b" in added_edge_ids


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


def test_concurrent_access(network_file: str) -> None:
    """Test that multiple processors can be created and used concurrently safely."""
    import concurrent.futures

    from goatlib.analysis.network.network_processor import (
        InMemoryNetworkProcessor,
    )

    def create_processor_and_get_stats() -> dict:
        # Each thread gets its own processor instance with its own connection
        with InMemoryNetworkProcessor(str(network_file)) as proc:
            return proc.get_network_stats()

    # Use a smaller number of workers to avoid resource exhaustion
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(create_processor_and_get_stats) for _ in range(3)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Verify all processors got consistent results
    for stats in results:
        assert stats["edge_count"] > 0

    # All results should be identical since they're loading the same file
    edge_counts = [stats["edge_count"] for stats in results]
    assert (
        len(set(edge_counts)) == 1
    ), "All processors should report the same edge count"
