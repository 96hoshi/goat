from typing import Any, Literal, Optional, Self, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from goatlib.routing.config import routing_settings
from goatlib.routing.schemas.base import (
    CatchmentAreaRoutingTypeActiveMobility,
    CatchmentAreaRoutingTypeCar,
    CatchmentAreaType,
    Coordinates,
)

# Default street network configuration constants
DEFAULT_NODE_LAYER_PROJECT_ID = 1  # Default node layer project ID


class TravelTimeCost(BaseModel):
    """Travel time-based cost schema."""

    cost_type: Literal["time"] = "time"
    max_traveltime: int = Field(
        ...,
        title="Max Travel Time",
        description="The maximum travel time in minutes.",
        ge=1,
    )
    steps: int = Field(
        ...,
        title="Steps",
        description="The number of steps.",
    )
    speed: Optional[int] = Field(
        None,
        title="Speed",
        description="The speed in km/h.",
        ge=1,
    )

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v: int, info) -> int:
        """Validate steps don't exceed max_traveltime."""
        max_traveltime = info.data.get("max_traveltime")
        if max_traveltime and v > max_traveltime:
            raise ValueError(
                f"Steps ({v}) cannot exceed max travel time ({max_traveltime})."
            )
        return v


class TravelDistanceCost(BaseModel):
    """Travel distance-based cost schema."""

    cost_type: Literal["distance"] = "distance"
    max_distance: int = Field(
        ...,
        title="Max Distance",
        description="The maximum distance in meters.",
        ge=50,
        le=20000,
    )
    steps: int = Field(
        ...,
        title="Steps",
        description="The number of steps.",
    )

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v: int, info) -> int:
        """Validate steps don't exceed max_distance."""
        max_distance = info.data.get("max_distance")
        if max_distance and v > max_distance:
            raise ValueError(
                f"Steps ({v}) cannot exceed max distance ({max_distance})."
            )
        return v


# Union type for travel costs
TravelCost = Union[TravelTimeCost, TravelDistanceCost]


class CatchmentAreaStreetNetwork(BaseModel):
    """Street network configuration for catchment area analysis."""

    edge_layer_project_id: int = Field(
        ...,
        title="Edge Layer Project ID",
        description="The layer project ID of the street network edge layer.",
    )
    node_layer_project_id: Optional[int] = Field(
        default=None,
        title="Node Layer Project ID",
        description="The layer project ID of the street network node layer.",
    )

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.node_layer_project_id is None:
            self.node_layer_project_id = DEFAULT_NODE_LAYER_PROJECT_ID


class CatchmentAreaActiveCarRequest(BaseModel):
    """Unified catchment area request model."""

    starting_points: list[Coordinates] = Field(
        ...,
        title="Starting Points",
        description="The starting points of the catchment area.",
    )
    routing_type: Union[
        CatchmentAreaRoutingTypeActiveMobility, CatchmentAreaRoutingTypeCar
    ] = Field(
        ..., title="Routing Type", description="The routing type of the catchment area."
    )
    travel_cost: TravelCost = Field(
        ..., title="Travel Cost", description="The travel cost configuration."
    )
    catchment_area_type: CatchmentAreaType = Field(
        ..., title="Return Type", description="The return type of the catchment area."
    )
    result_table: str = Field(
        ...,
        title="Result Table",
        description="The table name the results should be saved.",
    )
    layer_id: UUID = Field(
        ...,
        title="Layer ID",
        description="The ID of the layer the results should be saved.",
    )
    scenario_id: Optional[UUID] = Field(
        None,
        title="Scenario ID",
        description="The ID of the scenario that is to be applied on the base network.",
    )
    street_network: Optional[CatchmentAreaStreetNetwork] = Field(
        None,
        title="Street Network Layer Config",
        description="The configuration of the street network layers to use.",
    )
    polygon_difference: Optional[bool] = Field(
        None,
        title="Polygon Difference",
        description="If true, the polygons returned will be the geometrical difference of two following calculations.",
    )

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        """Validate the overall configuration consistency."""
        # Validate scenario + street network relationship
        if self.scenario_id is not None and self.street_network is None:
            raise ValueError(
                "Street network must be specified when using a scenario ID."
            )

        # Validate polygon difference settings
        is_polygon = self.catchment_area_type == CatchmentAreaType.polygon
        if is_polygon and self.polygon_difference is None:
            raise ValueError(
                "Polygon difference must be specified for polygon catchment areas."
            )
        elif not is_polygon and self.polygon_difference is not None:
            raise ValueError(
                "Polygon difference should not be specified for non-polygon catchment areas."
            )

        # Validate routing type and travel cost constraints
        self._validate_routing_constraints()

        return self

    def _validate_routing_constraints(self) -> None:
        """Validate routing type specific constraints."""
        # For active mobility, enforce speed requirements and limits
        if isinstance(self.routing_type, CatchmentAreaRoutingTypeActiveMobility):
            if isinstance(self.travel_cost, TravelTimeCost):
                if self.travel_cost.speed is None:
                    raise ValueError(
                        "Speed is required for active mobility time-based routing."
                    )
                if self.travel_cost.speed > routing_settings.active_mobility.max_speed:
                    raise ValueError(
                        f"Speed ({self.travel_cost.speed}) exceeds maximum for active mobility "
                        f"({routing_settings.active_mobility.max_speed})."
                    )
                if (
                    self.travel_cost.max_traveltime
                    > routing_settings.active_mobility.max_traveltime
                ):
                    raise ValueError(
                        f"Travel time ({self.travel_cost.max_traveltime}) exceeds maximum for active mobility "
                        f"({routing_settings.active_mobility.max_traveltime})."
                    )

        # For car routing, enforce travel time limits
        elif isinstance(self.routing_type, CatchmentAreaRoutingTypeCar):
            if isinstance(self.travel_cost, TravelTimeCost):
                if (
                    self.travel_cost.max_traveltime
                    > routing_settings.motorized_mobility.max_traveltime
                ):
                    raise ValueError(
                        f"Travel time ({self.travel_cost.max_traveltime}) exceeds maximum for motorized mobility "
                        f"({routing_settings.motorized_mobility.max_traveltime})."
                    )
                # Speed is optional for cars
                if self.travel_cost.speed is not None and self.travel_cost.speed <= 0:
                    raise ValueError("Speed must be positive if specified.")


