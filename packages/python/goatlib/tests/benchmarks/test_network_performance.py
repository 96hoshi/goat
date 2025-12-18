#!/usr/bin/env python3
import gc
import logging
import os
import time
from pathlib import Path

import psutil
import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.schemas.base import Coordinates

# Set up logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_benchmark_split_architecture():
    """Benchmark the split architecture benefits."""
    test_file = Path(__file__).parent.parent / "data" / "network" / "network.parquet"

    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return

    start_point = Coordinates(lat=48.1351, lon=11.5820)

    with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
        # Traditional approach (load every time)
        traditional_times = []

        for i in range(3):
            gc.collect()
            t1 = time.perf_counter()
            output_path, node_id = proc.prepare_routing_network(
                start_point=start_point, buffer_radius=400.0
            )
            t2 = time.perf_counter()
            elapsed = (t2 - t1) * 1000
            traditional_times.append(elapsed)

            # Cleanup
            import os

            if os.path.exists(output_path):
                os.unlink(output_path)

        avg_traditional = sum(traditional_times) / len(traditional_times)

        # Split approach (load once, reuse)
        # Load once
        gc.collect()
        t_load_start = time.perf_counter()
        subset_table = proc.load_network(center=start_point, buffer_radius=400.0)
        t_load_end = time.perf_counter()
        load_time = (t_load_end - t_load_start) * 1000

        # Reuse loaded data multiple times
        reuse_times = []
        for i in range(3):
            gc.collect()
            t1 = time.perf_counter()
            output_path, node_id = proc.prepare_routing_network(
                start_point=start_point,
                buffer_radius=400.0,
                subset_table=subset_table,  # Reuse!
            )
            t2 = time.perf_counter()
            elapsed = (t2 - t1) * 1000
            reuse_times.append(elapsed)

            # Cleanup
            import os

            if os.path.exists(output_path):
                os.unlink(output_path)

        avg_reuse = sum(reuse_times) / len(reuse_times)

        # Calculate benefits
        total_traditional = avg_traditional * 3
        total_split = load_time + (avg_reuse * 3)
        savings = total_traditional - total_split

        logger.info(
            f"Split architecture: {savings:.1f}ms savings ({savings/total_traditional*100:.1f}%)"
        )
        logger.info(
            f"  Traditional: {avg_traditional:.1f}ms avg | Split: Load {load_time:.1f}ms + Routing {avg_reuse:.1f}ms"
        )

        if avg_reuse < 10:
            logger.info(f"✅ EXCELLENT: Routing logic only {avg_reuse:.1f}ms!")
        elif avg_reuse < 20:
            logger.info(f"✅ VERY GOOD: Routing logic {avg_reuse:.1f}ms")
        else:
            logger.info(f"⚠ COULD IMPROVE: Routing logic {avg_reuse:.1f}ms")


def test_benchmark_buffer_sizes():
    """Benchmark different buffer sizes."""
    test_file = Path(__file__).parent.parent / "data" / "network" / "network.parquet"

    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return

    start_point = Coordinates(lat=48.1351, lon=11.5820)

    buffer_sizes = [200, 400, 800, 1200, 1600]  # meters

    for buffer_m in buffer_sizes:
        times = []
        edge_counts = []

        for run in range(3):
            gc.collect()

            with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
                t1 = time.perf_counter()
                output_path, node_id = proc.prepare_routing_network(
                    start_point=start_point, buffer_radius=buffer_m
                )
                t2 = time.perf_counter()

                elapsed = (t2 - t1) * 1000
                times.append(elapsed)

                # Get edge count from output
                import duckdb

                con = duckdb.connect()
                con.execute("INSTALL spatial; LOAD spatial;")
                edge_count = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{output_path}')"
                ).fetchone()[0]
                edge_counts.append(edge_count)
                con.close()

                # Cleanup
                import os

                if os.path.exists(output_path):
                    os.unlink(output_path)

        avg_time = sum(times) / len(times)
        avg_edges = sum(edge_counts) / len(edge_counts)
        min_time = min(times)
        max_time = max(times)

        if avg_time < 100:
            status = "✅"
        elif avg_time < 150:
            status = "✓"
        else:
            status = "⚠"

        logger.info(
            f"{status} {buffer_m}m: {avg_time:.1f}ms avg ({min_time:.1f}-{max_time:.1f}ms), {avg_edges:.0f} edges"
        )


