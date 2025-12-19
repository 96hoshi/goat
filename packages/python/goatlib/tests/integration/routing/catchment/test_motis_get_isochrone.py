import logging
import time
from pathlib import Path

import fast_routing_py as routing_rs
import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.adapters.motis import create_motis_adapter
from goatlib.routing.adapters.motis.motis_adapter import (
    MotisPlanApiAdapter,
)
from goatlib.routing.schemas.base import (
    CatchmentAreaRoutingModePT,
    CatchmentAreaType,
    Coordinates,
)
from goatlib.routing.schemas.catchment import CatchmentRequest
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    TransitCatchmentAreaRequest,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.network
async def test_get_isochrone(
    motis_adapter_online: MotisPlanApiAdapter,
):
    request = CatchmentRequest(
        starting_points=[Coordinates(lat=48.1351, lon=11.5820)],  # Munich center
        cutoffs=[15, 30],  # 15 and 30-minute isochrones
        transit_modes=[
            CatchmentAreaRoutingModePT.rail,
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.tram,
        ],
        access_settings=AccessEgressSettings.create_walk_settings(max_time=10),
        egress_settings=AccessEgressSettings.create_walk_settings(max_time=15),
        type=CatchmentAreaType.polygon,
    )
    response = await motis_adapter_online.get_isochrone(request)

    assert response.results
    r = response.results[0]

    assert r.pt_stations_found > 0
    assert r.successful_routing > 0
    assert r.total_reachable_nodes > 0


@pytest.mark.asyncio
@pytest.mark.network
async def test_complete_motis_rust_workflow(network_file: Path):
    """Complete MOTIS + Rust workflow using the correct adapter interface."""

    # Test data path

    if not network_file.exists():
        logger.info(f"❌ Test file not found: {network_file}")
        pytest.skip(f"Test file not found: {network_file}")

    # Step 1: Use MOTIS adapter directly with the interface
    center = Coordinates(lat=48.1351, lon=11.5820)  # Munich center
    motis_request = TransitCatchmentAreaRequest(
        starting_points=[center],  # Munich
        transit_modes=[
            CatchmentAreaRoutingModePT.rail,
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.tram,
        ],
        cutoffs=[5],  # 5 minutes
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    # Use the MOTIS client directly to get raw station data
    adapter = create_motis_adapter()
    try:
        # Get raw MOTIS data using the converter functions
        from goatlib.routing.adapters.motis.motis_converters import (
            extract_bus_stations_for_buffering,
            translate_to_motis_one_to_all_request,
        )

        # Convert our request to MOTIS format and call directly
        motis_req = translate_to_motis_one_to_all_request(motis_request)
        logger.info(f"MOTIS request: {motis_req}")

        raw_motis_response = await adapter.motis_client.one_to_all(motis_req)
        logger.info("MOTIS raw response received")

        # Extract stations from the raw response
        raw_stations = extract_bus_stations_for_buffering(raw_motis_response)
        logger.info(f"Extracted {len(raw_stations)} transit stations")

        if not raw_stations:
            logger.info("❌ No stations found in MOTIS response")
            pytest.skip("No station data from MOTIS")

        # Show first few stations for debugging
        for i, station in enumerate(raw_stations):
            coords = station["coordinates"]  # [lon, lat]
            logger.info(
                f"   Station {i+1}: {station.get('name', 'Unknown')} at [{coords[1]:.4f}, {coords[0]:.4f}]"
            )

        # Convert to our format
        stations_data = []
        for station in raw_stations:
            coords = station["coordinates"]  # [lon, lat]
            stations_data.append(
                {
                    "name": station.get("name", "Unknown"),
                    "lat": coords[1],  # latitude
                    "lon": coords[0],  # longitude
                    "transit_time": station.get("duration_minutes", 0),
                }
            )
        # final_response = CatchmentResponse(last_mile_catchment=stations_data)

    except Exception as e:
        await adapter.motis_client.close()
        logger.error(f"MOTIS API error: {e}")
        pytest.skip(f"MOTIS API unavailable: {e}")

    # Step 2: Process with Rust routing using the fast functions
    successful_routing = 0
    total_reachable = 0

    with InMemoryNetworkProcessor(input_path=str(network_file)) as proc:
        # Load network around Munich - larger area to ensure we catch stations
        subset_table = proc.load_network(
            center=center, buffer_radius=15000.0
        )  # 15km radius
        logger.info(f"Loaded network subset: {subset_table}")

        # Process all stations in batch using calculate_multiple_isochrones
        station_coordinates = [
            Coordinates(lat=station["lat"], lon=station["lon"])
            for station in stations_data
        ]

        try:
            # Create artificial nodes for all stations at once
            result = proc.create_artificial_nodes_for_points(
                station_coordinates, subset_table, search_radius_m=500.0
            )

            if isinstance(result, tuple):
                output_path, artificial_node_ids = result
                logger.info(
                    f"Created: {len(artificial_node_ids)} artificial nodes for {len(station_coordinates)} stations"
                )

                # Use batch routing with calculate_multiple_isochrones

                import fast_routing_py as routing

                network = routing.load_network(output_path)
                max_cost = 300  # 5 minutes in seconds

                # Use calculate_multiple_isochrones for all stations at once
                routing_results = network.calculate_multiple_isochrones(
                    start_nodes=artificial_node_ids, max_cost=max_cost
                )

                # Process results
                for i, routing_result in enumerate(routing_results):
                    if i < len(stations_data):
                        station = stations_data[i]
                        reachable = routing_result.reachable_nodes
                        successful_routing += 1
                        total_reachable += reachable
            else:
                logger.warning("⚠️ Network processing failed")

        except Exception as e:
            logger.warning(f"❌ Station processing failed: {e}")

    # Step 3: Results
    success_rate = (
        (successful_routing / len(stations_data) * 100) if stations_data else 0
    )
    logger.info(f"   MOTIS stations found: {len(stations_data)}")
    logger.info(f"   Successful routing: {successful_routing}")
    logger.info(f"   Success rate: {success_rate:.1f}%")
    logger.info(f"   Total reachable nodes: {total_reachable:,}")

    # Assertions
    assert len(stations_data) > 0, "Should find transit stations from MOTIS"
    assert (
        successful_routing > 0
    ), "Should successfully route from at least some stations"
    assert total_reachable > 0, "Should find reachable nodes"


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
        network = routing_rs.load_network(parquet_path)

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
        network = routing_rs.load_network(parquet_path)

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
