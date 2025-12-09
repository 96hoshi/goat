from typing import Any, Dict, List, Optional, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from goatlib.routing.config import routing_settings
from goatlib.routing.schemas.base import (
    AccessEgressMode,
    CatchmentAreaRoutingModePT,
)


class CatchmentAreaStartingPointsPT(BaseModel):
    """Starting points for transit catchment areas (single point only)."""

    lat: List[float] = Field(
        ..., description="List of latitudes (must contain exactly one point)."
    )
    lon: List[float] = Field(
        ..., description="List of longitudes (must contain exactly one point)."
    )

    @model_validator(mode="after")
    def validate_single_point(self) -> Self:
        """Ensure exactly one starting point for transit routing."""
        if not self.lat or not self.lon:
            raise ValueError("Latitude and longitude are required for transit routing.")

        if len(self.lat) != 1 or len(self.lon) != 1:
            raise ValueError(
                "Transit catchment areas support exactly one starting point."
            )

        return self


class TravelTimeCost(BaseModel):
    """Travel time configuration with cutoffs for transit analysis."""

    max_traveltime: int = Field(
        ...,
        title="Max Travel Time",
        description="The maximum travel time in minutes.",
        ge=1,
        le=routing_settings.transit.max_traveltime,
    )
    cutoffs: List[int] = Field(
        ...,
        title="Time Cutoffs",
        description="List of travel time cutoffs in minutes for catchment area bands.",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_cutoffs(self) -> Self:
        """Validate that cutoffs are within max_traveltime and properly ordered."""
        # Check cutoffs are within max time
        invalid_cutoffs = [c for c in self.cutoffs if c > self.max_traveltime]
        if invalid_cutoffs:
            raise ValueError(
                f"Cutoffs {invalid_cutoffs} exceed maximum travel time {self.max_traveltime}."
            )

        # Check all cutoffs are positive
        if any(c <= 0 for c in self.cutoffs):
            raise ValueError("All cutoffs must be positive.")

        # Check cutoffs are unique and sorted
        unique_sorted = sorted(set(self.cutoffs))
        if self.cutoffs != unique_sorted:
            raise ValueError("Cutoffs must be unique and in ascending order.")

        return self


class AccessEgressSettings(BaseModel):
    """Settings for access/egress modes in transit routing."""

    mode: AccessEgressMode = Field(
        default=AccessEgressMode.walk,
        title="Access/Egress Mode",
        description="Mode of transportation for access or egress.",
    )
    max_time: int = Field(
        ...,
        title="Maximum Time",
        description="Maximum time allowed for this mode in minutes.",
        ge=1,
    )
    speed: float = Field(
        ...,
        title="Speed",
        description="Average speed for this mode in km/h.",
        gt=0,
    )

    @model_validator(mode="after")
    def validate_mode_constraints(self) -> Self:
        """Validate constraints based on the access/egress mode."""
        mode_key = self.mode.value
        limits = getattr(routing_settings.transit, mode_key, None)
        if not limits:
            raise ValueError(f"Unknown access/egress mode: {self.mode}")

        # Validate time limits
        if self.max_time > limits.max_time:
            raise ValueError(
                f"Max time ({self.max_time}) exceeds limit for {self.mode} ({limits.max_time})."
            )

        # Validate speed limits
        if not (limits.min_speed <= self.speed <= limits.max_speed):
            raise ValueError(
                f"Speed ({self.speed}) must be between {limits.min_speed} and {limits.max_speed} for {self.mode}."
            )

        return self

    @classmethod
    def create_walk_settings(
        cls, max_time: int = 15, speed: float = None
    ) -> "AccessEgressSettings":
        """Create walk settings with defaults."""
        return cls(
            mode=AccessEgressMode.walk,
            max_time=max_time,
            speed=speed or routing_settings.transit.walk.default_speed,
        )

    @classmethod
    def create_bike_settings(
        cls, max_time: int = 20, speed: float = None
    ) -> "AccessEgressSettings":
        """Create bike settings with defaults."""
        return cls(
            mode=AccessEgressMode.bicycle,
            max_time=max_time,
            speed=speed or routing_settings.transit.bicycle.default_speed,
        )


class TransitRoutingSettings(BaseModel):
    """Advanced configuration for transit routing algorithm."""

    max_transfers: int = Field(
        default=4,
        title="Maximum Transfers",
        description="Maximum number of transfers allowed.",
        ge=0,
        le=routing_settings.transit.max_transfers,
    )
    access_settings: AccessEgressSettings = Field(
        default_factory=AccessEgressSettings.create_walk_settings,
        title="Access Settings",
        description="Configuration for accessing transit stops.",
    )
    egress_settings: AccessEgressSettings = Field(
        default_factory=AccessEgressSettings.create_walk_settings,
        title="Egress Settings",
        description="Configuration for egressing from transit stops.",
    )


class TransitCatchmentAreaRequest(BaseModel):
    """Unified request model for transit catchment area calculation."""

    starting_points: CatchmentAreaStartingPointsPT = Field(
        ...,
        title="Starting Points",
        description="Starting point for catchment area calculation (single point only).",
    )
    transit_modes: List[CatchmentAreaRoutingModePT] = Field(
        ...,
        title="Transit Modes",
        description="List of transit modes to include in the calculation.",
        min_length=1,
    )
    travel_cost: TravelTimeCost = Field(
        ...,
        title="Travel Cost Configuration",
        description="Travel time and cutoff configuration.",
    )
    routing_settings: TransitRoutingSettings = Field(
        default_factory=TransitRoutingSettings,
        title="Routing Settings",
        description="Advanced routing configuration.",
    )
    network_id: Optional[UUID] = Field(
        default=None,
        title="Network ID",
        description="Optional ID of the transit network to use.",
    )

    # Convenience properties for backward compatibility
    @property
    def access_mode(self) -> AccessEgressMode:
        """Get the access mode for backward compatibility."""
        return self.routing_settings.access_settings.mode

    @property
    def egress_mode(self) -> AccessEgressMode:
        """Get the egress mode for backward compatibility."""
        return self.routing_settings.egress_settings.mode

    @property
    def max_transfers(self) -> int:
        """Get max transfers for backward compatibility."""
        return self.routing_settings.max_transfers


# Backward compatibility aliases
TransitCatchmentAreaStartingPoints = CatchmentAreaStartingPointsPT
TransitCatchmentAreaTravelTimeCost = TravelTimeCost


"""Response schemas."""


class CatchmentAreaPolygon(BaseModel):
    """A single catchment area polygon with its properties."""

    travel_time: int = Field(
        ...,
        title="Travel Time",
        description="Maximum travel time for this catchment area in minutes.",
    )
    geometry: Dict[str, Any] = Field(
        ...,
        title="Polygon Geometry",
        description="Polygon geometry data (coordinates, type, etc.)",
    )

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate basic polygon geometry structure."""
        if not isinstance(v, dict):
            raise ValueError("Geometry must be a dictionary.")

        required_fields = ["type", "coordinates"]
        for field in required_fields:
            if field not in v:
                raise ValueError(f"Geometry must have a '{field}' field.")

        return v


class TransitCatchmentAreaResponse(BaseModel):
    """Response model for transit catchment area calculation."""

    polygons: List[CatchmentAreaPolygon] = Field(
        ...,
        title="Catchment Area Polygons",
        description="List of catchment area polygons with travel times.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        title="Metadata",
        description="Additional metadata about the calculation.",
    )
    request_id: Optional[str] = Field(
        default=None,
        title="Request ID",
        description="Unique identifier for the request.",
    )


"""Example requests."""


request_examples_transit_catchment_area = {
    "basic_transit_catchment_area": {
        "summary": "basic transit catchment area request",
        "value": {
            "starting_points": {"lat": [52.5200], "lon": [13.4050]},
            "transit_modes": ["bus", "tram", "subway"],
            "travel_cost": {"max_traveltime": 60, "cutoffs": [15, 30, 45, 60]},
        },
    },
    "bike_access_catchment_area": {
        "summary": "bike access catchment area request",
        "value": {
            "starting_points": {"lat": [52.5200], "lon": [13.4050]},
            "transit_modes": ["rail", "subway"],
            "access_mode": "bicycle",
            "travel_cost": {"max_traveltime": 45, "cutoffs": [15, 30, 45]},
            "routing_settings": {"bike_settings": {"max_time": 25}},
        },
    },
    "custom_speeds_catchment_area": {
        "summary": "custom speeds catchment area request",
        "value": {
            "starting_points": {"lat": [52.5200], "lon": [13.4050]},
            "transit_modes": ["bus", "tram"],
            "egress_mode": "bicycle",
            "travel_cost": {"max_traveltime": 50, "cutoffs": [10, 20, 30, 40, 50]},
            "routing_settings": {
                "walk_settings": {"speed": 1.2},
                "bike_settings": {"speed": 5.0},
            },
        },
    },
}