def test_benchmark_artificial_node_only():
    """Benchmark just the artificial node creation logic."""
    test_file = Path(__file__).parent.parent / "data" / "network" / "network.parquet"

    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return

    start_point = Coordinates(lat=48.1351, lon=11.5820)

    with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
        # Load network once
        subset_table = proc.load_network(center=start_point, buffer_radius=400.0)

        # Test artificial node creation multiple times
        times = []
        for i in range(10):
            gc.collect()

            search_radius_deg = 200.0 / 111320.0
            new_node_id = (
                abs(hash(f"split_{start_point.lat}_{start_point.lon}_{i}")) % 2147483647
            )

            t1 = time.perf_counter()

            # Core artificial node creation
            proc.con.execute(f"""
                DROP TABLE IF EXISTS temp_artificial_benchmark;
                CREATE TEMP TABLE temp_artificial_benchmark AS
                WITH 
                point_ref AS (
                    SELECT ST_MakePoint({start_point.lon}, {start_point.lat})::GEOMETRY as search_point
                ),
                closest AS (
                    SELECT *,
                        ST_Distance(geometry::GEOMETRY, p.search_point) as dist,
                        ST_LineLocatePoint(geometry::GEOMETRY, p.search_point) as frac
                    FROM {subset_table}, point_ref p
                    WHERE ST_DWithin(geometry::GEOMETRY, p.search_point, {search_radius_deg})
                    ORDER BY dist
                    LIMIT 1
                ),
                split_result AS (
                    SELECT edge_id, source, target, length_m, geometry
                    FROM {subset_table}
                    WHERE edge_id NOT IN (SELECT edge_id FROM closest)
                    
                    UNION ALL
                    
                    SELECT 
                        c.edge_id,
                        c.source,
                        {new_node_id} as target,
                        c.length_m * c.frac as length_m,
                        ST_LineSubstring(c.geometry::GEOMETRY, 0, c.frac) as geometry
                    FROM closest c
                    
                    UNION ALL
                    
                    SELECT 
                        c.edge_id + 1000000 as edge_id,
                        {new_node_id} as source, 
                        c.target,
                        c.length_m * (1 - c.frac) as length_m,
                        ST_LineSubstring(c.geometry::GEOMETRY, c.frac, 1) as geometry
                    FROM closest c
                )
                SELECT * FROM split_result;
            """)

            t2 = time.perf_counter()
            elapsed = (t2 - t1) * 1000
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)

        if avg_time < 5:
            status = "✅"
        elif avg_time < 10:
            status = "✓"
        else:
            status = "⚠"

        logger.info(
            f"{status} Artificial node: {avg_time:.2f}ms avg (range: {min_time:.2f}-{max_time:.2f}ms)"
        )


