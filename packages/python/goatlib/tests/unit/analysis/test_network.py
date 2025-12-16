import logging
from pathlib import Path

from goatlib.analysis.network.network_processor import (
    InMemoryNetworkProcessor,
)
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)


def test_network_loading(network_file: Path) -> None:
    """Test basic network loading without specific coordinates."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Test metadata loading
        metadata = proc.metadata
        assert metadata is not None
        assert metadata.geometry_column == "geometry"
        assert len(metadata.columns) > 0

        table_name = proc.load_network()
        assert table_name is not None

        # Verify table exists and has data
        count = proc.con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        assert count > 0, "Network should have loaded some data"

        logger.info(f"Network table '{table_name}' loaded with {count} sample edges")


def test_network_loading_with_point(network_file: Path) -> None:
    """Test network loading with spatial filtering around a specific point."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Load network subset around a specific point
        table_name = proc.load_network(
            center=Coordinates(lat=48.137154, lon=11.576124),
            buffer_radius=1000.0,
        )

        # Verify the filtered network
        count = proc.con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        assert count > 0, "Filtered network should have edges"

        # Verify spatial filtering worked
        sample = proc.con.execute(
            f"SELECT edge_id, source, target, length_m FROM {table_name} LIMIT 1"
        ).fetchone()
        assert sample is not None, "Should have at least one edge"
        assert sample[3] > 0, "Edge should have positive length"

        logger.info(f"Filtered network table '{table_name}' has {count} edges")


def test_prepare_routing_network(network_file: Path) -> None:
    """Test the core routing network preparation functionality."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        start_point = Coordinates(lat=48.137154, lon=11.576124)

        # Test routing network preparation
        output_path, new_node_id = proc.prepare_routing_network(
            start_point=start_point, buffer_radius=500.0
        )

        # Verify outputs
        assert output_path.endswith(".parquet"), "Should return parquet file path"
        assert isinstance(new_node_id, int), "Should return integer node ID"
        assert new_node_id > 0, "Node ID should be positive"

        # Verify the output file was created
        import os

        assert os.path.exists(output_path), "Output file should exist"

        # Clean up
        os.unlink(output_path)

        logger.info(f"Successfully prepared routing network with node {new_node_id}")


def test_network_geometry_format(network_file: Path) -> None:
    """Test that network geometries are properly handled."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Load small network subset
        table_name = proc.load_network(
            center=Coordinates(lat=48.137154, lon=11.576124), buffer_radius=200.0
        )

        # Check geometry column exists and has data
        geometry_sample = proc.con.execute(
            f"SELECT geometry FROM {table_name} LIMIT 1"
        ).fetchone()[0]

        assert geometry_sample is not None, "Geometry should not be null"
        assert isinstance(geometry_sample, bytes), "Geometry should be in binary format"

        # Test conversion to text format
        wkt_sample = proc.con.execute(
            f"SELECT ST_AsText(geometry) FROM {table_name} LIMIT 1"
        ).fetchone()[0]

        assert wkt_sample is not None, "WKT conversion should work"
        assert isinstance(wkt_sample, str), "WKT should be string"
        assert "LINESTRING" in wkt_sample.upper(), "Should be LineString geometry"

        logger.info(f"Geometry format verified: {wkt_sample[:50]}...")


