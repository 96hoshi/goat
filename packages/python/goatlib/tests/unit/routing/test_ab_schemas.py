from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest
from goatlib.routing.schemas.ab_routing import (
    ABLeg,
    ABRoute,
    ABRoutingRequest,
    ABRoutingResponse,
)
from goatlib.routing.schemas.base import Coordinates, Mode, Route
from pydantic import ValidationError

# =====================================================================
#  FIXTURES: Test Data
# =====================================================================


@pytest.fixture
def valid_coordinates_data() -> Coordinates:
    """Provides valid data for a Coordinates model."""
    return Coordinates(lat=48.8566, lon=2.3522)  # Munich coordinates


@pytest.fixture
def valid_leg_data(valid_coordinates_data: Coordinates) -> Dict[str, Any]:
    """Provides a valid dictionary for creating an ABLeg."""
    now = datetime.now(timezone.utc)
    return {
        "leg_id": "leg_123",
        "mode": Mode.walk,
        "origin": valid_coordinates_data,
        "destination": Coordinates(lat=48.8606, lon=2.3376),
        "departure_time": now,
        "arrival_time": now + timedelta(seconds=5),
        "duration": 300,
        "distance": 500,
    }


@pytest.fixture
def valid_route_data(valid_leg_data: Dict[str, Any]) -> Dict[str, Any]:
    """Provides a valid dictionary for creating an ABRoute."""
    return {
        "route_id": "route_abc",
        "duration": 25,  # Inherited from base Route
        "distance": 1500,  # Inherited from base Route
        "departure_time": datetime.now(timezone.utc),  # Required by base Route
        "legs": [valid_leg_data],
    }


# =====================================================================
#  SCHEMA TESTS: ABRoutingRequest
# =====================================================================


def test_ab_request_creation_with_defaults(
    valid_coordinates_data: Coordinates,
) -> None:
    """Tests that a minimal ABRoutingRequest is created with correct defaults."""
    req = ABRoutingRequest(
        origin=valid_coordinates_data,
        destination=Coordinates(lat=40.7128, lon=-74.0060),
        modes=[Mode.transit],
    )
    assert req.max_results == 5


@pytest.mark.parametrize(
    "results, should_fail",
    [
        (0, True),  # Lower bound fail (must be >= 1)
        (11, True),  # Upper bound fail (must be <= 10)
        (1, False),  # Lower bound success
        (10, False),  # Upper bound success
        (5, False),  # Valid middle value
    ],
    ids=["too-low", "too-high", "min-success", "max-success", "valid-middle"],
)
def test_ab_request_max_results_constraints(
    valid_coordinates_data: Coordinates, results: int, should_fail: bool
) -> None:
    """Tests the ge=1 and le=10 constraints on the max_results field."""
    base_data = {
        "origin": valid_coordinates_data,
        "destination": {"lat": 40.7128, "lon": -74.0060},
        "modes": [Mode.transit],
        "max_results": results,
    }
    if should_fail:
        with pytest.raises(ValidationError) as exc_info:
            ABRoutingRequest(**base_data)
        # Optional: Add an assertion to check the error message
        if results < 1:
            assert "Input should be greater than or equal to 1" in str(exc_info.value)
        else:
            assert "Input should be less than or equal to 10" in str(exc_info.value)
    else:
        assert ABRoutingRequest(**base_data).max_results == results


def test_same_origin_destination_validation() -> None:
    """Test routing validation with identical origin and destination."""
    coords = Coordinates(lat=52.5200, lon=13.4050)

    with pytest.raises(ValidationError) as exc_info:
        ABRoutingRequest(
            origin=coords,
            destination=coords,
            modes=[Mode.walk],
        )

    assert "Origin and destination cannot be the same" in str(exc_info.value)


# =====================================================================
#  SCHEMA TESTS: ABRoute
# =====================================================================


def test_ab_route_creation_success(valid_route_data: Dict[str, Any]) -> None:
    """Tests successful creation of an ABRoute from valid data."""
    route = ABRoute(**valid_route_data)

    assert route.route_id == "route_abc"
    assert len(route.legs) == 1
    assert isinstance(route.legs[0], ABLeg)
    assert route.duration == 25


@pytest.mark.parametrize(
    "duration, distance",
    [
        (0, 5000.0),  # Zero duration
        (-10, 5000.0),  # Negative duration
        (30.5, 0),  # Zero distance
        (30.5, -100),  # Negative distance
    ],
    ids=["zero-duration", "neg-duration", "zero-distance", "neg-distance"],
)
def test_ab_route_creation_invalid(duration: float, distance: float) -> None:
    """Tests that a Route with invalid duration or distance raises a ValueError."""
    with pytest.raises(ValidationError):
        ABRoute(
            duration=duration,
            distance=distance,
            departure_time=datetime.now(timezone.utc),
            legs=[],
        )


# =====================================================================
#  SCHEMA TESTS: ABRoutingResponse
# =====================================================================


def test_ab_response_creation_success(valid_route_data: Dict[str, Any]) -> None:
    """Tests successful creation of ABRoutingResponse with consistent route counts."""
    route_obj = ABRoute(**valid_route_data)

    response = ABRoutingResponse(
        routes=[route_obj],
    )

    assert response.type == "FeatureCollection"


@pytest.mark.parametrize(
    "lat, lon, expected_error",
    [
        (91.0, 13.4050, ValueError),
        (-91.0, 13.4050, ValueError),
        (52.5200, 181.0, ValueError),
        (52.5200, -181.0, ValueError),
    ],
    ids=["lat-too-high", "lat-too-low", "lon-too-high", "lon-too-low"],
)
def test_coordinates_invalid_coordinates(
    lat: float, lon: float, expected_error: type
) -> None:
    """Test that invalid coordinates raise a ValueError."""
    with pytest.raises(expected_error):
        Coordinates(lat=lat, lon=lon)


def test_coordinates_valid() -> None:
    """Test creating a valid Coordinates."""
    coords = Coordinates(lat=52.5200, lon=13.4050)
    assert coords.lat == 52.5200
    assert coords.lon == 13.4050


def test_transport_mode_enum() -> None:
    """Test that Mode enum has expected values."""
    assert Mode.walk == "walk"
    assert Mode.bus == "bus"
    assert Mode.car == "car"
    assert Mode.transit == "transit"


# add a test for route schema
def test_route_schema() -> None:
    """Test creating a Route object."""
    route = Route(
        duration=3600,
        distance=10000,
        departure_time=datetime.now(timezone.utc),
    )
    assert route.duration == 3600
    assert route.distance == 10000
    assert route.departure_time is not None
    assert route.route_id is not None
