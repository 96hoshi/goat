import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from goatlib.routing.errors import ParsingError
from goatlib.routing.schemas.ab_routing import (
    ABLeg,
    ABRoute,
    ABRoutingRequest,
    ABRoutingResponse,
)
from goatlib.routing.schemas.base import (
    AccessEgressMode,
    Coordinates,
    Mode,
    RoutingProvider,
)
from goatlib.routing.schemas.catchment_area_transit import (
    CatchmentAreaPolygon,
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaResponse,
)

from ..distance_utils import haversine_distance
from .motis_mappings import (
    MOTIS_TO_INTERNAL_MODE_MAP,
    MotisMode,
    internal_modes_to_motis_string,
)
from .motis_settings import motis_settings

logger = logging.getLogger(__name__)


# =============================================================================
# SHARED UTILITY FUNCTIONS
# =============================================================================


def _validate_coordinates(lat: Optional[float], lon: Optional[float]) -> bool:
    """Validate coordinate bounds. Centralized validation logic."""
    return (
        lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
    )


def _safe_parse_datetime(
    time_str: str, fallback: Optional[datetime] = None
) -> datetime:
    """Safely parse ISO datetime string with fallback."""
    try:
        return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return fallback or datetime.now()


def _build_location_string(lat: float, lon: float) -> str:
    """Build coordinate string for MOTIS API."""
    return f"{lat},{lon}"


