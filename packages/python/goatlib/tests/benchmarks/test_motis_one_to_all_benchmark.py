import tracemalloc

import psutil
from goatlib.routing.adapters.motis import create_motis_adapter
from goatlib.routing.schemas.base import AccessEgressMode, CatchmentAreaRoutingModePT
from goatlib.routing.schemas.catchment_area_transit import (
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaStartingPoints,
    TravelTimeCost,
)

from .conftest import BenchmarkMetrics, save_benchmark_results


class OneToAllBenchmarkMetrics(BenchmarkMetrics):
    """Class to track one-to-all performance metrics during test execution."""

    def record_response_stats(self, response, request):
        """Record response statistics."""
        polygon_count = len(response.polygons) if response.polygons else 0
        total_coordinates = 0

        for polygon in response.polygons:
            if polygon.geometry and polygon.geometry.get("coordinates"):
                # Count coordinates in the polygon
                coords = polygon.geometry["coordinates"]
                if coords and len(coords) > 0:
                    total_coordinates += len(coords[0])  # First ring

        self.response_stats = {
            "polygon_count": polygon_count,
            "total_coordinates": total_coordinates,
            "total_locations": response.metadata.get("total_locations", 0),
            "expected_cutoffs": len(request.travel_cost.cutoffs),
            "transit_modes": len(request.transit_modes),
        }


async def test_motis_one_to_all_performance_benchmark():
    """
    Comprehensive performance benchmark for MOTIS one-to-all functionality.

    Measures:
    - Pre-request preparation time
    - Network request time
    - Post-processing time
    - Memory allocation
    - Response data size
    """
    metrics = OneToAllBenchmarkMetrics()

    # Start memory tracing
    tracemalloc.start()

    try:
        # === PRE-REQUEST PHASE ===
        metrics.start_timing("pre_request")
        metrics.record_memory("pre_request_start")

        # Create adapter
        adapter = create_motis_adapter(use_fixtures=False)

        # Create request (Berlin with multiple cutoffs for substantial response)
        starting_points = TransitCatchmentAreaStartingPoints(
            lat=[52.5200],
            lon=[13.4050],  # Berlin center
        )
        request = TransitCatchmentAreaRequest(
            starting_points=starting_points,
            transit_modes=[
                CatchmentAreaRoutingModePT.bus,
                CatchmentAreaRoutingModePT.tram,
                CatchmentAreaRoutingModePT.subway,
                CatchmentAreaRoutingModePT.rail,
            ],
            access_mode=AccessEgressMode.walk,
            egress_mode=AccessEgressMode.walk,
            travel_cost=TravelTimeCost(
                max_traveltime=45,
                cutoffs=[15, 30, 45],  # Multiple cutoffs for larger response
            ),
        )

        metrics.record_memory("pre_request_end")
        metrics.end_timing("pre_request")

        # === REQUEST PHASE ===
        metrics.start_timing("request")
        metrics.record_memory("request_start")

        # Get network stats before request
        net_io_before = psutil.net_io_counters()

        # Execute the actual request
        response = await adapter.get_transit_catchment_area(request)

        # Get network stats after request
        net_io_after = psutil.net_io_counters()

        metrics.record_memory("request_end")
        metrics.end_timing("request")

        # Calculate network usage
        if net_io_before and net_io_after:
            metrics.network_stats = {
                "bytes_sent": net_io_after.bytes_sent - net_io_before.bytes_sent,
                "bytes_received": net_io_after.bytes_recv - net_io_before.bytes_recv,
                "packets_sent": net_io_after.packets_sent - net_io_before.packets_sent,
                "packets_received": net_io_after.packets_recv
                - net_io_before.packets_recv,
            }

        # === POST-PROCESSING PHASE ===
        metrics.start_timing("post_processing")
        metrics.record_memory("post_processing_start")

        # Validate response (simulating typical post-processing)
        assert response is not None
        assert len(response.polygons) == len(request.travel_cost.cutoffs)
        assert response.metadata.get("total_locations", 0) > 0

        # Validate each polygon geometry (typical validation work)
        for polygon in response.polygons:
            assert polygon.geometry is not None
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry
            if polygon.geometry["coordinates"]:
                coords = polygon.geometry["coordinates"][0]
                assert len(coords) >= 4  # Valid polygon
                assert coords[0] == coords[-1]  # Closed polygon

        metrics.record_memory("post_processing_end")
        metrics.end_timing("post_processing")

        # Record response statistics
        metrics.record_response_stats(response, request)

        # === SAVE RESULTS ===
        filepath = save_benchmark_results(metrics, "motis_one_to_all_performance")

        # === PRINT SUMMARY ===
        print("\n🚀 MOTIS One-to-All Performance Benchmark Results:")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        print("\n⏱️  Timing Breakdown:")
        print(
            f"   Pre-request:     {metrics.timings.get('pre_request_duration', 0):.3f}s"
        )
        print(f"   Request:         {metrics.timings.get('request_duration', 0):.3f}s")
        print(
            f"   Post-processing: {metrics.timings.get('post_processing_duration', 0):.3f}s"
        )
        print(
            f"   Total:           {sum(v for k, v in metrics.timings.items() if k.endswith('_duration')):.3f}s"
        )

        print("\n💾 Memory Usage:")
        for phase, mem in metrics.memory_usage.items():
            print(
                f"   {phase:15}: Current {mem['current_mb']:.1f}MB, Peak {mem['peak_mb']:.1f}MB, Process RSS {mem['process_rss_mb']:.1f}MB"
            )

        print("\n🌐 Network Stats:")
        net = metrics.network_stats
        print(f"   Bytes sent:      {net.get('bytes_sent', 0):,}")
        print(f"   Bytes received:  {net.get('bytes_received', 0):,}")
        print(
            f"   Total bandwidth: {(net.get('bytes_sent', 0) + net.get('bytes_received', 0)) / 1024:.1f} KB"
        )

        print("\n📊 Response Stats:")
        stats = metrics.response_stats
        print(f"   Polygons:        {stats['polygon_count']}")
        print(f"   Total coords:    {stats['total_coordinates']:,}")
        print(f"   Locations found: {stats['total_locations']:,}")
        print(f"   Transit modes:   {stats['transit_modes']}")

        # Performance assertions
        assert (
            metrics.timings.get("request_duration", 0) < 30.0
        ), "Request took too long"
        assert metrics.response_stats["polygon_count"] > 0, "No polygons generated"
        assert metrics.response_stats["total_locations"] > 0, "No locations found"

    finally:
        # Cleanup
        if "adapter" in locals():
            await adapter.motis_client.close()
        tracemalloc.stop()


