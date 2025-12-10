import pytest
from goatlib.routing.schemas.base import (
    AccessEgressMode,
    CatchmentAreaRoutingModePT,
)
from goatlib.routing.schemas.catchment_area_transit import (
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaStartingPoints,
    TransitCatchmentAreaTravelTimeCost,
)


@pytest.mark.slow
@pytest.mark.network
class TestMotisAdapterOneToAll:
    """Test class for MOTIS one-to-all functionality."""

    async def test_basic_one_to_all_success(self, motis_adapter_online, berlin_request):
        """Test basic one-to-all functionality returns valid catchment areas."""
        response = await motis_adapter_online.get_transit_catchment_area(berlin_request)

        # Basic structure checks
        assert response is not None
        assert len(response.polygons) == len(berlin_request.travel_cost.cutoffs)
        assert response.metadata.get("total_locations", 0) > 0
        assert response.metadata.get("source") == "motis_one_to_all"

        # Check each polygon
        for polygon in response.polygons:
            assert polygon.travel_time in berlin_request.travel_cost.cutoffs
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry

    async def test_multiple_cutoffs(self, motis_adapter_online, munich_request):
        """Test that multiple travel time cutoffs generate correct polygons."""
        response = await motis_adapter_online.get_transit_catchment_area(munich_request)

        assert len(response.polygons) == len(munich_request.travel_cost.cutoffs)

        # Polygons should be ordered by travel time
        travel_times = [p.travel_time for p in response.polygons]
        assert sorted(travel_times) == sorted(munich_request.travel_cost.cutoffs)

    async def test_different_transit_modes(self, motis_adapter_online):
        """Test different combinations of transit modes."""
        starting_points = TransitCatchmentAreaStartingPoints(
            lat=[52.5200], lon=[13.4050]
        )
        rail_only_request = TransitCatchmentAreaRequest(
            starting_points=starting_points,
            transit_modes=[CatchmentAreaRoutingModePT.rail],
            access_mode=AccessEgressMode.walk,
            egress_mode=AccessEgressMode.walk,
            travel_cost=TransitCatchmentAreaTravelTimeCost(
                max_traveltime=20, cutoffs=[20]
            ),
        )

        response = await motis_adapter_online.get_transit_catchment_area(
            rail_only_request
        )

        assert len(response.polygons) == 1
        assert response.polygons[0].travel_time == 20

    async def test_single_cutoff(self, motis_adapter_online):
        """Test with a single travel time cutoff."""
        starting_points = TransitCatchmentAreaStartingPoints(
            lat=[48.1351],
            lon=[11.5820],  # Munich
        )
        single_cutoff_request = TransitCatchmentAreaRequest(
            starting_points=starting_points,
            transit_modes=[
                CatchmentAreaRoutingModePT.subway,
                CatchmentAreaRoutingModePT.tram,
            ],
            access_mode=AccessEgressMode.walk,
            egress_mode=AccessEgressMode.walk,
            travel_cost=TransitCatchmentAreaTravelTimeCost(
                max_traveltime=20, cutoffs=[20]
            ),
        )

        response = await motis_adapter_online.get_transit_catchment_area(
            single_cutoff_request
        )

        assert len(response.polygons) == 1
        assert response.polygons[0].travel_time == 20

    async def test_geometry_structure(self, motis_adapter_online, berlin_request):
        """Test that returned geometry has correct GeoJSON structure."""
        response = await motis_adapter_online.get_transit_catchment_area(berlin_request)

        for polygon in response.polygons:
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry
            if polygon.geometry["coordinates"]:
                coord_ring = polygon.geometry["coordinates"][0]
                assert len(coord_ring) >= 4
                assert len(coord_ring[0]) == 2
                assert coord_ring[0] == coord_ring[-1]

    @pytest.mark.skip(reason="MOTIS bicycle access causes 500 error on public instance")
    async def test_bike_access_egress(self, motis_adapter_online):
        """Test catchment area with bicycle access and egress modes."""
        starting_points = TransitCatchmentAreaStartingPoints(
            lat=[52.5200], lon=[13.4050]
        )
        bike_request = TransitCatchmentAreaRequest(
            starting_points=starting_points,
            transit_modes=[
                CatchmentAreaRoutingModePT.bus,
                CatchmentAreaRoutingModePT.tram,
            ],
            access_mode=AccessEgressMode.bicycle,
            egress_mode=AccessEgressMode.bicycle,
            travel_cost=TransitCatchmentAreaTravelTimeCost(
                max_traveltime=25, cutoffs=[25]
            ),
        )

        response = await motis_adapter_online.get_transit_catchment_area(bike_request)

        assert len(response.polygons) == 1
        assert response.polygons[0].travel_time == 25

    async def test_invalid_coordinates_handling(self, motis_adapter_online):
        """Test handling of coordinates outside valid geographic range."""
        # MOTIS accepts invalid coordinates and returns empty results
        starting_points = TransitCatchmentAreaStartingPoints(
            lat=[91.0],
            lon=[181.0],  # Invalid coordinates
        )
        invalid_request = TransitCatchmentAreaRequest(
            starting_points=starting_points,
            transit_modes=[CatchmentAreaRoutingModePT.bus],
            access_mode=AccessEgressMode.walk,
            egress_mode=AccessEgressMode.walk,
            travel_cost=TransitCatchmentAreaTravelTimeCost(
                max_traveltime=15, cutoffs=[15]
            ),
        )

        response = await motis_adapter_online.get_transit_catchment_area(
            invalid_request
        )

        # Should return valid structure but with no locations
        assert response.metadata.get("total_locations", 0) == 0
        assert len(response.polygons) == 0


@pytest.mark.slow
@pytest.mark.network
async def test_motis_one_to_all_integration_minimal(
    simple_berlin_request: TransitCatchmentAreaRequest,
) -> None:
    """Minimal integration test that can run independently."""
    from goatlib.routing.adapters.motis import create_motis_adapter

    adapter = create_motis_adapter(use_fixtures=False)

    try:
        response = await adapter.get_transit_catchment_area(simple_berlin_request)
        assert len(response.polygons) == len(simple_berlin_request.travel_cost.cutoffs)
        assert response.metadata.get("source") == "motis_one_to_all"

    finally:
        await adapter.motis_client.close()
