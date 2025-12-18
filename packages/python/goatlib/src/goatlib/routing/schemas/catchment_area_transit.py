from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from goatlib.routing.config import routing_settings
from goatlib.routing.schemas.base import (
    AccessEgressMode,
    CatchmentAreaRoutingModePT,
    Coordinates,
)


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


class TransitCatchmentAreaRequest(BaseModel):
    """Unified request model for transit catchment area calculation."""

    starting_points: List[Coordinates] = Field(
        ...,
        title="Starting Point",
        description="Starting point for catchment area calculation (single point only).",
        min_length=1,
        max_length=1,
    )
    transit_modes: List[CatchmentAreaRoutingModePT] = Field(
        ...,
        title="Transit Modes",
        description="List of transit modes to include in the calculation.",
        min_length=1,
    )
    cutoffs: List[int] = Field(
        ...,
        title="Time Cutoffs",
        description="List of travel time cutoffs in minutes for catchment area bands.",
        min_length=1,
    )
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
    network_id: Optional[UUID] = Field(
        default=None,
        title="Network ID",
        description="Optional ID of the transit network to use.",
    )

    # Convenience properties for backward compatibility
    @property
    def access_mode(self) -> AccessEgressMode:
        """Get the access mode for backward compatibility."""
        return self.access_settings.mode

    @property
    def egress_mode(self) -> AccessEgressMode:
        """Get the egress mode for backward compatibility."""
        return self.egress_settings.mode

    @field_validator("cutoffs")
    @classmethod
    def validate_cutoffs(cls, v: List[int]) -> List[int]:
        """Validate that cutoffs are properly ordered and positive."""

        # Check all cutoffs are positive
        if any(c <= 0 for c in v):
            raise ValueError("All cutoffs must be positive.")

        # Check cutoffs are unique and sorted
        unique_sorted = sorted(set(v))
        if v != unique_sorted:
            # Auto-sort and deduplicate
            return unique_sorted

        return v


# ------------------------ Response Schemas ----------------------


class CatchmentAreaPolygon(BaseModel):
    """A single catchment area polygon with its properties."""

    travel_time: int = Field(
        ...,
        title="Travel Time",
        description="Maximum travel time for this catchment area in minutes.",
    )
    points: List[Coordinates] = Field(
        ...,
        title="Polygon Points",
        description="List of coordinates defining the polygon boundary.",
    )
    geometry: Dict[str, Any] | None = Field(
        default=None,
        title="Polygon Geometry",
        description="Optional polygon geometry data (coordinates, type, etc.)",
    )

    def set_geometry_from_points(self) -> None:
        """
        Create and set polygon geometry from the coordinate points.
        Updates the geometry field in-place using bounding box approach.
        """
        if not self.points:
            self.geometry = {"type": "Polygon", "coordinates": []}
            return

        # Extract coordinate pairs
        coord_pairs = []
        for coord in self.points:
            if coord.lat != 0 and coord.lon != 0:
                coord_pairs.append(
                    [coord.lon, coord.lat]
                )  # GeoJSON uses [lon, lat] order

        if len(coord_pairs) < 3:
            # Not enough points for a polygon, set empty
            self.geometry = {"type": "Polygon", "coordinates": []}
            return

        # Create a simple bounding box
        lons = [coord[0] for coord in coord_pairs]
        lats = [coord[1] for coord in coord_pairs]

        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        # Create bounding box polygon and set geometry
        bbox_coordinates = [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],  # Close the polygon
            ]
        ]

        self.geometry = {"type": "Polygon", "coordinates": bbox_coordinates}


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


# ------------------------ Example Requests ----------------------


request_examples_transit_catchment_area = {
    "basic_transit_catchment_area": {
        "summary": "basic transit catchment area request",
        "value": {
            "starting_points": [{"lat": 40.7128, "lon": -74.0060}],
            "transit_modes": ["bus", "tram", "subway"],
            "cutoffs": [15, 30, 45, 60],
        },
    },
    "bike_access_catchment_area": {
        "summary": "bike access catchment area request",
        "value": {
            "starting_points": [{"lat": 40.7128, "lon": -74.0060}],
            "transit_modes": ["rail", "subway"],
            "cutoffs": [15, 30, 45],
            "access_settings": {"mode": "bicycle", "max_time": 25, "speed": 15.0},
        },
    },
    "custom_speeds_catchment_area": {
        "summary": "custom speeds catchment area request",
        "value": {
            "starting_points": [{"lat": 40.7128, "lon": -74.0060}],
            "transit_modes": ["bus", "tram"],
            "cutoffs": [10, 20, 30, 40, 50],
            "egress_settings": {"mode": "bicycle", "max_time": 20, "speed": 12.0},
            "access_settings": {"mode": "walk", "max_time": 15, "speed": 4.5},
        },
    },
}
