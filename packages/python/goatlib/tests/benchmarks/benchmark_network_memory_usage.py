import gc
import logging
import time
from pathlib import Path

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.schemas.base import Coordinates

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


# --- Helper Functions ---
def get_memory_mb() -> dict[str, float]:
    process = psutil.Process()
    mem_info = process.memory_info()
    return {"rss": mem_info.rss / (1024**2), "vms": mem_info.vms / (1024**2)}


def print_memory(
    stage: str, current: dict[str, float], baseline: dict[str, float]
) -> None:
    rss_delta = current["rss"] - baseline["rss"]
    vms_delta = current["vms"] - baseline["vms"]
    print(
        f"{stage:<28} | RSS: {current['rss']:>7.1f} MB (+{rss_delta:6.1f}) | VMS: {current['vms']:>8.1f} MB (+{vms_delta:7.1f})"
    )


# --- Main Benchmark ---
def run_lightweight_benchmark(network_path: str | None = None) -> None:
    """Lightweight benchmark matching the original performance test."""
    # Get network path from conftest fixture location if not provided
    if network_path is None:
        network_path = str(
            Path(__file__).parent.parent / "data" / "network" / "network.parquet"
        )

    if not (PSUTIL_AVAILABLE and Path(network_path).exists()):
        print("psutil or network file not available. Aborting benchmark.")
        return

    print("=" * 80)
    print("🚀 Lightweight Network Processor: Performance Benchmark")
    print("=" * 80)

    gc.collect()
    baseline_memory = get_memory_mb()
    print(
        f"Baseline                     | RSS: {baseline_memory['rss']:>7.1f} MB          | VMS: {baseline_memory['vms']:>8.1f} MB"
    )

    stages = []
    total_time_start = time.perf_counter()

    with InMemoryNetworkProcessor(network_path) as proc:
        stages.append(("After Loading", get_memory_mb()))

        # Load network (replaces _generate_table_name)
        center = Coordinates(lat=48.1351, lon=11.5820)
        subset_table = proc.load_network(center=center, buffer_radius=2000)
        stages.append(("After Subset Creation", get_memory_mb()))

        stats = proc.get_network_stats(subset_table)

        # Test edge splitting
        try:
            split_point = Coordinates(lat=48.1370, lon=11.5760)
            split_table, split_meta = proc.split_edge_at_point(
                point=split_point,
                source_table=subset_table,
                max_search_radius_m=100.0,
            )
            stages.append(("After Edge Split", get_memory_mb()))

            # Verify split worked
            split_stats = proc.get_network_stats(split_table)
            assert split_stats["edge_count"] == stats["edge_count"] + 1

        except ValueError as e:
            print(f"Split operation failed: {e}")
            stages.append(("After Failed Split", get_memory_mb()))

    total_time_end = time.perf_counter()
    gc.collect()
    stages.append(("Final (After Context Exit)", get_memory_mb()))

    # Print all stages
    for stage_name, memory_data in stages:
        print_memory(stage_name, memory_data, baseline_memory)

    # Summary
    total_duration = total_time_end - total_time_start
    peak_rss = max(stage_data["rss"] for _, stage_data in stages)
    print("-" * 80)
    print("📊 Summary:")
    print(f"Total processing time: {total_duration:.3f} seconds")
    print(f"Network size: {stats['edge_count']:,} edges")
    print(
        f"Peak Physical Memory (RSS) Increase: {peak_rss - baseline_memory['rss']:.1f} MB"
    )
    print(f"Processing Rate: {stats['edge_count'] / total_duration:,.0f} edges/second")
    print("=" * 80)


def run_full_benchmark(network_path: str | None = None):
    """Full benchmark including interpolation and advanced features."""
    if network_path is None:
        network_path = str(
            Path(__file__).parent.parent / "data" / "network" / "network.parquet"
        )

    if not (PSUTIL_AVAILABLE and Path(network_path).exists()):
        print("psutil or network file not available. Aborting benchmark.")
        return

    print("=" * 80)
    print("🧠 Full Network Processor: Complete Workflow Benchmark")
    print("=" * 80)

    gc.collect()
    baseline_memory = get_memory_mb()
    print(
        f"Baseline                     | RSS: {baseline_memory['rss']:>7.1f} MB          | VMS: {baseline_memory['vms']:>8.1f} MB"
    )

    stages = []
    total_time_start = time.perf_counter()

    with InMemoryNetworkProcessor(network_path) as proc:
        stages.append(("After Loading", get_memory_mb()))

        # 1. Load network subset
        center = Coordinates(lat=48.1351, lon=11.5820)
        subset_table = proc.load_network(center=center, buffer_radius=5000)
        subset_stats = proc.get_network_stats(subset_table)
        stages.append(("After Subset Creation", get_memory_mb()))
        print(f"📊 Subset: {subset_stats['edge_count']:,} edges")

        # 2. Split edge at point
        split_point = Coordinates(lat=48.1370, lon=11.5760)
        split_table, split_meta = proc.split_edge_at_point(
            point=split_point,
            source_table=subset_table,
            max_search_radius_m=100.0,
        )
        split_stats = proc.get_network_stats(split_table)
        stages.append(("After Edge Split", get_memory_mb()))
        print(f"✂️  Split: {split_stats['edge_count']:,} edges (+1)")

        # 3. Interpolate long edges
        interp_table, interp_meta = proc.interpolate_long_edges(
            max_edge_length=50.0,
            base_table=split_table,
            include_stats=True,
        )
        interp_stats = proc.get_network_stats(interp_table)
        stages.append(("After Interpolation", get_memory_mb()))
        print(f"📐 Interpolated: {interp_stats['edge_count']:,} edges")

        # 4. Test split with subset (combined operation)
        combined_table, combined_meta = proc.split_edge_at_point_with_subset(
            point=Coordinates(lat=48.1360, lon=11.5770),
            network_buffer_radius=1000.0,
            max_search_radius_m=50.0,
        )
        stages.append(("After Combined Split", get_memory_mb()))

        # 5. Test apply_sql_query
        sql_table = proc.apply_sql_query(
            sql_query=f"""
            SELECT * 
            FROM {interp_table} 
            WHERE length_m > 30 
            AND edge_id NOT LIKE '%_s%'
            """,
            result_table="filtered_long",
        )
        stages.append(("After SQL Query", get_memory_mb()))

    total_time_end = time.perf_counter()
    gc.collect()
    stages.append(("Final (After Cleanup)", get_memory_mb()))

    # Print all stages
    for stage_name, memory_data in stages:
        print_memory(stage_name, memory_data, baseline_memory)

    # Summary
    total_duration = total_time_end - total_time_start
    peak_rss = max(stage_data["rss"] for _, stage_data in stages)
    print("-" * 80)
    print("📊 Summary:")
    print(f"Total processing time: {total_duration:.3f} seconds")
    print(f"Original subset: {subset_stats['edge_count']:,} edges")
    print(f"Final interpolated: {interp_stats['edge_count']:,} edges")
    print(
        f"Edge increase: {interp_stats['edge_count'] - subset_stats['edge_count']:,} edges"
    )
    print(
        f"Peak Physical Memory (RSS) Increase: {peak_rss - baseline_memory['rss']:.1f} MB"
    )
    print(f"Operations/second: {5 / total_duration:.1f} ops/sec")  # 5 main operations
    print("=" * 80)