def test_interpolate_long_edges(network_file: Path) -> None:
    """Test edge interpolation functionality."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Load network subset
        subset_table = proc.load_network(
            center=Coordinates(lat=48.137154, lon=11.576124), buffer_radius=500.0
        )

        # Get edge count before interpolation
        count_before = proc.con.execute(
            f"SELECT COUNT(*) FROM {subset_table}"
        ).fetchone()[0]

        # Interpolate long edges
        interp_table, interp_meta = proc.interpolate_long_edges(
            max_edge_length=100.0, base_table=subset_table
        )

        # Get edge count after interpolation
        count_after = proc.con.execute(
            f"SELECT COUNT(*) FROM {interp_table}"
        ).fetchone()[0]

        # Verify interpolation worked
        assert (
            count_after >= count_before
        ), "Should have same or more edges after interpolation"
        assert interp_meta is not None, "Should return metadata"
        assert interp_meta.geometry_column == "geometry"

        logger.info(f"Interpolated {count_before} -> {count_after} edges")


def test_split_architecture_performance(network_file: Path) -> None:
    """Test the split architecture for loading vs processing performance."""
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        start_point = Coordinates(lat=48.137154, lon=11.576124)

        # Test 1: Load network first
        import time

        t1 = time.perf_counter()
        subset_table = proc.load_network(center=start_point, buffer_radius=400.0)
        t2 = time.perf_counter()
        load_time = (t2 - t1) * 1000

        # Test 2: Reuse loaded data for routing preparation
        t3 = time.perf_counter()
        output_path, node_id = proc.prepare_routing_network(
            start_point=start_point,
            buffer_radius=400.0,
            subset_table=subset_table,  # Reuse loaded data
        )
        t4 = time.perf_counter()
        prep_time = (t4 - t3) * 1000

        # Verify performance split
        assert load_time > 0, "Load time should be measurable"
        assert prep_time > 0, "Prep time should be measurable"
        assert prep_time < load_time, "Routing prep should be faster than loading"

        # Clean up
        import os

        if os.path.exists(output_path):
            os.unlink(output_path)

        logger.info(
            f"Split architecture: Load={load_time:.1f}ms, Prep={prep_time:.1f}ms"
        )


def test_cleanup_functionality(network_file: Path) -> None:
    """Test that cleanup properly closes connections and removes temporary files."""
    import os
    from pathlib import Path

    # Create processor and track its temporary directory
    proc = InMemoryNetworkProcessor(input_path=str(network_file))
    temp_dir_path = Path(proc._temp_dir)

    # Verify temp directory exists
    assert temp_dir_path.exists(), "Temporary directory should be created"

    # Use the processor to create some files
    proc.load_network(
        center=Coordinates(lat=48.137154, lon=11.576124), buffer_radius=400.0
    )

    output_path, _ = proc.prepare_routing_network(
        start_point=Coordinates(lat=48.137154, lon=11.576124), buffer_radius=400.0
    )

    # Verify the output file was created in temp directory
    assert os.path.exists(output_path), "Output file should be created"
    assert str(output_path).startswith(
        str(temp_dir_path)
    ), "Output should be in temp directory"

    # Check connection is active
    assert proc.con is not None, "Connection should be active"

    # Test connection works
    result = proc.con.execute("SELECT 1").fetchone()
    assert result[0] == 1, "Connection should be functional"

    # Call cleanup
    proc.cleanup()

    # Verify temp directory is removed
    assert (
        not temp_dir_path.exists()
    ), "Temporary directory should be removed after cleanup"

    # Verify output file is gone (part of temp directory)
    assert not os.path.exists(
        output_path
    ), "Output file should be removed with temp directory"

    logger.info(
        "Cleanup test: Successfully removed temp directory and closed connection"
    )


def test_context_manager_cleanup(network_file: Path) -> None:
    """Test that context manager automatically calls cleanup."""
    import os

    temp_dir_path = None
    output_path = None

    # Use context manager
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        temp_dir_path = Path(proc._temp_dir)

        # Verify temp directory exists during usage
        assert temp_dir_path.exists(), "Temporary directory should exist during usage"

        # Create some files
        output_path, _ = proc.prepare_routing_network(
            start_point=Coordinates(lat=48.137154, lon=11.576124), buffer_radius=400.0
        )

        # Verify file exists during usage
        assert os.path.exists(output_path), "Output file should exist during usage"

    # After context manager exits, verify cleanup happened automatically
    assert (
        not temp_dir_path.exists()
    ), "Temporary directory should be cleaned up automatically"
    assert not os.path.exists(
        output_path
    ), "Output file should be cleaned up automatically"

    logger.info("Context manager test: Automatic cleanup successful")
