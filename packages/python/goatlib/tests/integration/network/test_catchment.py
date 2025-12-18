import logging
import os
import time
from pathlib import Path

import fast_routing_py as routing
from goatlib.analysis.network.network_processor import (
    InMemoryNetworkProcessor,
)
from goatlib.routing.schemas.base import Coordinates

logger = logging.getLogger(__name__)

example_request = {
    "starting_points": [{"lat": 48.1351, "lon": 11.5820}],  # Munich central
    "cutoffs": [10, 20, 30],
    "type": "point",
}


def test_catchment_workflow(network_file: Path):
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Use the new optimized method that combines all preprocessing
        start_coords = Coordinates(lat=48.1351, lon=11.5820)

        # Define cutoffs first to ensure network preparation covers the max cutoff
        cutoffs_minutes = [10, 20, 30]
        max_cutoff = max(cutoffs_minutes)

        parquet_path, start_node_id = proc.prepare_routing_network(
            start_point=start_coords,
            buffer_radius=1000.0,
            travel_time_minutes=max_cutoff,  # Use max cutoff for network preparation
            speed_kmh=5.0,
        )

        # Load network with fast_routing_py and calculate isochrone
        network = routing.load_network(parquet_path)

        # Calculate isochrones for the requested cutoffs (convert minutes to seconds)
        cutoffs_seconds = [c * 60 for c in cutoffs_minutes]
        results = network.calculate_isochrone_multiple_times(
            start_node=start_node_id, time_thresholds=cutoffs_seconds
        )

        assert len(results) == 3  # One result per cutoff
        for i, result in enumerate(results):
            assert result.reachable_nodes > 0
            logger.info(
                f"Cutoff {cutoffs_minutes[i]} min: {result.reachable_nodes} reachable nodes"
            )


def test_optimized_catchment_benchmark(network_file: Path):
    """
    Benchmark the optimized catchment workflow with split-edge approach.
    Tests realistic scenarios with performance targets.
    """
    logger.info("=== OPTIMIZED CATCHMENT BENCHMARK ===")

    # Test configurations: [buffer_radius, travel_time, speed, expected_time_ms]
    test_configs = [
        (200, 2.0, 12.0, 85),  # Ultra-minimal for speed
        (400, 3.0, 12.0, 95),  # Small catchment
        (800, 5.0, 12.0, 110),  # Medium catchment
    ]

    results = []

    for buffer_radius, travel_time, speed, expected_max_ms in test_configs:
        logger.info(
            f"\n--- Testing {buffer_radius}m buffer, {travel_time}min travel time ---"
        )

        # Run 3 iterations for stable timing
        times = []
        for run in range(3):
            with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
                start_coords = Coordinates(lat=48.1351, lon=11.5820)

                # Time the full preparation
                t1 = time.time()
                parquet_path, start_node_id = proc.prepare_routing_network(
                    start_point=start_coords,
                    buffer_radius=buffer_radius,
                    travel_time_minutes=travel_time,
                    speed_kmh=speed,
                )
                t2 = time.time()
                prep_time = (t2 - t1) * 1000

                # Quick routing test
                t3 = time.time()
                network = routing.load_network(parquet_path)
                cutoffs_seconds = [5 * 60, 10 * 60]  # 5min, 10min
                isochrones = network.calculate_isochrone_multiple_times(
                    start_node=start_node_id, time_thresholds=cutoffs_seconds
                )
                t4 = time.time()
                routing_time = (t4 - t3) * 1000

                total_time = prep_time + routing_time
                times.append(
                    {
                        "prep": prep_time,
                        "routing": routing_time,
                        "total": total_time,
                        "nodes": sum(r.reachable_nodes for r in isochrones),
                    }
                )

                # Cleanup
                if os.path.exists(parquet_path):
                    os.unlink(parquet_path)

        # Calculate averages
        avg_prep = sum(t["prep"] for t in times) / len(times)
        avg_routing = sum(t["routing"] for t in times) / len(times)
        avg_total = sum(t["total"] for t in times) / len(times)
        avg_nodes = sum(t["nodes"] for t in times) / len(times)

        # Log results
        prep_status = "✓" if avg_prep < expected_max_ms else "✗"
        total_status = "✓" if avg_total < expected_max_ms + 50 else "✗"

        logger.info(f"  Network prep: {avg_prep:.1f}ms {prep_status}")
        logger.info(f"  Routing calc: {avg_routing:.1f}ms")
        logger.info(f"  Total time:   {avg_total:.1f}ms {total_status}")
        logger.info(f"  Avg nodes:    {avg_nodes:.0f}")

        results.append(
            {
                "config": f"{buffer_radius}m_{travel_time}min",
                "prep_time": avg_prep,
                "routing_time": avg_routing,
                "total_time": avg_total,
                "target_prep": expected_max_ms,
                "nodes": avg_nodes,
            }
        )

    # Summary analysis
    best_prep = min(r["prep_time"] for r in results)
    best_total = min(r["total_time"] for r in results)

    logger.info(f"Best prep time: {best_prep:.1f}ms")
    logger.info(f"Best total time: {best_total:.1f}ms")

    # Performance assertions
    assert best_prep < 100, f"Best prep time {best_prep:.1f}ms should be under 100ms"
    assert best_total < 150, f"Best total time {best_total:.1f}ms should be under 150ms"
    assert all(
        r["nodes"] > 100 for r in results
    ), "All configs should find substantial nodes"

    logger.info("✓ Optimized catchment benchmark PASSED")