def test_benchmark_memory_and_performance():
    """Comprehensive benchmark combining memory usage and performance metrics."""
    test_file = Path(__file__).parent.parent / "data" / "network" / "network.parquet"

    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return

    # Get current process for memory tracking
    process = psutil.Process(os.getpid())

    def get_memory_info():
        """Get current memory usage in MB."""
        mem_info = process.memory_info()
        return {
            "rss_mb": mem_info.rss / 1024 / 1024,
            "vms_mb": mem_info.vms / 1024 / 1024,
            "available_mb": psutil.virtual_memory().available / 1024 / 1024,
            "percent": psutil.virtual_memory().percent,
        }

    start_point = Coordinates(lat=48.1351, lon=11.5820)
    buffer_sizes = [400, 800, 1200]  # Different buffer sizes to test scaling

    results = []

    # Baseline memory
    gc.collect()
    baseline = get_memory_info()

    for buffer_m in buffer_sizes:
        # Test with fresh processor instances
        gc.collect()
        before_test = get_memory_info()

        performance_times = []
        peak_memory = before_test["rss_mb"]

        for run in range(3):
            gc.collect()
            run_start_memory = get_memory_info()

            with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
                # Measure both time and memory for complete operation
                t1 = time.perf_counter()

                # Load network
                subset_table = proc.load_network(
                    center=start_point, buffer_radius=buffer_m
                )

                after_load = get_memory_info()
                peak_memory = max(peak_memory, after_load["rss_mb"])

                # Prepare routing network
                output_path, node_id = proc.prepare_routing_network(
                    start_point=start_point,
                    buffer_radius=buffer_m,
                    subset_table=subset_table,
                )

                t2 = time.perf_counter()
                after_prep = get_memory_info()
                peak_memory = max(peak_memory, after_prep["rss_mb"])

                elapsed_ms = (t2 - t1) * 1000
                performance_times.append(elapsed_ms)

                # Get network statistics from output
                import duckdb

                temp_con = duckdb.connect()
                temp_con.execute("INSTALL spatial; LOAD spatial;")
                edge_count = temp_con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{output_path}')"
                ).fetchone()[0]
                temp_con.close()

                # Cleanup
                if os.path.exists(output_path):
                    os.unlink(output_path)

        # Calculate statistics
        avg_time = sum(performance_times) / len(performance_times)
        min_time = min(performance_times)
        max_time = max(performance_times)
        memory_increase = peak_memory - baseline["rss_mb"]

        results.append(
            {
                "buffer_m": buffer_m,
                "avg_time_ms": avg_time,
                "min_time_ms": min_time,
                "max_time_ms": max_time,
                "peak_memory_mb": peak_memory,
                "memory_increase_mb": memory_increase,
                "edge_count": edge_count,
            }
        )

        # Memory efficiency calculation
        memory_per_edge = (
            memory_increase / edge_count * 1024 if edge_count > 0 else 0
        )  # KB per edge

        # Performance vs memory assessment
        if avg_time < 100 and memory_increase < 100:
            efficiency = "✅"
        elif avg_time < 150 and memory_increase < 150:
            efficiency = "✓"
        else:
            efficiency = "⚠"

        logger.info(
            f"{efficiency} {buffer_m}m: {avg_time:.1f}ms ({min_time:.1f}-{max_time:.1f}ms), {memory_increase:.1f}MB, {memory_per_edge:.1f}KB/edge"
        )

    # Final cleanup check
    gc.collect()
    time.sleep(0.2)
    final = get_memory_info()
    total_cleanup = final["rss_mb"] - baseline["rss_mb"]

    peak_increase = max(r["memory_increase_mb"] for r in results)
    logger.info(
        f"Memory: Peak {peak_increase:.1f}MB, Cleanup {total_cleanup:+.1f}MB, Available {final['available_mb']:.1f}MB"
    )

    # Scalability analysis
    if len(results) >= 2:
        # Check if performance scales reasonably with buffer size
        small_buffer = results[0]
        large_buffer = results[-1]

        time_scale_factor = large_buffer["avg_time_ms"] / small_buffer["avg_time_ms"]
        memory_scale_factor = (
            large_buffer["memory_increase_mb"] / small_buffer["memory_increase_mb"]
        )
        edge_scale_factor = large_buffer["edge_count"] / small_buffer["edge_count"]

        time_status = "✅" if time_scale_factor < edge_scale_factor * 1.5 else "⚠"
        memory_status = "✅" if memory_scale_factor < edge_scale_factor * 2 else "⚠"

        logger.info(
            f"Scalability: {time_status} Time {time_scale_factor:.1f}x, {memory_status} Memory {memory_scale_factor:.1f}x, Edges {edge_scale_factor:.1f}x"
        )