def _extract_place_data(place: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate place data from MOTIS response."""
    fields = motis_settings.one_to_all_fields

    lat = place.get(fields.lat)
    lon = place.get(fields.lon)

    if not _validate_coordinates(lat, lon):
        logger.warning(f"Invalid coordinates: lat={lat}, lon={lon}")
        return {}

    return {
        "lat": lat,
        "lon": lon,
        "name": place.get(fields.name, "Unknown"),
        "stop_id": place.get(fields.stop_id, ""),
        "coordinates": [lon, lat],  # GeoJSON format
    }


# =============================================================================
# ROUTING API FUNCTIONS
# =============================================================================


def translate_to_motis_request(request: ABRoutingRequest) -> Dict[str, Any]:
    """Convert ABRoutingRequest to MOTIS v5/plan GET API parameters."""
    if not request:
        raise ParsingError("Routing request cannot be None or empty")

    if request.provider != RoutingProvider.motis:
        raise ParsingError(
            f"MotisPlanApiAdapter cannot handle requests for provider {request.provider}"
        )

    params = motis_settings.request_params
    defaults = motis_settings.defaults

    # Build core parameters using utility functions
    api_params = {
        params.origin: _build_location_string(request.origin.lat, request.origin.lon),
        params.destination: _build_location_string(
            request.destination.lat, request.destination.lon
        ),
        params.detailed_transfers: request.detailed_transfers
        or defaults.detailed_transfers,
        params.num_itineraries: defaults.num_itineraries,
        params.time_is_arrival: request.time_is_arrival or defaults.time_is_arrival,
    }

    # Add time if provided
    if request.time:
        api_params[params.time] = request.time.isoformat()

    # Handle transport modes using utility function from motis_mappings
    motis_modes_string = internal_modes_to_motis_string(request.modes)
    api_params[params.transit_modes] = (
        motis_modes_string
        if motis_modes_string
        else ",".join(mode.value for mode in defaults.transit_modes)
    )

    return api_params


def parse_motis_response(motis_data: Dict[str, Any]) -> ABRoutingResponse:
    """
    Convert MOTIS API response to internal route objects.

    Raises:
        ParsingError: If any part of the response data is invalid.
    """
    routes = []

    # MOTIS response has "itineraries" at the top level
    itineraries = motis_data.get("itineraries", [])

    if itineraries:
        for idx, itinerary in enumerate(itineraries):
            try:
                # This will raise an exception if the itinerary is malformed
                route = _convert_itinerary_to_route(itinerary, idx)
                routes.append(route)
            except (KeyError, ValueError, ValidationError) as e:
                logger.error(f"Failed to parse MOTIS itinerary #{idx}: {e}")
                raise ParsingError(
                    "Could not parse MOTIS itinerary due to invalid data."
                ) from e

    return ABRoutingResponse(routes=routes)


def _convert_itinerary_to_route(
    itinerary: Dict[str, Any], itinerary_idx: int
) -> ABRoute:
    """Convert a MOTIS itinerary to an ABRoute."""
    itinerary_fields = motis_settings.itinerary_fields

    original_legs_data = itinerary[itinerary_fields.legs]

    if not original_legs_data:
        raise ParsingError(f"Itinerary {itinerary_idx} has no legs.")

    parsed_legs = [
        _convert_leg_to_ab_leg(leg, itinerary_idx, leg_idx)
        for leg_idx, leg in enumerate(original_legs_data)
    ]

    # Calculate total distance by summing all leg distances
    total_distance = sum(
        leg.distance for leg in parsed_legs if leg.distance is not None
    )

    return ABRoute(
        route_id=f"motis_route_{itinerary_idx}",
        duration=itinerary[itinerary_fields.duration],
        distance=total_distance,
        departure_time=parsed_legs[0].departure_time,
        legs=parsed_legs,
    )


def _convert_leg_to_ab_leg(
    leg: Dict[str, Any], itinerary_idx: int, leg_idx: int
) -> ABLeg:
    """Convert a MOTIS leg to an ABLeg object."""
    leg_fields = motis_settings.leg_fields

    mode = _extract_transport_mode(leg)
    origin, destination = _extract_locations(leg)
    departure_time, arrival_time = _extract_timing(leg)

    distance = leg.get(leg_fields.distance, 0.0)

    # If MOTIS doesn't provide distance, calculate it using coordinate distance
    if distance <= 0:
        # Use Haversine distance for all modes when MOTIS doesn't provide distance
        distance = haversine_distance(
            origin.lat, origin.lon, destination.lat, destination.lon
        )

    duration = leg[leg_fields.duration]
    if duration <= 0:
        # If MOTIS doesn't provide duration, calculate from actual times
        time_difference: timedelta = arrival_time - departure_time
        duration = int(time_difference.total_seconds())
    else:
        # Verify that MOTIS duration matches the actual time difference
        actual_duration = int((arrival_time - departure_time).total_seconds())
        if abs(duration - actual_duration) > 60:  # More than 1 minute difference
            logger.warning(
                f"MOTIS duration ({duration}s) doesn't match actual time difference ({actual_duration}s), using calculated duration"
            )
            duration = actual_duration

    return ABLeg(
        leg_id=f"i{itinerary_idx}_l{leg_idx}",
        mode=mode,
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        arrival_time=arrival_time,
        duration=duration,
        distance=distance,
    )


def _extract_transport_mode(leg: Dict[str, Any]) -> Mode:
    """Extract and convert transport mode from MOTIS leg."""
    leg_fields = motis_settings.leg_fields
    mode_str = leg[leg_fields.mode]

    if mode_str in MOTIS_TO_INTERNAL_MODE_MAP:
        return MOTIS_TO_INTERNAL_MODE_MAP[mode_str]

    motis_mode = getattr(MotisMode, mode_str, None)
    if motis_mode and motis_mode in MOTIS_TO_INTERNAL_MODE_MAP:
        return MOTIS_TO_INTERNAL_MODE_MAP[motis_mode]

    logger.warning(f"Unknown mode {mode_str} in MOTIS leg")
    return Mode.walk


def _extract_locations(leg: Dict[str, Any]) -> tuple[Coordinates, Coordinates]:
    """Extract origin and destination locations from MOTIS leg."""
    leg_fields = motis_settings.leg_fields
    location_fields = motis_settings.location_fields

    from_data = leg[leg_fields.from_loc]
    to_data = leg[leg_fields.to_loc]

    origin = Coordinates(
        lat=from_data[location_fields.lat],
        lon=from_data[location_fields.lon],
    )

    destination = Coordinates(
        lat=to_data[location_fields.lat],
        lon=to_data[location_fields.lon],
    )

    return origin, destination


def _extract_timing(leg: Dict[str, Any]) -> tuple[datetime, datetime]:
    """Extract departure and arrival times from MOTIS leg."""
    leg_fields = motis_settings.leg_fields
    start_time_str = leg[leg_fields.start_time]
    end_time_str = leg[leg_fields.end_time]

    # Use centralized datetime parsing
    departure_time = _safe_parse_datetime(start_time_str)
    arrival_time = _safe_parse_datetime(end_time_str, departure_time)

    # Validate and correct timing if needed
    if arrival_time <= departure_time:
        duration_from_motis = leg.get(leg_fields.duration, 0)
        if duration_from_motis > 0:
            arrival_time = departure_time + timedelta(seconds=duration_from_motis)
        else:
            arrival_time = departure_time + timedelta(minutes=1)
            logger.warning(
                "MOTIS provided invalid timing data for leg, using minimal duration"
            )

    return departure_time, arrival_time


# =====================================================================
# ONE-TO-ALL CONVERTER FUNCTIONS
# =====================================================================


def translate_to_motis_one_to_all_request(
    request: TransitCatchmentAreaRequest,
) -> Dict[str, Any]:
    """Convert TransitCatchmentAreaRequest to MOTIS one-to-all API parameters."""
    params = motis_settings.one_to_all_params
    defaults = motis_settings.one_to_all_defaults

    # Extract starting point coordinates (handle list format, use first point)
    starting_point = (
        request.starting_points[0]
        if isinstance(request.starting_points, list)
        else request.starting_points
    )
    lat, lon = starting_point.lat, starting_point.lon

    # Build core parameters
    api_params = {
        params.origin: _build_location_string(lat, lon),
        params.max_travel_time: max(
            request.cutoffs
        ),  # Use max cutoff as the time limit
        params.arrive_by: False,
        # Omit time parameter to use current time
    }

    # Handle transit modes using utility function
    transit_modes_string = internal_modes_to_motis_string(request.transit_modes)
    api_params[params.transit_modes] = (
        transit_modes_string
        if transit_modes_string
        else ",".join(defaults.transit_modes)
    )

    # Handle access/egress modes using utility function
    api_params[params.pre_transit_modes] = internal_modes_to_motis_string(
        [request.access_mode]
    )
    api_params[params.post_transit_modes] = internal_modes_to_motis_string(
        [request.egress_mode]
    )

    # Add max transfers
    api_params[params.max_transfers] = request.max_transfers

    # Access settings (pre-transit)
    if request.access_settings:
        access = request.access_settings
        access_time_seconds = access.max_time * 60
        access_speed_ms = access.speed / 3.6  # km/h to m/s

        # Update access-related parameters
        update_params = {
            params.max_pre_transit_time: access_time_seconds,
        }

        if access.mode == AccessEgressMode.walk:
            update_params[params.pedestrian_speed] = access_speed_ms
        elif access.mode == AccessEgressMode.bicycle:
            update_params[params.cycling_speed] = access_speed_ms

        api_params.update(update_params)

    # Egress settings (post-transit)
    if request.egress_settings:
        egress = request.egress_settings
        egress_time_seconds = egress.max_time * 60

        api_params[params.max_post_transit_time] = egress_time_seconds

    # Add default values
    api_params.update(
        {
            params.pedestrian_profile: defaults.pedestrian_profile,
            params.elevation_costs: defaults.elevation_costs,
            params.use_routed_transfers: defaults.use_routed_transfers,
        }
    )

    return api_params


def parse_motis_one_to_all_response(
    motis_data: Dict[str, Any], request: TransitCatchmentAreaRequest
) -> TransitCatchmentAreaResponse:
    """
    Convert MOTIS one-to-all API response to TransitCatchmentAreaResponse.

    Args:
        motis_data: Raw MOTIS one-to-all response
        request: Original request (needed for cutoff processing)

    Returns:
        TransitCatchmentAreaResponse with reachable locations (geometry optional)

    Raises:
        ParsingError: If the response data is invalid
    """
    try:
        fields = motis_settings.one_to_all_fields

        # Extract reachable locations from MOTIS response
        reachable_locations = motis_data.get(fields.all, [])

        if not reachable_locations:
            logger.warning("No reachable locations found in MOTIS one-to-all response")
            return TransitCatchmentAreaResponse(polygons=[])

        # Group locations by travel time cutoffs
        cutoffs = request.cutoffs
        polygons = []

        for cutoff in cutoffs:
            # Find all locations reachable within this cutoff (in minutes)
            reachable_within_cutoff = []

            for loc in reachable_locations:
                duration_minutes = loc.get(fields.travel_time, 0)

                if duration_minutes <= cutoff:
                    place = loc.get(fields.place, {})
                    if not place:
                        continue

                    # Use centralized place data extraction
                    place_data = _extract_place_data(place)
                    if place_data:  # Only add if coordinates are valid
                        place_data["duration_minutes"] = duration_minutes
                        reachable_within_cutoff.append(place_data)

            if reachable_within_cutoff:
                # Convert place data to Coordinates objects for points field
                coordinate_points = [
                    Coordinates(lat=place["lat"], lon=place["lon"])
                    for place in reachable_within_cutoff
                ]

                # Create polygon without geometry for now (geometry calculation optional)
                polygon = CatchmentAreaPolygon(
                    travel_time=cutoff,
                    points=coordinate_points,
                    geometry=None,  # Geometry calculation optional, can be added later
                )
                polygons.append(polygon)

        return TransitCatchmentAreaResponse(
            polygons=polygons,
            metadata={
                "total_locations": len(reachable_locations),
                "source": "motis_one_to_all",
                "cutoffs_requested": request.cutoffs,
                "polygons_generated": len(polygons),
                "total_reachable_points": sum(
                    len(polygon.points) for polygon in polygons
                ),
            },
        )

    except (KeyError, ValueError) as e:
        logger.error(f"Failed to parse MOTIS one-to-all response: {e}")
        raise ParsingError(
            "Could not parse MOTIS one-to-all response due to invalid data."
        ) from e


def extract_bus_stations_for_buffering(
    motis_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Extract bus station information from MOTIS one-to-all response for buffer analysis."""
    fields = motis_settings.one_to_all_fields
    stations = []

    reachable_locations = motis_data.get(fields.all, [])

    for loc in reachable_locations:
        place = loc.get(fields.place, {})
        if not place:
            continue

        # Use centralized place data extraction
        place_data = _extract_place_data(place)
        if not place_data:  # Skip invalid coordinates
            continue

        # Add duration from location
        place_data["duration_minutes"] = loc.get(fields.travel_time, 0)
        stations.append(place_data)

    logger.info(f"Extracted {len(stations)} valid bus stations for buffering")
    return stations
