import pytest
from goatlib.routing.schemas.base import CatchmentAreaType
from goatlib.routing.schemas.catchment import Catchment
from pydantic import ValidationError

"""Test cases for Catchment validation and functionality."""


def test_valid_catchment_schema_creation() -> None:
    """Test creating a valid catchment schema."""
    data = {
        "starting_points": [
            {"lon": 11.123, "lat": 48.1234},
            {"lon": 11.456, "lat": 48.5678},
        ],
        "cutoffs": [10.0, 20.0, 30.0],
        "type": "polygon",
    }

    schema = Catchment(**data)
    assert len(schema.starting_points) == 2
    assert schema.starting_points[0].lon == 11.123
    assert schema.starting_points[0].lat == 48.1234
    assert schema.starting_points[1].lon == 11.456
    assert schema.starting_points[1].lat == 48.5678
    assert schema.cutoffs == [10.0, 20.0, 30.0]
    assert schema.type == CatchmentAreaType.polygon


def test_coordinate_validation_longitude() -> None:
    """Test longitude coordinate validation."""
    # Valid longitude range
    valid_data = {
        "starting_points": [
            {"lon": -180.0, "lat": 48.1},
            {"lon": 0.0, "lat": 48.1},
            {"lon": 180.0, "lat": 48.1},
        ],
        "cutoffs": [10.0],
        "type": "point",
    }
    schema = Catchment(**valid_data)
    assert len(schema.starting_points) == 3

    # Invalid longitude - too low
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": -180.1, "lat": 48.1}],
            cutoffs=[10.0],
            type="point",
        )
    assert "greater than or equal to -180" in str(exc_info.value)

    # Invalid longitude - too high
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": 180.1, "lat": 48.1}],
            cutoffs=[10.0],
            type="point",
        )
    assert "less than or equal to 180" in str(exc_info.value)


def test_coordinate_validation_latitude() -> None:
    """Test latitude coordinate validation."""
    # Valid latitude range
    valid_data = {
        "starting_points": [
            {"lon": 11.0, "lat": -90.0},
            {"lon": 11.0, "lat": 0.0},
            {"lon": 11.0, "lat": 90.0},
        ],
        "cutoffs": [10.0],
        "type": "point",
    }
    schema = Catchment(**valid_data)
    assert len(schema.starting_points) == 3

    # Invalid latitude - too low
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": 11.0, "lat": -90.1}],
            cutoffs=[10.0],
            type="point",
        )
    assert "greater than or equal to -90" in str(exc_info.value)

    # Invalid latitude - too high
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": 11.0, "lat": 90.1}],
            cutoffs=[10.0],
            type="point",
        )
    assert "less than or equal to 90" in str(exc_info.value)


def test_invalid_coordinate_count() -> None:
    """Test validation of coordinate structure."""
    # Missing required field
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": 11.123}],  # Missing lat
            cutoffs=[10.0],
            type="point",
        )
    assert "Field required" in str(exc_info.value)

    # Invalid format (list instead of dict)
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[[11.123, 48.1234]],  # Should be dict
            cutoffs=[10.0],
            type="point",
        )
    assert "Input should be a valid dictionary" in str(exc_info.value)


def test_empty_starting_points() -> None:
    """Test validation with empty starting points."""
    with pytest.raises(ValidationError) as exc_info:
        Catchment(starting_points=[], cutoffs=[10.0], type="point")
    assert "at least 1" in str(exc_info.value).lower()


def test_cutoffs_validation() -> None:
    """Test cutoffs validation."""
    base_data = {
        "starting_points": [{"lon": 11.123, "lat": 48.1234}],
        "type": "point",
    }

    # Negative cutoff
    with pytest.raises(ValidationError) as exc_info:
        Catchment(cutoffs=[-5.0], **base_data)
    assert "must be positive" in str(exc_info.value)

    # Zero cutoff
    with pytest.raises(ValidationError) as exc_info:
        Catchment(cutoffs=[0.0], **base_data)
    assert "must be positive" in str(exc_info.value)

    # Unsorted cutoffs should be auto-sorted without error
    schema = Catchment(cutoffs=[20.0, 10.0, 30.0], **base_data)
    assert schema.cutoffs == [10.0, 20.0, 30.0]


def test_empty_cutoffs() -> None:
    """Test validation with empty cutoffs."""
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": 11.123, "lat": 48.1234}],
            cutoffs=[],
            type="point",
        )
    assert "at least 1" in str(exc_info.value).lower()


def test_invalid_catchment_type() -> None:
    """Test validation with invalid catchment type."""
    with pytest.raises(ValidationError) as exc_info:
        Catchment(
            starting_points=[{"lon": 11.123, "lat": 48.1234}],
            cutoffs=[10.0],
            type="invalid_type",
        )
    assert "Input should be" in str(exc_info.value)


def test_example_from_user_request() -> None:
    """Test the exact example provided in the user request."""
    data = {
        "starting_points": [
            {"lon": 11.123, "lat": 12.34},
            {"lon": 48.11, "lat": 48.1234},
        ],
        "cutoffs": [10.0, 20.0, 30.0],
        "type": "polygon",
    }

    schema = Catchment(**data)
    assert len(schema.starting_points) == 2
    assert schema.starting_points[0].lon == 11.123
    assert schema.starting_points[0].lat == 12.34
    assert schema.starting_points[1].lon == 48.11
    assert schema.starting_points[1].lat == 48.1234
    assert schema.cutoffs == [10.0, 20.0, 30.0]
    assert schema.type == CatchmentAreaType.polygon