def test_benchmark_artificial_node_splitting():
    """Benchmark artificial node splitting performance."""
    test_file = Path(__file__).parent.parent / "data" / "network" / "network.parquet"

    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return
    origin = Coordinates(lat=48.1351, lon=11.5820)
    start_points = [
        Coordinates(lat=origin.lat + i * 0.0001, lon=origin.lon + i * 0.0001)
        for i in range(5)
    ]

    with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
        # Load network once
        subset_table = proc.load_network(center=origin, buffer_radius=400.0)
        search_radius_m = 200.0
        # Test artificial node creation multiple times
        times = []
        for i in range(10):
            gc.collect()

            t1 = time.perf_counter()

            # Core artificial node creation
            proc.create_artificial_nodes_for_points(
                points=start_points,
                search_radius_m=search_radius_m,
                subset_table=subset_table,
            )

            t2 = time.perf_counter()
            elapsed = (t2 - t1) * 1000
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)

        if avg_time < 5:
            status = "✅"
        elif avg_time < 10:
            status = "✓"
        else:
            status = "⚠"
            pytest.fail("Artificial node splitting too slow")

        logger.info(
            f"{status} Artificial node splitting: {avg_time:.2f}ms avg (range: {min_time:.2f}-{max_time:.2f}ms)"
        )


@pytest.mark.benchmark
def test_benchmark_artificial_node_splitting_large():
    """Test performance of create_artificial_nodes_for_points with larger datasets."""
    test_file = Path(__file__).parent.parent / "data" / "network" / "network.parquet"

    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return

    origin = Coordinates(lat=48.1351, lon=11.5820)

    # Test with different sized datasets
    test_sizes = [100, 200, 500]

    for num_points in test_sizes:
        logger.info(f"\n--- Testing with {num_points} points ---")

        # Create random points around Munich
        import random

        random.seed(42)  # For reproducibility

        start_points = []
        for i in range(num_points):
            # Generate points within reasonable distance from origin
            lat_offset = random.uniform(-0.01, 0.01)  # ~1km radius
            lon_offset = random.uniform(-0.01, 0.01)
            start_points.append(
                Coordinates(lat=origin.lat + lat_offset, lon=origin.lon + lon_offset)
            )

        with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
            # Load network once
            subset_table = proc.load_network(
                center=origin, buffer_radius=2000.0
            )  # Larger buffer for more points
            search_radius_m = 200.0

            # Measure performance over multiple runs
            times = []
            for _ in range(3):  # Fewer runs for large datasets
                start_time = time.perf_counter()

                result = proc.create_artificial_nodes_for_points(
                    start_points, subset_table, search_radius_m=search_radius_m
                )

                end_time = time.perf_counter()

                execution_time = (end_time - start_time) * 1000  # Convert to ms
                times.append(execution_time)

                # Verify result structure
                assert result is not None
                if isinstance(result, tuple):
                    # Handle tuple return (file_path, node_ids)
                    file_path, node_ids = result
                    assert isinstance(file_path, str)
                    assert file_path.endswith(".parquet")
                    assert isinstance(node_ids, list)
                else:
                    # Handle string return
                    assert isinstance(result, str)
                    assert result.endswith(".parquet")

            # Calculate statistics
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)

            # Adjust thresholds for larger datasets
            if num_points <= 100:
                threshold = 100  # 100ms for 100 points
            elif num_points <= 200:
                threshold = 300  # 300ms for 200 points
            else:
                threshold = 800  # 800ms for 500 points

            if avg_time < threshold * 0.5:
                status = "✅"
            elif avg_time < threshold:
                status = "✓"
            else:
                status = "⚠"

            logger.info(
                f"{status} Artificial node splitting ({num_points} points): {avg_time:.2f}ms avg (range: {min_time:.2f}-{max_time:.2f}ms, threshold: {threshold}ms)"
            )
