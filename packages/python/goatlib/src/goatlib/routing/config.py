from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Nested Models for Configuration ---
# These models represent the structure of your settings dictionary.


class TransitAccessModeLimits(BaseModel):
    """Configuration for access/egress modes like walking or biking."""

    max_time: int = Field(..., description="Maximum duration in minutes for this mode.")
    min_speed: float = Field(..., description="Minimum assumed speed in km/h.")
    max_speed: float = Field(..., description="Maximum assumed speed in km/h.")
    default_speed: float = Field(..., description="Default assumed speed in km/h.")


class TransitLimits(BaseModel):
    """Configuration specific to transit routing."""

    max_traveltime: int = Field(90, description="Maximum total travel time in minutes.")
    max_transfers: int = Field(10, description="Maximum number of transfers allowed.")

    walk: TransitAccessModeLimits = TransitAccessModeLimits(
        max_time=30, min_speed=1.0, max_speed=10.0, default_speed=5.0
    )
    bicycle: TransitAccessModeLimits = TransitAccessModeLimits(
        max_time=45, min_speed=5.0, max_speed=30.0, default_speed=15.0
    )


class ActiveMobilityLimits(BaseModel):
    """Configuration for active mobility like walking or cycling."""

    max_traveltime: int = Field(45, description="Maximum travel time in minutes.")
    max_speed: int = Field(25, description="Maximum speed in km/h.")


class MotorizedMobilityLimits(BaseModel):
    """Configuration for private motorized mobility like cars."""

    max_traveltime: int = Field(90, description="Maximum travel time in minutes.")
    max_speed: Optional[int] = Field(
        None, description="Maximum speed in km/h (optional)."
    )


class DistanceLimits(BaseModel):
    """Configuration for distance-based limits."""

    max_distance: int = Field(20000, description="Maximum distance in meters.")


# --- The Main BaseSettings Model ---


class RoutingSettings(BaseSettings):
    """
    Manages all routing limit configurations.
    Reads from environment variables or uses defaults.
    """

    # Define the top-level keys from your original dictionary
    active_mobility: ActiveMobilityLimits = ActiveMobilityLimits()
    motorized_mobility: MotorizedMobilityLimits = MotorizedMobilityLimits()
    distance: DistanceLimits = DistanceLimits()
    transit: TransitLimits = TransitLimits()

    # Configure Pydantic to look for environment variables
    # e.g., an env var `ROUTING_TRANSIT__MAX_TRANSFERS=5` would override the default.
    model_config = SettingsConfigDict(
        env_prefix="ROUTING_",  # A prefix for all environment variables
        env_nested_delimiter="__",  # Use double underscore for nested objects
    )


# --- Singleton Instance and Legacy Aliases ---

routing_settings = RoutingSettings()
