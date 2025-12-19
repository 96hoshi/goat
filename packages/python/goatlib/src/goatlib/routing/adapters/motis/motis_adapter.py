import asyncio
import logging
import time
from collections import defaultdict
from typing import Self

import fast_routing_py as routing_rs

from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor
from goatlib.routing.errors import ParsingError, RoutingError, ServiceError
from goatlib.routing.interfaces.routing_service import RoutingService
from goatlib.routing.schemas.ab_routing import (
    ABRoutingRequest,
    ABRoutingResponse,
)
from goatlib.routing.schemas.base import CatchmentAreaRoutingModePT, Coordinates
from goatlib.routing.schemas.catchment import (
    CatchmentRequest,
    CatchmentResponse,
    CutoffResult,
)
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaResponse,
)

from .motis_client import MotisServiceClient
from .motis_converters import (
    extract_bus_stations_for_buffering,
    parse_motis_one_to_all_response,
    parse_motis_response,
    translate_to_motis_one_to_all_request,
    translate_to_motis_request,
)

logger = logging.getLogger(__name__)
PATH = "/app/packages/python/goatlib/tests/data/network/network.parquet"


class MotisPlanApiAdapter(RoutingService):
    """
    Adapter that makes the MOTIS service interface compatible with our
    standardized routing interface.

    This adapter translates between our internal ABRouting schemas and
    the MOTIS-specific API format, following the Adapter pattern.
    """

    def __init__(self: Self, motis_client: MotisServiceClient) -> None:
        """
        Initialize the adapter with a MOTIS service client.

        Args:
            motis_client: The MOTIS service client instance
        """
        self.motis_client = motis_client
        self.network_path = PATH  # Set the default network path
        self.transit_modes = [
            CatchmentAreaRoutingModePT.bus,
            CatchmentAreaRoutingModePT.tram,
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.rail,
            CatchmentAreaRoutingModePT.ferry,
            CatchmentAreaRoutingModePT.cable_car,
            CatchmentAreaRoutingModePT.gondola,
            CatchmentAreaRoutingModePT.funicular,
        ]

    async def route(self: Self, request: ABRoutingRequest) -> ABRoutingResponse:
        """
        Execute a routing request using the MOTIS plan API.
        Args:
            request: ABRoutingRequest containing origin, destination, modes, etc.
        Returns:
            ABRoutingResponse with routing results
        Raises:
            ParsingError: If request/response format is invalid
            ServiceError: If network/service connection fails
            RoutingError: For unexpected errors
        """

        try:
            # Translate our internal request to MOTIS format
            request_data = translate_to_motis_request(request)

            # Make the network call to MOTIS
            motis_response = await self.motis_client.plan(request_data)

            # Parse MOTIS response to our internal format
            response_data = parse_motis_response(motis_response)

            return response_data

        except (asyncio.TimeoutError, ConnectionError) as e:
            # Network-specific issues
            logger.error(f"Network error while contacting MOTIS: {e}")
            raise ServiceError("Failed to connect to the routing service") from e

        except ParsingError as e:
            # Request/response format issues - log and re-raise as-is
            logger.warning(f"Parsing error in MOTIS routing: {e}")
            raise

        except ServiceError:
            # Service errors from lower layers - re-raise as-is
            raise

        except Exception as e:
            # Unexpected errors - wrap in RoutingError
            logger.error(f"Unexpected error during MOTIS routing: {e}")
            raise RoutingError("An unexpected internal error occurred") from e

    async def get_isochrone(self, request: CatchmentRequest) -> CatchmentResponse:
        test_file = PATH

        results: list[CutoffResult] = []

        try:
            motis_request = TransitCatchmentAreaRequest(
                starting_points=request.starting_points,
                transit_modes=request.transit_modes,
                cutoffs=request.cutoffs,
                access_settings=AccessEgressSettings.create_walk_settings(),
                egress_settings=AccessEgressSettings.create_walk_settings(),
            )

            motis_req = translate_to_motis_one_to_all_request(motis_request)
            raw_response = await self.motis_client.one_to_all(motis_req)
            raw_stations = extract_bus_stations_for_buffering(raw_response)

            if not raw_stations:
                return CatchmentResponse(
                    results=[
                        CutoffResult(
                            cutoff_minutes=c,
                            pt_stations_found=0,
                            successful_routing=0,
                            total_reachable_nodes=0,
                            raw_response={},
                        )
                        for c in request.cutoffs
                    ],
                    metadata={"engine": "motis + rust"},
                )
            # ──────────────────────────────────────────────────────
            # 2. Prepare stations + eligibility per cutoff
            # ──────────────────────────────────────────────────────
            stations: list[Coordinates] = []
            station_times: list[int] = []

            for s in raw_stations:
                lon, lat = s["coordinates"]
                t = int(s.get("duration_minutes", 0))

                stations.append(Coordinates(lat=lat, lon=lon))
                station_times.append(t)

            # For each cutoff, which station indices are valid?
            stations_per_cutoff: dict[int, list[int]] = defaultdict(list)

            for idx, t in enumerate(station_times):
                for cutoff in request.cutoffs:
                    if t <= cutoff:
                        stations_per_cutoff[cutoff].append(idx)

            # ──────────────────────────────────────────────────────
            # 3. Rust routing (single call, multiple cutoffs)
            # ──────────────────────────────────────────────────────
            network_processor_start = time.time()

            with InMemoryNetworkProcessor(input_path=str(test_file)) as proc:
                logger.info(
                    f"Creating network subset around {motis_request.starting_points[0]}"
                )

                # Time network loading
                subset = proc.load_network(
                    center=request.starting_points[0],
                    buffer_radius=1500.0,
                )

                # Time artificial nodes creation
                output_path, node_ids = proc.create_artificial_nodes_for_points(
                    stations, subset, search_radius_m=200.0
                )

                logger.info(
                    f"Running Rust isochrone calculation for {len(stations)} stations"
                )

                # Time Rust network loading
                rust_load_start = time.time()
                network = routing_rs.load_network(output_path)
                rust_load_time = time.time() - rust_load_start
                logger.info(
                    f"Rust routing_rs.load_network completed in {rust_load_time:.3f}s"
                )

                # Use the maximum egress time from the egress settings
                # This is the configured maximum last-mile walking time
                max_egress_time = motis_request.egress_settings.max_time
                max_cutoff = max_egress_time * 60  # Convert to seconds for Rust

                logger.info(
                    f"Calculating last mile with max cutoff {max_cutoff} seconds"
                )

                # Time the main Rust isochrone calculation
                rust_calc_start = time.time()
                routing_results = network.calculate_multiple_isochrones(
                    start_nodes=node_ids,
                    max_cost=max_cutoff,
                )
                rust_calc_time = time.time() - rust_calc_start
                logger.info(
                    f"Rust calculate_multiple_isochrones completed in {rust_calc_time:.3f}s"
                )
            # ──────────────────────────────────────────────────────
            # 4. Aggregate per cutoff
            # ──────────────────────────────────────────────────────
            results: list[CutoffResult] = []

            for cutoff in request.cutoffs:
                valid_station_indices = set(stations_per_cutoff[cutoff])

                successful = 0
                total_reachable = 0

                for station_idx, iso in enumerate(routing_results):
                    if station_idx in valid_station_indices:
                        successful += 1
                        total_reachable += iso.reachable_nodes

                results.append(
                    CutoffResult(
                        cutoff_minutes=cutoff,
                        pt_stations_found=len(valid_station_indices),
                        successful_routing=successful,
                        total_reachable_nodes=total_reachable,
                        raw_response={"routing_summary": routing_results},
                    )
                )

            return CatchmentResponse(
                results=results,
                metadata={
                    "engine": "motis + rust",
                    "stations_total": len(stations),
                },
            )

        finally:
            await self.motis_client.close()

    async def _get_transit_catchment_area(
        self: Self, request: TransitCatchmentAreaRequest
    ) -> TransitCatchmentAreaResponse:
        """
        Execute a transit catchment area request using MOTIS one-to-all API.

        Args:
            request: Transit catchment area request

        Returns:
            TransitCatchmentAreaResponse with isochrone polygons

        Raises:
            ParsingError: If request/response format is invalid
            ServiceError: If network/service connection fails
            RoutingError: For unexpected errors
        """
        try:
            # Translate our internal request to MOTIS one-to-all format
            request_data = translate_to_motis_one_to_all_request(request)

            # Make the network call to MOTIS
            motis_response = await self.motis_client.one_to_all(request_data)

            # Parse MOTIS response to our internal format
            response_data = parse_motis_one_to_all_response(motis_response, request)

            return response_data

        except (asyncio.TimeoutError, ConnectionError) as e:
            # Network-specific issues
            logger.error(f"Network error while contacting MOTIS one-to-all: {e}")
            raise ServiceError("Failed to connect to the routing service") from e

        except ParsingError as e:
            # Request/response format issues - log and re-raise as-is
            logger.warning(f"Parsing error in MOTIS catchment area: {e}")
            raise

        except ServiceError:
            # Service errors from lower layers - re-raise as-is
            raise

        except Exception as e:
            # Unexpected errors - wrap in RoutingError
            logger.error(f"Unexpected error during MOTIS catchment area request: {e}")
            raise RoutingError("An unexpected internal error occurred") from e


def create_motis_adapter(
    base_url: str = "https://api.transitous.org",
) -> MotisPlanApiAdapter:
    """
    Convenience function to create a MOTIS adapter instance.

    Args:
        base_url: Base URL for the MOTIS API

    Returns:
        Configured MotisPlanApiAdapter instance

    """
    motis_client = MotisServiceClient(
        base_url=base_url,
    )
    return MotisPlanApiAdapter(motis_client)
