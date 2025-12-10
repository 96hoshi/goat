import asyncio
import logging
from pathlib import Path
from typing import Self

from goatlib.routing.errors import ParsingError, RoutingError, ServiceError
from goatlib.routing.interfaces.routing_service import RoutingService
from goatlib.routing.schemas.ab_routing import (
    ABRoutingRequest,
    ABRoutingResponse,
)
from goatlib.routing.schemas.catchment_area_transit import (
    TransitCatchmentAreaRequest,
    TransitCatchmentAreaResponse,
)

from .motis_client import MotisServiceClient
from .motis_converters import (
    parse_motis_one_to_all_response,
    parse_motis_response,
    translate_to_motis_one_to_all_request,
    translate_to_motis_request,
)

logger = logging.getLogger(__name__)


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

    async def get_transit_catchment_area(
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
    use_fixtures: bool = True,
    fixture_path: Path | str = None,
    base_url: str = "https://api.transitous.org",
) -> MotisPlanApiAdapter:
    """
    Convenience function to create a MOTIS adapter instance.

    Args:
        use_fixtures: Whether to use fixture data instead of real API calls
        fixture_path: Path to the directory containing MOTIS fixture data
        base_url: Base URL for the MOTIS API

    Returns:
        Configured MotisPlanApiAdapter instance

    """
    motis_client = MotisServiceClient(
        use_fixtures=use_fixtures,
        fixture_path=fixture_path,
        base_url=base_url,
    )
    return MotisPlanApiAdapter(motis_client)