# Backward compatibility aliases
ICatchmentAreaActiveMobility = CatchmentAreaActiveCarRequest
ICatchmentAreaCar = CatchmentAreaActiveCarRequest


request_examples: dict[str, Any] = {
    "catchment_area_active_mobility": {
        # 1. Single catchment area for walking (time based)
        "single_point_walking_time": {
            "summary": "Single point catchment area walking (time based)",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "walking",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 30,
                    "steps": 5,
                    "speed": 5,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 2. Single catchment area for walking (distance based)
        "single_point_walking_distance": {
            "summary": "Single point catchment area walking (distance based)",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "walking",
                "travel_cost": {
                    "cost_type": "distance",
                    "max_distance": 2500,
                    "steps": 100,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 3. Single catchment area for cycling
        "single_point_cycling": {
            "summary": "Single point catchment area cycling",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "bicycle",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 15,
                    "steps": 5,
                    "speed": 15,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 4. Single catchment area for walking with scenario
        "single_point_walking_scenario": {
            "summary": "Single point catchment area walking with scenario",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "walking",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 30,
                    "steps": 10,
                    "speed": 5,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "scenario_id": "e7dcaae4-1750-49b7-89a5-9510bf2761ad",
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 5. Multi-catchment area walking with more than one starting point
        "multi_point_walking": {
            "summary": "Multi point catchment area walking",
            "value": {
                "starting_points": [
                    {"lat": 52.5200, "lon": 13.4050},
                    {"lat": 52.5210, "lon": 13.4060},
                    {"lat": 52.5220, "lon": 13.4070},
                    {"lat": 52.5230, "lon": 13.4080},
                    {"lat": 52.5240, "lon": 13.4090},
                    {"lat": 52.5250, "lon": 13.4100},
                    {"lat": 52.5260, "lon": 13.4110},
                    {"lat": 52.5270, "lon": 13.4120},
                    {"lat": 52.5280, "lon": 13.4130},
                    {"lat": 52.5290, "lon": 13.4140},
                ],
                "routing_type": "walking",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 30,
                    "steps": 10,
                    "speed": 5,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 6. Multi-catchment area cycling with more than one starting point
        "multi_point_cycling": {
            "summary": "Multi point catchment area cycling",
            "value": {
                "starting_points": [
                    {"lat": 52.5200, "lon": 13.4050},
                    {"lat": 52.5210, "lon": 13.4060},
                    {"lat": 52.5220, "lon": 13.4070},
                    {"lat": 52.5230, "lon": 13.4080},
                    {"lat": 52.5240, "lon": 13.4090},
                    {"lat": 52.5250, "lon": 13.4100},
                    {"lat": 52.5260, "lon": 13.4110},
                    {"lat": 52.5270, "lon": 13.4120},
                    {"lat": 52.5280, "lon": 13.4130},
                    {"lat": 52.5290, "lon": 13.4140},
                ],
                "routing_type": "bicycle",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 15,
                    "steps": 5,
                    "speed": 15,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
    },
    "catchment_area_motorized_mobility": {
        # 1. Single catchment area for car (time based)
        "single_point_car_time": {
            "summary": "Single point catchment area car (time based)",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "car",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 30,
                    "steps": 5,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 2. Single catchment area for car (distance based)
        "single_point_car_distance": {
            "summary": "Single point catchment area car (distance based)",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "car",
                "travel_cost": {
                    "cost_type": "distance",
                    "max_distance": 10000,
                    "steps": 100,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 3. Single catchment area for car with scenario
        "single_point_car_scenario": {
            "summary": "Single point catchment area car with scenario",
            "value": {
                "starting_points": [{"lat": 52.5200, "lon": 13.4050}],
                "routing_type": "car",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 30,
                    "steps": 10,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "scenario_id": "e7dcaae4-1750-49b7-89a5-9510bf2761ad",
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
        # 4. Multi-catchment area car with more than one starting point
        "multi_point_car": {
            "summary": "Multi point catchment area car",
            "value": {
                "starting_points": [
                    {"lat": 52.5200, "lon": 13.4050},
                    {"lat": 52.5210, "lon": 13.4060},
                    {"lat": 52.5220, "lon": 13.4070},
                    {"lat": 52.5230, "lon": 13.4080},
                    {"lat": 52.5240, "lon": 13.4090},
                    {"lat": 52.5250, "lon": 13.4100},
                    {"lat": 52.5260, "lon": 13.4110},
                    {"lat": 52.5270, "lon": 13.4120},
                    {"lat": 52.5280, "lon": 13.4130},
                    {"lat": 52.5290, "lon": 13.4140},
                ],
                "routing_type": "car",
                "travel_cost": {
                    "cost_type": "time",
                    "max_traveltime": 30,
                    "steps": 10,
                },
                "catchment_area_type": "polygon",
                "polygon_difference": True,
                "result_table": "polygon_744e4fd1685c495c8b02efebce875359",
                "layer_id": "744e4fd1-685c-495c-8b02-efebce875359",
            },
        },
    },
}
