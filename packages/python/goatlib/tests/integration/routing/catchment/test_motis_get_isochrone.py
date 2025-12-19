# tests/adapters/test_motis_adapter_e2e.py

import json
import logging

import pytest
from goatlib.routing.adapters.motis.motis_adapter import (
    MotisPlanApiAdapter,
)
from goatlib.routing.schemas.base import (
    CatchmentAreaRoutingModePT,
    CatchmentAreaType,
    Coordinates,
)
from goatlib.routing.schemas.catchment import CatchmentRequest, CatchmentResponse
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
)

# Setup logger
logger = logging.getLogger(__name__)

# Assume your test data file is located here relative to your project root
TEST_NETWORK_PATH = "packages/python/goatlib/tests/data/network/network.parquet"

# --- End-to-End Test ---


@pytest.mark.network  # Mark this test as requiring the network
@pytest.mark.asyncio
async def test_get_isochrone_live_e2e_chained_workflow(
    motis_adapter_online: MotisPlanApiAdapter, mocker
):
    """
    Tests the full chained workflow against a live MOTIS API and the real Rust engine.
    This test will fail if the MOTIS API is unavailable or if the local network
    data doesn't cover the requested area (Munich).
    """
    # Arrange
    # We still spy on os.unlink to ensure cleanup happens correctly.
    mock_unlink = mocker.patch("os.unlink")

    # A realistic request for Munich, which should yield results
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

    # Act
    logger.info("Sending live E2E request to MOTIS and local Rust engine...")
    try:
        response = await motis_adapter_online.get_isochrone(request)
        # Close the client session after the test is done
        await motis_adapter_online.motis_client.close()
    except Exception as e:
        await motis_adapter_online.motis_client.close()
        pytest.fail(f"The live get_isochrone call failed with an exception: {e}")

    logger.info(f"Received live response: {response.dict()}")

    # Assert
    # 1. Assertions on the response structure
    assert isinstance(response, CatchmentResponse)
    assert len(response.results) == 2, "Should have one result per cutoff"
    assert (
        mock_unlink.call_count > 0
    ), "Temporary graph files should have been created and cleaned up"

    # 2. Assertions on the 15-minute cutoff result
    # These are "behavioral" assertions, not hardcoded numbers.
    result_15_min = response.results[0]
    assert result_15_min.cutoff_minutes == 15
    assert result_15_min.pt_stations_found is not None
    assert (
        result_15_min.pt_stations_found > 0
    ), "MOTIS should have found at least one station within 15 mins"
    assert (
        result_15_min.last_mile_walkshed_nodes > 0
    ), "Rust engine should have found reachable nodes from the stations"

    # 3. Assertions on the 30-minute cutoff result
    result_30_min = response.results[1]
    assert result_30_min.cutoff_minutes == 30
    assert result_30_min.pt_stations_found is not None
    assert (
        result_30_min.pt_stations_found >= result_15_min.pt_stations_found
    ), "30-min cutoff should find at least as many stations as 15-min"
    assert (
        result_30_min.last_mile_walkshed_nodes >= result_15_min.last_mile_walkshed_nodes
    ), "30-min cutoff should cover a larger or equal area"
    # We can be more confident that the 30-min result is strictly larger
    assert result_30_min.pt_stations_found > 0
    assert result_30_min.last_mile_walkshed_nodes > 0


import json


def get_all_attributes(obj):
    """Get all attributes of an object."""
    attrs = {}

    # Get __dict__ attributes
    if hasattr(obj, "__dict__"):
        for k, v in obj.__dict__.items():
            attrs[k] = v

    # Get properties via getattr
    for attr_name in dir(obj):
        if not attr_name.startswith("_"):
            try:
                attr_value = getattr(obj, attr_name)
                if not callable(attr_value) and attr_name not in attrs:
                    attrs[attr_name] = attr_value
            except:
                attrs[attr_name] = "<Error accessing>"

    return attrs


@pytest.mark.asyncio
@pytest.mark.network
async def test_complete_motis_rust_workflow(
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
