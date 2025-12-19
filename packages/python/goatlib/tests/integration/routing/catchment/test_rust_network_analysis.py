#!/usr/bin/env python3
import os
import time
from pathlib import Path

import fast_routing_py as routing


def test_analyze_rust_network_loading():
    """Detailed analysis of how the Rust library loads and processes networks"""

    # Use an existing network file from the recent run
    temp_dirs = [d for d in Path("/tmp").glob("routing_*") if d.is_dir()]
    network_files = []

    for temp_dir in temp_dirs:
        for parquet_file in temp_dir.glob("*.parquet"):
            network_files.append(parquet_file)

    if not network_files:
        print("No network files found. Please run the PT workflow first.")
        return

    # Use the most recent network file
    parquet_path = max(network_files, key=lambda p: p.stat().st_mtime)
    print(f"Using network file: {parquet_path}")

    # Check file size
    file_size = os.path.getsize(parquet_path) / (1024 * 1024)
    print(f"Parquet file size: {file_size:.2f}MB")

    # Now test Rust loading with detailed timing
    print("\nTesting Rust network loading...")

    # Test 1: Basic loading - repeat multiple times for accuracy
    load_times = []
    for i in range(3):
        start_time = time.time()
        network = routing.load_network(str(parquet_path))
        rust_load_time = time.time() - start_time
        load_times.append(rust_load_time)
        print(f"  Loading attempt {i+1}: {rust_load_time:.3f}s")

    avg_load_time = sum(load_times) / len(load_times)
    print(f"Average Rust network loading: {avg_load_time:.3f}s")

    # Test 2: Get network info
    start_time = time.time()
    try:
        info = network.get_network_info()
        info_time = time.time() - start_time
        print(f"Network info retrieval: {info_time:.3f}s")
        print(f"Network info: {info}")
    except Exception as e:
        print(f"Error getting network info: {e}")

    # Test 3: Get all node IDs
    start_time = time.time()
    try:
        node_ids = network.get_all_node_ids()
        node_ids_time = time.time() - start_time
        print(f"Node IDs retrieval: {node_ids_time:.3f}s")
        print(f"Total nodes from Rust: {len(node_ids)}")

        # Sample some node IDs
        if len(node_ids) > 0:
            print(f"Node ID range: {min(node_ids)} to {max(node_ids)}")
            print(
                f"Sample node IDs: {node_ids[:10] if len(node_ids) > 10 else node_ids}"
            )
    except Exception as e:
        print(f"Error getting node IDs: {e}")
        return

    # Test 4: Single isochrone calculation timing
    if len(node_ids) > 0:
        test_node = node_ids[len(node_ids) // 2]  # Use middle node

        # Test different time limits
        time_limits = [300, 600, 900]  # 5, 10, 15 minutes
        for limit in time_limits:
            start_time = time.time()
            try:
                result = network.calculate_isochrone(test_node, limit)
                calc_time = time.time() - start_time
                print(
                    f"Single isochrone ({limit//60}min): {calc_time:.3f}s, reached {len(result.nodes)} nodes"
                )
            except Exception as e:
                print(f"Error calculating single isochrone ({limit//60}min): {e}")

    # Test 5: Multiple isochrones calculation with different batch sizes
    if len(node_ids) > 10:
        batch_sizes = [1, 5, 10, 20]
        time_limit = 600  # 10 minutes

        for batch_size in batch_sizes:
            if batch_size > len(node_ids):
                continue

            test_nodes = node_ids[:batch_size]
            start_time = time.time()
            try:
                results = network.calculate_multiple_isochrones(test_nodes, time_limit)
                calc_time = time.time() - start_time
                avg_nodes = sum(len(r.nodes) for r in results) / len(results)
                time_per_node = calc_time / batch_size
                print(
                    f"Multiple isochrones (batch={batch_size}): {calc_time:.3f}s total, {time_per_node:.3f}s per node, avg {avg_nodes:.0f} reached"
                )
            except Exception as e:
                print(
                    f"Error calculating multiple isochrones (batch={batch_size}): {e}"
                )

    print("\nPerformance Summary:")
    print(f"  Average Rust loading: {avg_load_time:.3f}s")
    print(f"  File size: {file_size:.2f}MB")
    print(f"  Load time per MB: {avg_load_time / file_size:.3f} s/MB")
    print(f"  Total nodes: {len(node_ids)}")

    # Calculate throughput
    if len(node_ids) > 0:
        print(
            f"  Load time per 1000 nodes: {avg_load_time / len(node_ids) * 1000:.3f} s/1000nodes"
        )