async def test_motis_one_to_all_minimal_benchmark():
    """
    Minimal benchmark for quick performance checks.
    """
    metrics = OneToAllBenchmarkMetrics()
    tracemalloc.start()

    try:
        # Simple single cutoff request for baseline performance
        metrics.start_timing("total")

        adapter = create_motis_adapter(use_fixtures=False)

        request = TransitCatchmentAreaRequest(
            starting_points=TransitCatchmentAreaStartingPoints(
                lat=[52.5200], lon=[13.4050]
            ),
            transit_modes=[CatchmentAreaRoutingModePT.subway],
            access_mode=AccessEgressMode.walk,
            egress_mode=AccessEgressMode.walk,
            travel_cost=TravelTimeCost(
                max_traveltime=15,
                cutoffs=[15],
            ),
        )

        response = await adapter.get_transit_catchment_area(request)

        metrics.end_timing("total")
        metrics.record_memory("final")
        metrics.record_response_stats(response, request)

        # Save minimal results
        save_benchmark_results(metrics, "motis_one_to_all_minimal")

        print(
            f"\n⚡ Minimal Benchmark: {metrics.timings.get('total_duration', 0):.3f}s"
        )
        print(f"   Found {metrics.response_stats['total_locations']} locations")
        print(f"   Memory peak: {metrics.memory_usage['final']['peak_mb']:.1f}MB")

        # Quick assertions
        assert (
            metrics.timings.get("total_duration", 0) < 10.0
        ), "Minimal request too slow"
        assert (
            metrics.response_stats["polygon_count"] == 1
        ), "Should generate exactly 1 polygon"

    finally:
        if "adapter" in locals():
            await adapter.motis_client.close()
        tracemalloc.stop()
