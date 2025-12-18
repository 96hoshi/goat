import json
import logging
from typing import Any, Dict, Optional, Self

import httpx

from goatlib.routing.errors import ParsingError, ServiceError

logger = logging.getLogger(__name__)


class MotisServiceClient:
    """
    Client for MOTIS routing services.

    Handles real API requests using the standard MOTIS API format.
    """

    base_url: str
    plan_endpoint: str
    _http_client: httpx.AsyncClient

    def __init__(
        self: Self,
        base_url: str = "https://api.transitous.org",
        plan_endpoint: str = "/api/v5/plan",
        one_to_all_endpoint: str = "/api/v1/one-to-all",
    ) -> None:
        self.base_url = base_url
        self.plan_endpoint = plan_endpoint
        self.one_to_all_endpoint = one_to_all_endpoint
        self._http_client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def __aenter__(self: Self) -> Self:
        """
        Enter the runtime context related to this object.
        Initializes the client if needed and returns itself.
        """
        return self

    async def __aexit__(
        self: Self,
        exc_type: Optional[type],
        exc_val: Optional[Exception],
        exc_tb: Optional[Any],
    ) -> None:
        """
        Exit the runtime context and close resources.
        This is called automatically when exiting an `async with` block.
        """
        await self.close()

    async def plan(self: Self, motis_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a routing plan request.

        Args:
            motis_request: Request in MOTIS-specific format

        Returns:
            Raw MOTIS response data

        Raises:
            ServiceError: If the MOTIS service is unavailable or returns an error
            ParsingError: If the response format is invalid
        """
        return await self._make_plan_api_request(motis_request)

    async def one_to_all(self: Self, motis_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a one-to-all routing request.

        Args:
            motis_request: Request in MOTIS one-to-all specific format

        Returns:
            Raw MOTIS one-to-all response data

        Raises:
            ServiceError: If the MOTIS service is unavailable or returns an error
            ParsingError: If the response format is invalid
        """
        # For now, one-to-all only supports real API calls, not fixtures
        return await self._make_one_to_all_api_request(motis_request)

    async def _make_plan_api_request(
        self: Self, api_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info(f"Making async MOTIS plan request to {self.plan_endpoint}")
        try:
            response = await self._http_client.get(
                self.plan_endpoint,
                params=api_params,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()

        except httpx.RequestError as e:
            if isinstance(e, httpx.TimeoutException):
                log_msg = f"Request to MOTIS service timed out at {e.request.url}"
            if isinstance(e, httpx.HTTPStatusError):
                log_msg = f"MOTIS service returned error {e.response.status_code} for request to {e.request.url}"
            if isinstance(e, httpx.ConnectionError):
                log_msg = f"Connection error occurred while requesting MOTIS service at {e.request.url}"
            else:
                log_msg = f"An unexpected request error occurred: {e}"
            logger.error(log_msg)
            raise ServiceError("MOTIS service request failed to complete.") from e

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response from MOTIS service: {e}")
            raise ParsingError("Invalid response format from MOTIS service.") from e

    async def _make_one_to_all_api_request(
        self: Self, api_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info(
            f"Making async MOTIS one-to-all request to {self.one_to_all_endpoint}"
        )
        try:
            response = await self._http_client.get(
                self.one_to_all_endpoint,
                params=api_params,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()

        except httpx.RequestError as e:
            if isinstance(e, httpx.TimeoutException):
                log_msg = (
                    f"Request to MOTIS one-to-all service timed out at {e.request.url}"
                )
            if isinstance(e, httpx.HTTPStatusError):
                log_msg = f"MOTIS one-to-all service returned error {e.response.status_code} for request to {e.request.url}"
            if isinstance(e, httpx.ConnectionError):
                log_msg = f"Connection error occurred while requesting MOTIS one-to-all service at {e.request.url}"
            else:
                log_msg = f"An unexpected request error occurred: {e}"
            logger.error(log_msg)
            raise ServiceError(
                "MOTIS one-to-all service request failed to complete."
            ) from e

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse JSON response from MOTIS one-to-all service: {e}"
            )
            raise ParsingError(
                "Invalid response format from MOTIS one-to-all service."
            ) from e

    async def close(self: Self) -> None:
        """Closes the underlying HTTP client."""
        if hasattr(self, "_http_client"):
            await self._http_client.aclose()
