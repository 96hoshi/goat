import pytest
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressMode,
    CatchmentAreaPolygon,
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaResponse,
    TransitCatchmentAreaStartingPoints,
    TransitCatchmentAreaTravelTimeCost,
    TransitRoutingSettings,
)


def test_valid_single_point() -> None:
    """Test creating valid single starting point."""
    starting_points = TransitCatchmentAreaStartingPoints(lat=[52.5200], lon=[13.4050])
    assert starting_points.lat == [52.5200]
    assert starting_points.lon == [13.4050]


def test_reject_multiple_points() -> None:
    """Test that multiple starting points are rejected."""
    with pytest.raises(ValueError, match="exactly one starting point"):
        TransitCatchmentAreaStartingPoints(
            lat=[52.5200, 52.5300], lon=[13.4050, 13.4150]
        )


def test_valid_travel_cost() -> None:
    """Test creating valid travel cost configuration."""
    travel_cost = TransitCatchmentAreaTravelTimeCost(
        max_traveltime=60, cutoffs=[15, 30, 45, 60]
    )
    assert travel_cost.max_traveltime == 60
    assert travel_cost.cutoffs == [15, 30, 45, 60]


def test_cutoffs_exceed_max_time() -> None:
    """Test that cutoffs exceeding max travel time are rejected."""
    with pytest.raises(ValueError, match="exceed maximum travel time"):
        TransitCatchmentAreaTravelTimeCost(max_traveltime=30, cutoffs=[15, 45, 60])


def test_negative_cutoffs() -> None:
    """Test that negative cutoffs are rejected."""
    with pytest.raises(ValueError, match="must be positive"):
        TransitCatchmentAreaTravelTimeCost(max_traveltime=60, cutoffs=[-15, 30, 45])


def test_unsorted_cutoffs() -> None:
    """Test that unsorted cutoffs are rejected."""
    with pytest.raises(ValueError, match="ascending order"):
        TransitCatchmentAreaTravelTimeCost(max_traveltime=60, cutoffs=[30, 15, 45, 60])


def test_valid_request() -> None:
    """Test creating a valid transit isochrone request."""
    request_data = {
        "starting_points": {"lat": [52.5200], "lon": [13.4050]},
        "transit_modes": ["bus", "tram"],
        "travel_cost": {
            "max_traveltime": 60,
            "cutoffs": [15, 30, 45, 60],
        },
    }

    request = TransitCatchmentAreaRequest(**request_data)
    assert len(request.starting_points.lat) == 1
    assert len(request.transit_modes) == 2
    assert request.travel_cost.max_traveltime == 60


def test_bike_access_request() -> None:
    """Test transit request with bicycle access mode."""
    request_data = {
        "starting_points": {"lat": [52.5200], "lon": [13.4050]},
        "transit_modes": ["rail", "subway"],
        "travel_cost": {"max_traveltime": 45, "cutoffs": [15, 30, 45]},
        "routing_settings": {
            "access_settings": {"mode": "bicycle", "max_time": 25, "speed": 15.0}
        },
    }

    request = TransitCatchmentAreaRequest(**request_data)
    assert request.access_mode == AccessEgressMode.bicycle
    assert request.routing_settings.access_settings.max_time == 25


def test_routing_settings() -> None:
    """Test routing settings configuration."""
    routing_settings = TransitRoutingSettings()

    # Test default values
    assert routing_settings.max_transfers == 4
    assert routing_settings.access_settings.max_time == 15
    assert routing_settings.access_settings.speed == 5.0
    assert routing_settings.egress_settings.max_time == 15
    assert routing_settings.egress_settings.speed == 5.0


def test_custom_routing_settings() -> None:
    """Test custom routing settings."""
    routing_settings = TransitRoutingSettings(
        max_transfers=6,
        access_settings={"mode": "walk", "max_time": 20, "speed": 4.5},
        egress_settings={"mode": "bicycle", "max_time": 30, "speed": 18.0},
    )

    assert routing_settings.max_transfers == 6
    assert routing_settings.access_settings.max_time == 20
    assert routing_settings.access_settings.speed == 4.5
    assert routing_settings.egress_settings.max_time == 30
    assert routing_settings.egress_settings.speed == 18.0


def test_catchment_area_polygon() -> None:
    """Test catchment area polygon response structure."""
    polygon = CatchmentAreaPolygon(
        travel_time=30,
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    )

    assert polygon.travel_time == 30
    assert polygon.geometry["type"] == "Polygon"


def test_transit_response() -> None:
    """Test transit catchment area response."""
    polygons = [
        CatchmentAreaPolygon(
            travel_time=15,
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        ),
        CatchmentAreaPolygon(
            travel_time=30,
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
            },
        ),
    ]

    response = TransitCatchmentAreaResponse(
        polygons=polygons, metadata={"calculation_time": "2.3s"}, request_id="test-123"
    )

    assert len(response.polygons) == 2
    assert response.polygons[0].travel_time == 15
    assert response.polygons[1].travel_time == 30
    assert response.metadata["calculation_time"] == "2.3s"
    assert response.request_id == "test-123"
