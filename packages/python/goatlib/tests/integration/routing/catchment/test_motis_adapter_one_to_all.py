import logging

import pytest
from goatlib.routing.adapters.motis.motis_adapter import create_motis_adapter
from goatlib.routing.schemas.base import (
    AccessEgressMode,
    CatchmentAreaRoutingModePT,
    Coordinates,
)
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    TransitCatchmentAreaRequest,
)

logger = logging.getLogger(__name__)


async def test_basic_one_to_all_success():
    """Test basic one-to-all functionality returns valid catchment areas."""
    adapter = create_motis_adapter()

    berlin_request = TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=52.520008, lon=13.404954)],  # Berlin center
        cutoffs=[15, 30],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        access_settings=AccessEgressSettings(
            mode=AccessEgressMode.walk, max_time=10, speed=5.0
        ),
        egress_settings=AccessEgressSettings(
            mode=AccessEgressMode.walk, max_time=10, speed=5.0
        ),
    )

    async with adapter.motis_client:
        response = await adapter._get_transit_catchment_area(berlin_request)

    # Basic structure checks
    assert response is not None
    assert len(response.polygons) == len(berlin_request.cutoffs)
    assert response.metadata.get("total_locations", 0) > 0
    assert response.metadata.get("source") == "motis_one_to_all"

    # Check each polygon
    for polygon in response.polygons:
        assert polygon.travel_time in berlin_request.cutoffs
        assert hasattr(polygon, "points")
        assert isinstance(polygon.points, list)

        # Geometry may be None initially, can be generated from points
        if polygon.geometry is not None:
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry
        else:
            # Test that geometry can be generated from points
            polygon.set_geometry_from_points()
            if polygon.points:  # Only check if there are points
                assert polygon.geometry["type"] == "Polygon"
                assert "coordinates" in polygon.geometry


async def test_multiple_cutoffs():
    """Test that multiple travel time cutoffs generate correct polygons."""
    adapter = create_motis_adapter()

    munich_request = TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=48.137154, lon=11.576124)],  # Munich center
        cutoffs=[10, 20, 30],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        access_settings=AccessEgressSettings(
            mode=AccessEgressMode.walk, max_time=10, speed=5.0
        ),
        egress_settings=AccessEgressSettings(
            mode=AccessEgressMode.walk, max_time=10, speed=5.0
        ),
    )

    async with adapter.motis_client:
        response = await adapter._get_transit_catchment_area(munich_request)

    assert len(response.polygons) == len(munich_request.cutoffs)

    # Polygons should be ordered by travel time
    travel_times = [p.travel_time for p in response.polygons]
    assert sorted(travel_times) == sorted(munich_request.cutoffs)


async def test_different_transit_modes(motis_adapter_online):
    """Test different combinations of transit modes."""
    rail_only_request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[CatchmentAreaRoutingModePT.rail],
        cutoffs=[20],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    response = await motis_adapter_online._get_transit_catchment_area(rail_only_request)

    assert len(response.polygons) == 1


async def test_single_cutoff(motis_adapter_online):
    """Test with a single travel time cutoff."""
    single_cutoff_request = TransitCatchmentAreaRequest(
        starting_points=[
            {"lat": 48.1351, "lon": 11.5820}  # Munich
        ],
        transit_modes=[
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.tram,
        ],
        cutoffs=[20],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    response = await motis_adapter_online._get_transit_catchment_area(
        single_cutoff_request
    )

    assert len(response.polygons) == 1
    assert response.polygons[0].travel_time == 20


async def test_geometry_structure(motis_adapter_online, berlin_request):
    """Test that returned geometry has correct GeoJSON structure."""
    response = await motis_adapter_online._get_transit_catchment_area(berlin_request)

    for polygon in response.polygons:
        # Check that polygon has points field
        assert hasattr(polygon, "points")
        assert isinstance(polygon.points, list)

        # If geometry is None, generate it from points for testing
        if polygon.geometry is None:
            polygon.set_geometry_from_points()

        # Now test the geometry structure (if points exist)
        if polygon.points and polygon.geometry:
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry
            if polygon.geometry["coordinates"]:
                coord_ring = polygon.geometry["coordinates"][0]
                assert len(coord_ring) >= 4
                assert len(coord_ring[0]) == 2
                assert coord_ring[0] == coord_ring[-1]


async def test_bike_access_egress(motis_adapter_online):
    """Test catchment area with bicycle access and egress modes."""
    bike_request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[
            CatchmentAreaRoutingModePT.bus,
            CatchmentAreaRoutingModePT.tram,
        ],
        cutoffs=[25],
        access_settings=AccessEgressSettings(
            mode=AccessEgressMode.bicycle, max_time=15, speed=15.0
        ),
        egress_settings=AccessEgressSettings(
            mode=AccessEgressMode.bicycle, max_time=15, speed=15.0
        ),
    )

    response = await motis_adapter_online._get_transit_catchment_area(bike_request)

    assert len(response.polygons) == 1
    assert response.polygons[0].travel_time == 25
    assert bike_request.access_settings.mode == AccessEgressMode.bicycle
    assert bike_request.egress_settings.mode == AccessEgressMode.bicycle


async def test_invalid_coordinates_handling(motis_adapter_online):
    """Test handling of coordinates in remote areas with no transit coverage."""
    # Use coordinates in the middle of the Pacific Ocean where MOTIS has no data
    remote_request = TransitCatchmentAreaRequest(
        starting_points=[
            {"lat": 0.0, "lon": -160.0}  # Middle of Pacific Ocean
        ],
        transit_modes=[CatchmentAreaRoutingModePT.bus],
        cutoffs=[15],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    response = await motis_adapter_online._get_transit_catchment_area(remote_request)

    # Should return valid structure but likely with no or minimal locations
    assert response is not None
    assert len(response.polygons) <= len(remote_request.cutoffs)
    assert response.metadata.get("total_locations", 0) == 0


@pytest.mark.network
async def test_motis_one_to_all_integration_minimal(
    simple_berlin_request: TransitCatchmentAreaRequest,
) -> None:
    """Minimal integration test that can run independently."""
    from goatlib.routing.adapters.motis import create_motis_adapter

    adapter = create_motis_adapter()

    try:
        response = await adapter._get_transit_catchment_area(simple_berlin_request)
        assert len(response.polygons) == len(simple_berlin_request.cutoffs)
        assert response.metadata.get("source") == "motis_one_to_all"

    finally:
        await adapter.motis_client.close()