def test_split_edge_accuracy_benchmark(network_file: Path):
    """
    Test the accuracy improvements of the optimized routing network preparation.
    """
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        start_coords = Coordinates(lat=48.1351, lon=11.5820)

        # Test optimized routing network preparation
        t1 = time.time()
        parquet_path, start_node_id = proc.prepare_routing_network(
            start_point=start_coords, buffer_radius=500.0
        )
        t2 = time.time()

        prep_time = (t2 - t1) * 1000

        logger.info(f"Optimized routing prep: {prep_time:.1f}ms")
        logger.info(f"  Start node ID: {start_node_id}")
        logger.info(f"  Output file: {parquet_path}")

        # Load the result to verify network quality
        import duckdb

        con = duckdb.connect(":memory:")
        con.execute("INSTALL spatial; LOAD spatial;")

        # Get network statistics
        network_info = con.execute(f"""
            SELECT 
                COUNT(*) as edge_count,
                COUNT(DISTINCT source) as unique_sources,
                COUNT(DISTINCT target) as unique_targets,
                AVG(length_m) as avg_length
            FROM read_parquet('{parquet_path}')
        """).fetchone()

        edge_count = network_info[0]
        avg_length = network_info[3]

        logger.info(f"  Network edges: {edge_count}")
        logger.info(f"  Avg edge length: {avg_length:.1f}m")

        # Verify the start node exists in the network
        start_node_exists = con.execute(f"""
            SELECT COUNT(*) FROM read_parquet('{parquet_path}')
            WHERE source = {start_node_id} OR target = {start_node_id}
        """).fetchone()[0]

        logger.info(f"  Start node connectivity: {start_node_exists} edges")

        # Clean up
        import os

        if os.path.exists(parquet_path):
            os.unlink(parquet_path)
        con.close()

        # Assertions for quality
        assert edge_count > 100, "Network should have substantial edges"
        assert start_node_exists > 0, "Start node should be connected to the network"
        assert avg_length > 0, "Edges should have positive length"
        assert (
            prep_time < 150
        ), f"Preparation took {prep_time:.1f}ms, should be under 150ms"

        logger.info("✓ Optimized routing network accuracy benchmark PASSED")


# add a test to try calculate_multiple_isochrones on the rust_network_analysis module
def test_rust_network_multiple_isochrones(network_file: Path):
    """
    Test the Rust network analysis library's ability to calculate multiple isochrones.
    """

    # Use InMemoryNetworkProcessor to prepare a properly formatted network for Rust
    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        start_coords = Coordinates(lat=48.1351, lon=11.5820)

        # Prepare the network in the format expected by the Rust library
        parquet_path, start_node_id = proc.prepare_routing_network(
            start_point=start_coords,
            buffer_radius=1000.0,
            travel_time_minutes=20.0,
            speed_kmh=5.0,
        )

        # Load the network using the Rust library
        network = routing.load_network(parquet_path)

    # Define multiple cutoffs in seconds
    cutoffs_seconds = [300, 600, 900]  # 5min, 10min, 15min

    # Calculate multiple isochrones
    results = network.calculate_isochrone_multiple_times(
        start_node=start_node_id, time_thresholds=cutoffs_seconds
    )

    assert len(results) == len(cutoffs_seconds), "Should return results for all cutoffs"

    for i, result in enumerate(results):
        assert (
            result.reachable_nodes > 0
        ), f"Isochrone for cutoff {cutoffs_seconds[i]}s should have reachable nodes"
        logger.info(
            f"Cutoff {cutoffs_seconds[i]//60} min: {result.reachable_nodes} reachable nodes"
        )

    logger.info("✓ Rust network multiple isochrones test PASSED")
