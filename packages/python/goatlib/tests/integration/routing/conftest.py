from typing import AsyncGenerator

import pytest_asyncio
from goatlib.routing.adapters.motis import MotisPlanApiAdapter, create_motis_adapter
from goatlib.routing.schemas.base import CatchmentAreaRoutingModePT
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    TransitCatchmentAreaRequest,
)


@pytest_asyncio.fixture
async def motis_adapter_online() -> AsyncGenerator[MotisPlanApiAdapter, None]:
    """
    MOTIS adapter for online integration testing.

    This adapter:
    - Makes real HTTP requests to api.transitous.org
    - Should be used for tests that need real API validation
    """
    adapter = create_motis_adapter()
    yield adapter
    await adapter.motis_client.close()


# Common test data fixtures for one-to-all testing
@pytest_asyncio.fixture
def berlin_request() -> TransitCatchmentAreaRequest:
    """Create a standard Berlin transit catchment area request."""
    return TransitCatchmentAreaRequest(
        starting_points=[
            {"lat": 52.5200, "lon": 13.4050}  # Berlin center
        ],
        transit_modes=[
            CatchmentAreaRoutingModePT.bus,
            CatchmentAreaRoutingModePT.tram,
            CatchmentAreaRoutingModePT.subway,
        ],
        cutoffs=[15, 30],  # 15 and 30 minute isochrones
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )


@pytest_asyncio.fixture
def munich_request() -> TransitCatchmentAreaRequest:
    """Create a Munich transit catchment area request for testing."""
    return TransitCatchmentAreaRequest(
        starting_points=[
            {"lat": 48.1351, "lon": 11.5820}  # Munich center
        ],
        transit_modes=[
            CatchmentAreaRoutingModePT.rail,
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.tram,
        ],
        cutoffs=[15, 30, 45],  # Three isochrone bands
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )


@pytest_asyncio.fixture
def simple_berlin_request() -> TransitCatchmentAreaRequest:
    """Create a simple Berlin request for minimal testing."""
    return TransitCatchmentAreaRequest(
        starting_points=[
            {"lat": 52.5200, "lon": 13.4050}  # Berlin
        ],
        transit_modes=[CatchmentAreaRoutingModePT.subway],
        cutoffs=[15],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )
