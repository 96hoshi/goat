import logging
import os
from pathlib import Path

import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.adapters.motis import create_motis_adapter
from goatlib.routing.schemas.base import (
    CatchmentAreaRoutingModePT,
    Coordinates,
)
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    TransitCatchmentAreaRequest,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.network
async def test_complete_motis_rust_workflow():
    """Complete MOTIS + Rust workflow using the correct adapter interface."""

    logger.info("🚀 Starting complete MOTIS + Rust workflow...")

    # Test data path
    test_file = Path("/app/packages/python/goatlib/tests/data/network/network.parquet")
    if not test_file.exists():
        pytest.skip(f"Test file not found: {test_file}")

    # Step 1: Use MOTIS adapter directly with the interface
    logger.info("📡 Step 1: Getting transit catchment area from MOTIS...")
    center = Coordinates(lat=48.1351, lon=11.5820)  # Munich center
    motis_request = TransitCatchmentAreaRequest(
        starting_points=[center],  # Munich
        transit_modes=[
            CatchmentAreaRoutingModePT.rail,
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.tram,
        ],
        cutoffs=[15],  # 15 minutes
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
        logger.info(f"🔄 MOTIS request: {motis_req}")

        raw_motis_response = await adapter.motis_client.one_to_all(motis_req)
        logger.info("✅ MOTIS raw response received")

        # Extract stations from the raw response
        raw_stations = extract_bus_stations_for_buffering(raw_motis_response)
        logger.info(f"📍 Extracted {len(raw_stations)} transit stations")

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

    except Exception as e:
        await adapter.motis_client.close()
        logger.error(f"MOTIS API error: {e}")
        pytest.skip(f"MOTIS API unavailable: {e}")

    await adapter.motis_client.close()

    # Step 2: Process with Rust routing using the fast functions
    logger.info("⚙️ Step 2: Testing Rust routing on transit stations...")

    successful_routing = 0
    total_reachable = 0

    with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
        # Load network around Munich - larger area to ensure we catch stations
        subset_table = proc.load_network(
            center=center, buffer_radius=15000.0
        )  # 15km radius
        logger.info(f"📊 Loaded network subset: {subset_table}")

        # Process all stations in batch using calculate_multiple_isochrones
        station_coordinates = [
            Coordinates(lat=station["lat"], lon=station["lon"])
            for station in stations_data
        ]
        logger.info(f"🔧 Processing {len(station_coordinates)} stations in batch...")

        try:
            # Create artificial nodes for all stations at once
            result = proc.create_artificial_nodes_for_points(
                station_coordinates, subset_table, search_radius_m=500.0
            )

            if isinstance(result, tuple):
                output_path, artificial_node_ids = result
                logger.info(
                    f"📄 Created: {len(artificial_node_ids)} artificial nodes for {len(station_coordinates)} stations"
                )

                # Use batch routing with calculate_multiple_isochrones
                try:
                    import fast_routing_py as routing

                    network = routing.load_network(output_path)
                    max_cost = 5  # 5 minutes

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

                    # Cleanup
                    if os.path.exists(output_path):
                        os.unlink(output_path)

                except Exception as e:
                    logger.warning(f"❌ Batch routing failed: {e}")
                    if os.path.exists(output_path):
                        os.unlink(output_path)
            else:
                logger.warning("⚠️ Network processing failed")

        except Exception as e:
            logger.warning(f"❌ Station processing failed: {e}")

    # Step 3: Results
    success_rate = (
        (successful_routing / len(stations_data) * 100) if stations_data else 0
    )
    logger.info(f"   MOTIS stations found: {len(stations_data)}")
    logger.info(f"   Stations tested: {len(stations_data)}")
    logger.info(f"   Successful routing: {successful_routing}")
    logger.info(f"   Success rate: {success_rate:.1f}%")
    logger.info(f"   Total reachable nodes: {total_reachable:,}")

    # Assertions
    assert len(stations_data) > 0, "Should find transit stations from MOTIS"
    assert (
        successful_routing > 0
    ), "Should successfully route from at least some stations"
    assert total_reachable > 0, "Should find reachable nodes"

    logger.info("✅ Complete MOTIS + Rust workflow successful!")


def test_routing_compatibility():
    """Test that artificial nodes output can be loaded by the Rust routing library."""

    # Test data path
    test_file = Path("/app/packages/python/goatlib/tests/data/network/network.parquet")

    if not test_file.exists():
        logger.info(f"❌ Test file not found: {test_file}")
        pytest.fail("Routing compatibility test failed: test file missing")

    logger.info("🧪 Testing routing compatibility...")

    try:
        # Create some test points around Munich
        origin = Coordinates(lat=48.1351, lon=11.5820)
        start_points = [
            Coordinates(lat=origin.lat + i * 0.001, lon=origin.lon + i * 0.001)
            for i in range(10)  # Test with 10 points
        ]

        logger.info(f"📍 Testing with {len(start_points)} points around Munich")

        with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
            # Load network subset
            subset_table = proc.load_network(center=origin, buffer_radius=1000.0)
            logger.info(f"📊 Loaded network subset: {subset_table}")

            # Create artificial nodes
            logger.info("🔧 Creating artificial nodes...")
            result = proc.create_artificial_nodes_for_points(
                start_points, subset_table, search_radius_m=200.0
            )

            if isinstance(result, tuple):
                output_path, artificial_node_ids = result
                logger.info(" Created artificial nodes:")
                logger.info(f"   📄 File: {output_path}")
                logger.info(f"   🔢 Node IDs: {len(artificial_node_ids)} nodes")
                logger.info(f"   🆔 First few IDs: {artificial_node_ids[:5]}")
            else:
                output_path = result
                artificial_node_ids = None
                logger.info(f"✅ Created network file: {output_path}")

            # Verify file exists and has content
            if not os.path.exists(output_path):
                logger.info(f"❌ Output file not found: {output_path}")
                pytest.fail("Routing compatibility test failed: output file missing")

            file_size = os.path.getsize(output_path) / 1024  # KB
            logger.info(f"📏 File size: {file_size:.1f} KB")

            # Try to import the routing library
            try:
                import fast_routing_py as routing

                # Test loading the network
                logger.info("🔌 Loading network into routing library...")
                network = routing.load_network(output_path)
                logger.info("✅ Network loaded successfully into routing library!")

                # If we have artificial node IDs, test routing
                if artificial_node_ids and len(artificial_node_ids) > 0:
                    logger.info("🧭 Testing routing calculation...")

                    # Test with first artificial node
                    start_node = artificial_node_ids[0]
                    max_cost = 300  # 5 minutes in seconds
                    logging.info(
                        f"   Calculating isochrone from node {start_node} with max cost {max_cost}s..."
                    )
                    result = network.calculate_isochrone_multiple_times(
                        start_node=start_node, time_thresholds=[max_cost]
                    )

                    if result and len(result) > 0:
                        logger.info(
                            f"   📈 Reachable nodes: {result[0].reachable_nodes}"
                        )
                        logger.info(f"   ⏱️  Max cost: {max_cost}s")
                    else:
                        logger.info("⚠️  Routing returned empty result")

            except ImportError as e:
                logger.info(f"⚠️  Could not import fast_routing_py: {e}")
                pytest.fail(f"Routing compatibility test failed: {e}")

            except Exception as e:
                logger.info(f"❌ Error testing routing library: {e}")
                pytest.fail(f"Routing compatibility test failed: {e}")

    except Exception as e:
        logger.info(f"❌ Test failed: {e}")
        import traceback

        traceback.logger.info_exc()
        pytest.fail(f"Routing compatibility test failed: {e}")
