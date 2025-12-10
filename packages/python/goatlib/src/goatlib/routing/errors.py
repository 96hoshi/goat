class RoutingError(Exception):
    """Base exception for routing adapter failures."""

    pass


class ParsingError(RoutingError):
    """Raised when an API response cannot be parsed correctly."""

    pass


class ServiceError(RoutingError):
    """Raised when the routing service is unavailable or returns an error."""

    pass
