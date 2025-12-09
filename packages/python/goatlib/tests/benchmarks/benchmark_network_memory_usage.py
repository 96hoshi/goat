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
    print("🚀 Lightweight Network Processor: Performance Benchmark (Original)")
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
        stats = proc.get_network_stats()
        original_table = proc.network_table_name

        # Create a filtered network (matching original)
        filtered_table = proc._generate_table_name("filtered_network")
        proc.con.execute(f"""
            CREATE TABLE {filtered_table} AS 
            SELECT * FROM {original_table} WHERE length_m > 100
        """)
        stages.append(("After Filtering", get_memory_mb()))

        # Test edge splitting only (matching original)
        try:
            split_table, split_meta = proc.split_edge_at_point(
                latitude=48.13,
                longitude=11.58,
                # base_table=filtered_table,
            )
            stages.append(("After Edge Split", get_memory_mb()))
        except ValueError as e:
            print(f"Split operation failed: {e}")
            stages.append(("After Failed Split", get_memory_mb()))

        # Cleanup intermediate (matching original)
        stages.append(("After Intermediate Cleanup", get_memory_mb()))

    total_time_end = time.perf_counter()
    gc.collect()
    stages.append(("Final (After Full Cleanup)", get_memory_mb()))

    # Print all stages
    for stage_name, memory_data in stages:
        print_memory(stage_name, memory_data, baseline_memory)

    # Summary
    total_duration = total_time_end - total_time_start
    peak_rss = max(stage_data["rss"] for _, stage_data in stages)
    print("-" * 80)
    print("📊 Summary:")
    print(f"Total processing time: {total_duration:.3f} seconds")
    print(
        f"Peak Physical Memory (RSS) Increase: {peak_rss - baseline_memory['rss']:.1f} MB"
    )
    print(f"Processing Rate: {stats['edge_count'] / total_duration:,.0f} edges/second")
    print("=" * 80)


def run_full_benchmark(network_path: str | None = None):
    """Full benchmark including interpolation and advanced features."""
    # Get network path from conftest fixture location if not provided
    if network_path is None:
        network_path = str(
            Path(__file__).parent.parent / "data" / "network" / "network.parquet"
        )

    if not (PSUTIL_AVAILABLE and Path(network_path).exists()):
        print("psutil or network file not available. Aborting benchmark.")
        return

    print("=" * 80)
    print("🧠 Full Network Processor: Performance and Memory Benchmark")
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
        stats = proc.get_network_stats()
        original_table = proc.network_table_name

        # Create a filtered network
        filtered_table = proc._generate_table_name("filtered_network")
        proc.con.execute(f"""
            CREATE TABLE {filtered_table} AS 
            SELECT * FROM {original_table} WHERE length_m > 100
        """)
        stages.append(("After Filtering", get_memory_mb()))

        # Test edge splitting
        try:
            split_table, split_meta = proc.split_edge_at_point(
                latitude=48.13,
                longitude=11.58,
                base_table=filtered_table,
            )
            stages.append(("After Edge Split", get_memory_mb()))
        except ValueError as e:
            print(f"Split operation failed: {e}")
            stages.append(("After Failed Split", get_memory_mb()))

        # Test interpolation
        try:
            interp_table, interp_meta = proc.interpolate_long_edges(
                max_edge_length=200.0, base_table=original_table
            )
            stages.append(("After Interpolation", get_memory_mb()))
        except Exception as e:
            print(f"Interpolation failed: {e}")
            stages.append(("After Failed Interpolation", get_memory_mb()))
    total_time_end = time.perf_counter()
    gc.collect()
    stages.append(("Final (After Full Cleanup)", get_memory_mb()))

    # Print all stages
    for stage_name, memory_data in stages:
        print_memory(stage_name, memory_data, baseline_memory)

    # Summary
    total_duration = total_time_end - total_time_start
    peak_rss = max(stage_data["rss"] for _, stage_data in stages)
    print("-" * 80)
    print("📊 Summary:")
    print(f"Total processing time: {total_duration:.3f} seconds")
    print(
        f"Peak Physical Memory (RSS) Increase: {peak_rss - baseline_memory['rss']:.1f} MB"
    )
    print(f"Processing Rate: {stats['edge_count'] / total_duration:,.0f} edges/second")
    print("=" * 80)


# --- Pytest Version Using Conftest Fixture ---
def test_benchmark_with_fixture(network_file: Path):
    """Pytest version of the benchmark that uses the conftest network_file fixture."""
    if not PSUTIL_AVAILABLE:
        pytest.skip("psutil not available for memory monitoring")

    run_lightweight_benchmark(str(network_file))


if __name__ == "__main__":
    print("Running lightweight benchmark (matching original)...")
    run_lightweight_benchmark()

    print("\n" + "=" * 80 + "\n")

    print("Running full benchmark (with interpolation)...")
    run_full_benchmark()
