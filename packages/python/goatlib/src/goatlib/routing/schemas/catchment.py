from typing import Any, Dict, List, Optional

from core.schemas.catchment_area import CatchmentAreaRoutingModePT
from pydantic import BaseModel, Field, field_validator

from goatlib.routing.schemas.base import (
    CatchmentAreaType,
    Coordinates,
)
from goatlib.routing.schemas.catchment_area_transit import AccessEgressSettings


class CatchmentRequest(BaseModel):
    """Schema for catchment area requests."""

    starting_points: List[Coordinates] = Field(
        ...,
        title="Starting Points",
        description="List of geographic Coordinates for catchment calculation starting points.",
        min_length=1,
    )
    cutoffs: List[float] = Field(
        ...,
        title="Cutoffs",
        description="List of cost thresholds for catchment area calculation (time in minutes or distance in meters).",
        min_length=1,
    )
    type: CatchmentAreaType = Field(
        ...,
        title="Area Type",
        description="The type of catchment area output to generate.",
    )

    transit_modes: Optional[List[CatchmentAreaRoutingModePT]] = Field(
        default=None,
        title="Transit Modes",
        description="List of public transit modes. If None, PT catchment is skipped.",
    )
    access_settings: Optional[AccessEgressSettings] = Field(
        default_factory=AccessEgressSettings.create_walk_settings,
        title="Access Settings",
        description="Configuration for accessing the first transit stop. Defaults to walking.",
    )
    egress_settings: Optional[AccessEgressSettings] = Field(
        # Default to a 15-minute walk. The caller can override this.
        default_factory=AccessEgressSettings.create_walk_settings,
        title="Egress Settings",
        description="Configuration for the last-mile (egress) leg from transit stops or the origin. If None, the last-mile calculation is skipped.",
    )

    @field_validator("cutoffs")
    @classmethod
    def validate_cutoffs(cls, v: List[float]) -> List[float]:
        """Validate that cutoffs are positive and in ascending order."""
        for i, cutoff in enumerate(v):
            if cutoff <= 0:
                raise ValueError(f"Cutoff {i} must be positive, got {cutoff}")

        # Ensure cutoffs are in ascending order
        if all(v[i] <= v[i + 1] for i in range(len(v) - 1)):
            return v
        v.sort()
        return v


class CutoffResult(BaseModel):
    """Schema for the aggregated result of a single cutoff time."""

    cutoff_minutes: int
    pt_stations_found: Optional[int] = None  # It might not be calculated
    successful_routing: int
    total_reachable_nodes: int
    raw_response: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw response data from the routing engine",
    )


class CatchmentResponse(BaseModel):
    """Schema for the final catchment area response."""

    results: List[CutoffResult]
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Metadata about the calculation process."
    )


# Example usage
example_catchment = {
    "starting_points": [{"lon": 11.123, "lat": 12.34}, {"lon": 48.11, "lat": 48.1234}],
    "cutoffs": [10.0, 20.0, 30.0],
    "type": "polygon",
}
