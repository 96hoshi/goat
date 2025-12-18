import pytest
from goatlib.routing.schemas.base import AccessEgressMode, CatchmentAreaRoutingModePT
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    CatchmentAreaPolygon,
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaResponse,
)


def test_valid_single_point() -> None:
    """Test creating valid transit catchment area request."""
    request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[CatchmentAreaRoutingModePT.bus],
        cutoffs=[15, 30],
    )
    assert len(request.starting_points) == 1
    assert request.starting_points[0].lat == 52.5200
    assert request.starting_points[0].lon == 13.4050


def test_reject_multiple_points() -> None:
    """Test that multiple starting points are rejected."""
    with pytest.raises(ValueError, match="at most 1 item"):
        TransitCatchmentAreaRequest(
            starting_points=[
                {"lat": 52.5200, "lon": 13.4050},
                {"lat": 52.5300, "lon": 13.4150},
            ],
            transit_modes=[CatchmentAreaRoutingModePT.bus],
            cutoffs=[15, 30],
        )


def test_valid_cutoffs() -> None:
    """Test creating valid cutoffs configuration."""
    request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[CatchmentAreaRoutingModePT.bus],
        cutoffs=[15, 30, 45, 60],
    )
    assert request.cutoffs == [15, 30, 45, 60]


def test_unsorted_cutoffs_auto_fix() -> None:
    """Test that unsorted cutoffs are automatically sorted."""
    request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[CatchmentAreaRoutingModePT.bus],
        cutoffs=[30, 15, 45, 60],  # Unsorted input
    )
    # Should be automatically sorted and deduplicated
    assert request.cutoffs == [15, 30, 45, 60]


def test_negative_cutoffs() -> None:
    """Test that negative cutoffs are rejected."""
    with pytest.raises(ValueError, match="must be positive"):
        TransitCatchmentAreaRequest(
            starting_points=[{"lat": 52.5200, "lon": 13.4050}],
            transit_modes=[CatchmentAreaRoutingModePT.bus],
            cutoffs=[-15, 30, 45],
        )


def test_valid_request() -> None:
    """Test creating a valid transit catchment area request."""
    request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        cutoffs=[15, 30, 45, 60],
    )

    assert len(request.starting_points) == 1
    assert len(request.transit_modes) == 2
    assert len(request.cutoffs) == 4


def test_bike_access_request() -> None:
    """Test transit request with bicycle access mode."""
    request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[
            CatchmentAreaRoutingModePT.rail,
            CatchmentAreaRoutingModePT.subway,
        ],
        cutoffs=[15, 30, 45],
        access_settings=AccessEgressSettings(
            mode=AccessEgressMode.bicycle, max_time=25, speed=15.0
        ),
    )

    assert request.access_mode == AccessEgressMode.bicycle
    assert request.access_settings.max_time == 25


def test_access_egress_settings() -> None:
    """Test access and egress settings configuration."""
    # Test default walk settings
    walk_settings = AccessEgressSettings.create_walk_settings()
    assert walk_settings.mode == AccessEgressMode.walk
    assert walk_settings.max_time == 15
    assert walk_settings.speed == 5.0

    # Test default bike settings
    bike_settings = AccessEgressSettings.create_bike_settings()
    assert bike_settings.mode == AccessEgressMode.bicycle
    assert bike_settings.max_time == 20
    assert bike_settings.speed == 15.0


def test_custom_request_configuration() -> None:
    """Test custom transit request configuration."""
    request = TransitCatchmentAreaRequest(
        starting_points=[{"lat": 52.5200, "lon": 13.4050}],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        cutoffs=[15, 30, 45],
        max_transfers=6,
        access_settings=AccessEgressSettings(
            mode=AccessEgressMode.walk, max_time=20, speed=4.5
        ),
        egress_settings=AccessEgressSettings(
            mode=AccessEgressMode.bicycle, max_time=30, speed=18.0
        ),
    )

    assert request.max_transfers == 6
    assert request.access_settings.max_time == 20
    assert request.access_settings.speed == 4.5
    assert request.egress_settings.max_time == 30
    assert request.egress_settings.speed == 18.0


def test_catchment_area_polygon() -> None:
    """Test catchment area polygon response structure."""
    polygon = CatchmentAreaPolygon(
        travel_time=30,
        points=[
            {"lat": 0, "lon": 0},
            {"lat": 0, "lon": 1},
            {"lat": 1, "lon": 1},
            {"lat": 1, "lon": 0},
        ],
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    )

    assert polygon.travel_time == 30
    assert polygon.geometry["type"] == "Polygon"
    assert len(polygon.points) == 4


def test_transit_response() -> None:
    """Test transit catchment area response."""
    polygons = [
        CatchmentAreaPolygon(
            travel_time=15,
            points=[
                {"lat": 0, "lon": 0},
                {"lat": 0, "lon": 1},
                {"lat": 1, "lon": 1},
                {"lat": 1, "lon": 0},
            ],
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        ),
        CatchmentAreaPolygon(
            travel_time=30,
            points=[
                {"lat": 0, "lon": 0},
                {"lat": 0, "lon": 2},
                {"lat": 2, "lon": 2},
                {"lat": 2, "lon": 0},
            ],
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
