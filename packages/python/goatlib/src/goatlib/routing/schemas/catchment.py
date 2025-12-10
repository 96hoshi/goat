from typing import List

from pydantic import BaseModel, Field, field_validator

from goatlib.routing.schemas.base import (
    CatchmentAreaType,
    Coordinates,
)


class Catchment(BaseModel):
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


# Example usage
example_catchment = {
    "starting_points": [{"lon": 11.123, "lat": 12.34}, {"lon": 48.11, "lat": 48.1234}],
    "cutoffs": [10.0, 20.0, 30.0],
    "type": "polygon",
}