def run_performance_stress_test(network_path: str | None = None):
    """Stress test with multiple operations."""
    if network_path is None:
        network_path = str(
            Path(__file__).parent.parent / "data" / "network" / "network.parquet"
        )

    if not (PSUTIL_AVAILABLE and Path(network_path).exists()):
        print("psutil or network file not available. Aborting benchmark.")
        return

    print("=" * 80)
    print("⚡ Network Processor: Stress Test (Multiple Operations)")
    print("=" * 80)

    gc.collect()
    baseline_memory = get_memory_mb()

    with InMemoryNetworkProcessor(network_path) as proc:
        center = Coordinates(lat=48.1351, lon=11.5820)

        # Create multiple subsets
        tables = []
        start = time.perf_counter()

        for i in range(3):
            # Vary buffer sizes
            table = proc.load_network(center=center, buffer_radius=1000 + i * 2000)
            tables.append(table)

            # Split at slightly different points
            split_point = Coordinates(
                lat=48.1351 + (i * 0.001), lon=11.5820 + (i * 0.001)
            )
            split_table, _ = proc.split_edge_at_point(
                point=split_point,
                source_table=table,
                max_search_radius_m=50.0,
            )
            tables.append(split_table)

            # Interpolate with different thresholds
            interp_table, _ = proc.interpolate_long_edges(
                max_edge_length=30.0 + (i * 20),
                base_table=split_table,
                include_stats=False,
            )
            tables.append(interp_table)

        end = time.perf_counter()

        print(f"Created {len(tables)} tables in {end - start:.2f}s")
        print(f"Average: {(end - start) / len(tables):.3f}s per table")

        # Memory after many operations
        current_memory = get_memory_mb()
        print_memory("After Stress Test", current_memory, baseline_memory)

    print("✅ Stress test completed - all tables should be cleaned up")


# --- Pytest Version Using Conftest Fixture ---
def test_benchmark_with_fixture(network_file: Path):
    """Pytest version of the benchmark that uses the conftest network_file fixture."""
    if not PSUTIL_AVAILABLE:
        pytest.skip("psutil not available for memory monitoring")

    run_lightweight_benchmark(str(network_file))


def test_full_benchmark_with_fixture(network_file: Path):
    """Full benchmark test."""
    if not PSUTIL_AVAILABLE:
        pytest.skip("psutil not available for memory monitoring")

    run_full_benchmark(str(network_file))


def test_table_tracking_benchmark(network_file: Path):
    """Test table tracking and cleanup."""
    if not PSUTIL_AVAILABLE:
        pytest.skip("psutil not available for memory monitoring")

    # Get initial table count
    with InMemoryNetworkProcessor(str(network_file)) as proc:
        initial_tables = proc.get_application_tables()
        print(f"Initial tables: {len(initial_tables)}")

        # Create tables
        center = Coordinates(lat=48.1351, lon=11.5820)
        table1 = proc.load_network(center=center, buffer_radius=1000)
        table2, _ = proc.split_edge_at_point(
            point=center,
            source_table=table1,
            max_search_radius_m=100.0,
        )

        # Memory usage
        mem = get_memory_mb()
        print(f"Memory with 2 tables: {mem['rss']:.1f} MB RSS")

    # After context exit, tables should be cleaned
    # (Can't verify without new connection, but memory should drop)
    final_mem = get_memory_mb()
    print(f"Final memory after cleanup: {final_mem['rss']:.1f} MB RSS")


if __name__ == "__main__":
    print("Running lightweight benchmark...")
    run_lightweight_benchmark()

    print("\n" + "=" * 80 + "\n")

    print("Running full workflow benchmark...")
    run_full_benchmark()

    print("\n" + "=" * 80 + "\n")

    print("Running stress test...")
    run_performance_stress_test()
