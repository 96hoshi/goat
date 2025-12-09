import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RoutingProvider(StrEnum):
    """Supported routing service providers."""

    motis = "motis"
    otp = "otp"
    r5 = "r5"


class Mode(StrEnum):
    """Transport mode schema."""

    airplane = "airplane"
    bicycle = "bicycle"
    bus = "bus"
    cable_car = "cable_car"
    car = "car"
    coach = "coach"
    ferry = "ferry"
    flex = "flex"
    funicular = "funicular"
    gondola = "gondola"
    rail = "rail"
    scooter = "scooter"
    subway = "subway"
    tram = "tram"
    carpool = "carpool"
    taxi = "taxi"
    transit = "transit"
    walk = "walk"
    trolleybus = "trolleybus"
    monorail = "monorail"


class CatchmentAreaRoutingTypeActiveMobility(StrEnum):
    """Active mobility routing mode schema."""

    walk = "walk"
    wheelchair = "wheelchair"
    bicycle = "bicycle"
    pedelec = "pedelec"


class CatchmentAreaRoutingTypeCar(StrEnum):
    """Car routing mode schema."""

    car = "car"


class CatchmentAreaRoutingModePT(StrEnum):
    """Public transport routing mode schema."""

    bus = "bus"
    tram = "tram"
    rail = "rail"
    subway = "subway"
    ferry = "ferry"
    cable_car = "cable_car"
    gondola = "gondola"
    funicular = "funicular"


class AccessEgressMode(StrEnum):
    """Access and egress modes for transit routing."""

    walk = "walk"
    bicycle = "bicycle"


class CatchmentAreaType(StrEnum):
    """Area analysis type schema."""

    point = "point"
    network = "network"
    grid = "grid"
    polygon = "polygon"


class Coordinates(BaseModel):
    """Standard geographic location with WGS84 coordinates."""

    lat: float = Field(..., description="Latitude", ge=-90.0, le=90.0)
    lon: float = Field(..., description="Longitude", ge=-180.0, le=180.0)


class Route(BaseModel):
    """Base route model with common routing attributes."""

    route_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique route identifier"
    )
    duration: float = Field(..., description="Total duration in seconds", ge=0)
    distance: float | None = Field(None, description="Total distance in meters", ge=0)
    departure_time: datetime = Field(..., description="Route departure time")
    arrival_time: datetime | None = Field(None, description="Route arrival time")
