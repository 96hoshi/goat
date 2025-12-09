from enum import StrEnum
from typing import List

from goatlib.routing.schemas.base import Mode


class MotisMode(StrEnum):
    """MOTIS transport modes enum."""

    walk = "WALK"
    bike = "BIKE"
    rental = "RENTAL"
    car = "CAR"
    car_parking = "CAR_PARKING"
    car_dropoff = "CAR_DROPOFF"
    odm = "ODM"
    flex = "FLEX"
    transit = "TRANSIT"
    tram = "TRAM"
    subway = "SUBWAY"
    ferry = "FERRY"
    airplane = "AIRPLANE"
    metro = "METRO"
    bus = "BUS"
    coach = "COACH"
    rail = "RAIL"
    highspeed_rail = "HIGHSPEED_RAIL"
    long_distance = "LONG_DISTANCE"
    night_rail = "NIGHT_RAIL"
    regional_fast_rail = "REGIONAL_FAST_RAIL"
    regional_rail = "REGIONAL_RAIL"
    suburban = "SUBURBAN"  # S-Bahn/suburban rail
    cable_car = "CABLE_CAR"
    funicular = "FUNICULAR"
    areal_lift = "AREAL_LIFT"
    other = "OTHER"


# Mode mappings between MOTIS and internal representations
MOTIS_TO_INTERNAL_MODE_MAP = {
    # Active mobility
    MotisMode.walk: Mode.walk,
    MotisMode.bike: Mode.bicycle,
    # Public transport - Direct mappings
    MotisMode.bus: Mode.bus,
    MotisMode.coach: Mode.bus,  # Coach is a type of bus
    MotisMode.tram: Mode.tram,
    MotisMode.subway: Mode.subway,
    MotisMode.metro: Mode.subway,  # Metro is subway
    MotisMode.ferry: Mode.ferry,
    MotisMode.cable_car: Mode.cable_car,
    MotisMode.funicular: Mode.funicular,
    # Rail variants - All map to RAIL
    MotisMode.rail: Mode.rail,
    MotisMode.highspeed_rail: Mode.rail,
    MotisMode.long_distance: Mode.rail,
    MotisMode.night_rail: Mode.rail,
    MotisMode.regional_fast_rail: Mode.rail,
    MotisMode.regional_rail: Mode.rail,
    MotisMode.suburban: Mode.rail,  # S-Bahn/suburban rail
    # Private transport
    MotisMode.car: Mode.car,
    MotisMode.car_parking: Mode.car,
    MotisMode.car_dropoff: Mode.car,
    # Meta-modes
    MotisMode.transit: Mode.transit,
    # Note: MotisMode.other maps to transit as a fallback for unknown modes
    MotisMode.other: Mode.transit,
}

INTERNAL_TO_MOTIS_MODE_MAP = {
    # Create reverse mapping, handling duplicates by preferring the primary mode
    Mode.walk: MotisMode.walk,
    Mode.bicycle: MotisMode.bike,
    Mode.bus: MotisMode.bus,
    Mode.tram: MotisMode.tram,
    Mode.subway: MotisMode.subway,
    Mode.rail: MotisMode.rail,
    Mode.ferry: MotisMode.ferry,
    Mode.cable_car: MotisMode.cable_car,
    Mode.funicular: MotisMode.funicular,
    Mode.car: MotisMode.car,
    Mode.transit: MotisMode.transit,
}


def internal_modes_to_motis_string(modes: List[Mode]) -> str:
    """
    Converts a list of internal `Mode` enums to the final comma-separated
    string required by the MOTIS API, intelligently handling the TRANSIT category.

    Example:
      [Mode.transit, Mode.walk] -> "TRANSIT,WALK" (because MOTIS understands "TRANSIT")
      [Mode.subway, Mode.bus, Mode.walk] -> "SUBWAY,BUS,WALK"
    """
    motis_modes = [INTERNAL_TO_MOTIS_MODE_MAP.get(m) for m in modes]

    # Filter out any modes that couldn't be mapped
    valid_motis_modes = [m for m in motis_modes if m is not None]

    # The MOTIS API itself understands the "TRANSIT" meta-mode. If the user
    # selected our internal `Mode.transit`, we should pass "TRANSIT" directly
    # to MOTIS rather than expanding it. MOTIS will do the expansion.
    # The only time we need to expand is if our internal logic needs to know
    # the specific modes. The API call does not.

    return ",".join(sorted([m.value for m in valid_motis_modes]))
