import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import pytest
from goatlib.routing.adapters.motis.motis_adapter import create_motis_adapter
from goatlib.routing.adapters.motis.motis_converters import (
    parse_motis_one_to_all_response,
    translate_to_motis_one_to_all_request,
)
from goatlib.routing.schemas.base import (
    AccessEgressMode,
    CatchmentAreaRoutingModePT,
    Coordinates,
)
from goatlib.routing.schemas.catchment_area_transit import (
    AccessEgressSettings,
    TransitCatchmentAreaRequest,
)

logger = logging.getLogger(__name__)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def motis_adapter_online():
    """Fixture providing a MOTIS adapter instance for online tests."""
    adapter = create_motis_adapter()
    yield adapter
    # Cleanup is handled in tests using async context


@pytest.fixture
def simple_berlin_request():
    """Simple Berlin request for basic integration tests."""
    return TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=52.520008, lon=13.404954)],
        cutoffs=[15, 30],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        access_settings=AccessEgressSettings.create_walk_settings(max_time=10),
        egress_settings=AccessEgressSettings.create_walk_settings(max_time=10),
    )


@pytest.fixture
def munich_request():
    """Munich request for testing different scenarios."""
    return TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=48.137154, lon=11.576124)],
        cutoffs=[10, 20, 30],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )


@pytest.fixture
def plausibility_tester():
    """Fixture providing a MotisOneToAllPlausibilityTester instance."""
    return MotisOneToAllPlausibilityTester()


# ============================================================================
# PLAUSIBILITY TESTER CLASS
# ============================================================================


class MotisOneToAllPlausibilityTester:
    """Comprehensive plausibility tester for MOTIS one-to-all responses."""

    def __init__(self):
        self.tolerance_meters = 1000  # 1km tolerance for location validation
        self.max_reasonable_travel_time = 120  # 2 hours max (minutes)
        self.min_locations_expected = 5  # Minimum locations we expect to reach

    def validate_raw_motis_response(self, motis_data: Dict[str, Any]) -> List[str]:
        """Validate the raw MOTIS response structure and content."""
        issues = []

        if not isinstance(motis_data, dict):
            issues.append("MOTIS response is not a dictionary")
            return issues

        if "all" not in motis_data:
            issues.append("Missing 'all' field in MOTIS response")
            return issues

        reachable_locations = motis_data.get("all", [])

        if not isinstance(reachable_locations, list):
            issues.append("'all' field is not a list")
            return issues

        if len(reachable_locations) < self.min_locations_expected:
            issues.append(
                f"Too few reachable locations: {len(reachable_locations)} < {self.min_locations_expected}"
            )

        for idx, location in enumerate(reachable_locations):
            location_issues = self._validate_location_entry(location, idx)
            issues.extend(location_issues)

        return issues

    def _validate_location_entry(self, location: Dict[str, Any], idx: int) -> List[str]:
        """Validate an individual location entry from MOTIS response."""
        issues = []
        prefix = f"Location {idx}:"

        required_fields = ["place", "duration"]
        for field in required_fields:
            if field not in location:
                issues.append(f"{prefix} Missing required field '{field}'")
                continue

        duration = location.get("duration", 0)
        if duration > self.max_reasonable_travel_time:
            issues.append(f"{prefix} Unreasonably long travel time: {duration} min")

        place = location.get("place", {})
        if not isinstance(place, dict):
            issues.append(f"{prefix} 'place' field is not a dictionary")
            return issues

        place_issues = self._validate_place_data(place, idx)
        issues.extend(place_issues)

        return issues

    def _validate_place_data(self, place: Dict[str, Any], idx: int) -> List[str]:
        """Validate place data within a location entry."""
        issues = []
        prefix = f"Location {idx} place:"

        if "lon" not in place and "lng" not in place:
            issues.append(f"{prefix} Missing longitude field (lon/lng)")
        if "lat" not in place:
            issues.append(f"{prefix} Missing latitude field")

        lon = place.get("lon", place.get("lng"))
        lat = place.get("lat")

        if lon is not None:
            if not isinstance(lon, (int, float)):
                issues.append(f"{prefix} Longitude is not numeric: {type(lon)}")
            elif not -180 <= lon <= 180:
                issues.append(f"{prefix} Invalid longitude: {lon}")

        if lat is not None:
            if not isinstance(lat, (int, float)):
                issues.append(f"{prefix} Latitude is not numeric: {type(lat)}")
            elif not -90 <= lat <= 90:
                issues.append(f"{prefix} Invalid latitude: {lat}")

        return issues

    async def run_comprehensive_test(self) -> Dict[str, Any]:
        """Run comprehensive plausibility testing."""
        logger.info("🧪 MOTIS One-to-All Plausibility Test")

        adapter = create_motis_adapter()

        try:
            request = TransitCatchmentAreaRequest(
                starting_points=[{"lat": 48.1351, "lon": 11.5820}],
                transit_modes=[
                    CatchmentAreaRoutingModePT.bus,
                    CatchmentAreaRoutingModePT.subway,
                ],
                cutoffs=[10, 20, 30],
                access_settings=AccessEgressSettings.create_walk_settings(),
                egress_settings=AccessEgressSettings.create_walk_settings(),
            )

            motis_request_data = translate_to_motis_one_to_all_request(request)
            motis_response = await adapter.motis_client.one_to_all(motis_request_data)

            raw_issues = self.validate_raw_motis_response(motis_response)
            parsed_response = parse_motis_one_to_all_response(motis_response, request)
            parsed_issues = []

            if parsed_response is None:
                parsed_issues.append("Failed to parse MOTIS response")
            elif not hasattr(parsed_response, "polygons"):
                parsed_issues.append("Parsed response missing polygons")
            elif len(parsed_response.polygons) == 0:
                parsed_issues.append("No polygons generated from response")

            reachable_locations = motis_response.get("all", [])
            travel_times = [loc.get("duration", 0) for loc in reachable_locations]

            try:
                adapter_response = await adapter._get_transit_catchment_area(request)
                adapter_polygon_count = (
                    len(adapter_response.polygons) if adapter_response else 0
                )
                direct_polygon_count = (
                    len(parsed_response.polygons) if parsed_response else 0
                )
            except Exception as e:
                logger.warning(f"Adapter test failed: {e}")
                adapter_polygon_count = 0
                direct_polygon_count = 0

            results = {
                "timestamp": datetime.now().isoformat(),
                "test_location": "Munich, Germany",
                "request_params": {
                    "starting_point": [
                        request.starting_points[0].lat,
                        request.starting_points[0].lon,
                    ],
                    "transit_modes": [mode.value for mode in request.transit_modes],
                    "cutoffs": request.cutoffs,
                },
                "motis_request": motis_request_data,
                "raw_response_stats": {
                    "total_locations": len(reachable_locations),
                    "travel_time_range": [min(travel_times), max(travel_times)]
                    if travel_times
                    else [0, 0],
                    "average_travel_time": sum(travel_times) / len(travel_times)
                    if travel_times
                    else 0,
                },
                "validation_results": {
                    "raw_response_issues": raw_issues,
                    "parsed_response_issues": parsed_issues,
                    "total_issues": len(raw_issues) + len(parsed_issues),
                },
                "parsed_response_stats": {
                    "polygon_count": len(parsed_response.polygons)
                    if parsed_response
                    else 0,
                    "travel_times": [p.travel_time for p in parsed_response.polygons]
                    if parsed_response
                    else [],
                },
                "adapter_comparison": {
                    "adapter_polygons": adapter_polygon_count,
                    "direct_polygons": direct_polygon_count,
                    "consistent": adapter_polygon_count == direct_polygon_count,
                },
            }

            return results

        except Exception as e:
            logger.exception("Plausibility test failed")
            return {"error": str(e)}

        finally:
            if hasattr(adapter, "motis_client") and hasattr(
                adapter.motis_client, "close"
            ):
                await adapter.motis_client.close()


# ============================================================================
# BASIC FUNCTIONALITY TESTS
# ============================================================================


@pytest.mark.network
async def test_basic_one_to_all_success(motis_adapter_online, simple_berlin_request):
    """Test basic one-to-all functionality returns valid catchment areas."""
    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(
            simple_berlin_request
        )

    assert response is not None
    assert len(response.polygons) == len(simple_berlin_request.cutoffs)
    assert response.metadata.get("total_locations", 0) > 0
    assert response.metadata.get("source") == "motis_one_to_all"

    for polygon in response.polygons:
        assert polygon.travel_time in simple_berlin_request.cutoffs
        assert hasattr(polygon, "points")
        assert isinstance(polygon.points, list)

        if polygon.geometry is not None:
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry
        elif polygon.points:
            polygon.set_geometry_from_points()
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry


async def test_multiple_cutoffs(motis_adapter_online, munich_request):
    """Test that multiple travel time cutoffs generate correct polygons."""
    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(
            munich_request
        )

    assert len(response.polygons) == len(munich_request.cutoffs)
    travel_times = [p.travel_time for p in response.polygons]
    assert sorted(travel_times) == sorted(munich_request.cutoffs)


@pytest.mark.network
async def test_different_transit_modes(motis_adapter_online):
    """Test different combinations of transit modes."""
    rail_only_request = TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=52.5200, lon=13.4050)],
        transit_modes=[CatchmentAreaRoutingModePT.rail],
        cutoffs=[20],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(
            rail_only_request
        )

    assert len(response.polygons) == 1


async def test_single_cutoff(motis_adapter_online):
    """Test with a single travel time cutoff."""
    single_cutoff_request = TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=48.1351, lon=11.5820)],
        transit_modes=[
            CatchmentAreaRoutingModePT.subway,
            CatchmentAreaRoutingModePT.tram,
        ],
        cutoffs=[20],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(
            single_cutoff_request
        )

    assert len(response.polygons) == 1
    assert response.polygons[0].travel_time == 20


async def test_geometry_structure(motis_adapter_online, simple_berlin_request):
    """Test that returned geometry has correct GeoJSON structure."""
    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(
            simple_berlin_request
        )

    for polygon in response.polygons:
        assert hasattr(polygon, "points")
        assert isinstance(polygon.points, list)

        if polygon.geometry is None:
            polygon.set_geometry_from_points()

        if polygon.points and polygon.geometry:
            assert polygon.geometry["type"] == "Polygon"
            assert "coordinates" in polygon.geometry
            if polygon.geometry["coordinates"]:
                coord_ring = polygon.geometry["coordinates"][0]
                assert len(coord_ring) >= 4
                assert len(coord_ring[0]) == 2
                assert coord_ring[0] == coord_ring[-1]


async def test_bike_access_egress(motis_adapter_online):
    """Test catchment area with bicycle access and egress modes."""
    bike_request = TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=52.5200, lon=13.4050)],
        transit_modes=[CatchmentAreaRoutingModePT.bus, CatchmentAreaRoutingModePT.tram],
        cutoffs=[25],
        access_settings=AccessEgressSettings(
            mode=AccessEgressMode.bicycle, max_time=15, speed=15.0
        ),
        egress_settings=AccessEgressSettings(
            mode=AccessEgressMode.bicycle, max_time=15, speed=15.0
        ),
    )

    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(bike_request)

    assert len(response.polygons) == 1
    assert response.polygons[0].travel_time == 25
    assert bike_request.access_settings.mode == AccessEgressMode.bicycle
    assert bike_request.egress_settings.mode == AccessEgressMode.bicycle


async def test_invalid_coordinates_handling(motis_adapter_online):
    """Test handling of coordinates in remote areas with no transit coverage."""
    remote_request = TransitCatchmentAreaRequest(
        starting_points=[Coordinates(lat=0.0, lon=-160.0)],
        transit_modes=[CatchmentAreaRoutingModePT.bus],
        cutoffs=[15],
        access_settings=AccessEgressSettings.create_walk_settings(),
        egress_settings=AccessEgressSettings.create_walk_settings(),
    )

    async with motis_adapter_online.motis_client:
        response = await motis_adapter_online._get_transit_catchment_area(
            remote_request
        )

    assert response is not None
    assert len(response.polygons) <= len(remote_request.cutoffs)
    assert response.metadata.get("total_locations", 0) == 0


@pytest.mark.network
async def test_motis_one_to_all_integration_minimal(simple_berlin_request):
    """Minimal integration test that can run independently."""
    adapter = create_motis_adapter()

    try:
        async with adapter.motis_client:
            response = await adapter._get_transit_catchment_area(simple_berlin_request)
        assert len(response.polygons) == len(simple_berlin_request.cutoffs)
        assert response.metadata.get("source") == "motis_one_to_all"
    finally:
        await adapter.motis_client.close()


# ============================================================================
# PLAUSIBILITY AND VALIDATION TESTS
# ============================================================================


@pytest.mark.network
@pytest.mark.asyncio
async def test_motis_one_to_all_raw_response_validation(plausibility_tester):
    """Test that MOTIS one-to-all returns a valid response structure."""
    adapter = create_motis_adapter()

    try:
        request = TransitCatchmentAreaRequest(
            starting_points=[Coordinates(lat=48.1351, lon=11.5820)],
            transit_modes=[CatchmentAreaRoutingModePT.bus],
            cutoffs=[10, 20],
            access_settings=AccessEgressSettings.create_walk_settings(),
            egress_settings=AccessEgressSettings.create_walk_settings(),
        )

        motis_request = translate_to_motis_one_to_all_request(request)
        try:
            motis_response = await adapter.motis_client.one_to_all(motis_request)
        except Exception as e:
            pytest.skip(f"MOTIS one-to-all service unavailable: {e}")

        issues = plausibility_tester.validate_raw_motis_response(motis_response)

        if issues:
            logger.warning(f"Validation issues found: {issues}")

        assert isinstance(motis_response, dict), "Response should be a dictionary"
        assert "all" in motis_response, "Response should contain 'all' field"
        assert isinstance(motis_response["all"], list), "'all' field should be a list"
        assert len(motis_response["all"]) > 0, "Should have reachable locations"

    finally:
        await adapter.motis_client.close()


@pytest.mark.network
@pytest.mark.asyncio
async def test_motis_response_parsing(plausibility_tester):
    """Test that MOTIS response can be parsed into our internal format."""
    adapter = create_motis_adapter()

    try:
        request = TransitCatchmentAreaRequest(
            starting_points=[Coordinates(lat=48.1351, lon=11.5820)],
            transit_modes=[
                CatchmentAreaRoutingModePT.bus,
                CatchmentAreaRoutingModePT.subway,
            ],
            cutoffs=[10, 20, 30],
            access_settings=AccessEgressSettings.create_walk_settings(),
            egress_settings=AccessEgressSettings.create_walk_settings(),
        )

        motis_request = translate_to_motis_one_to_all_request(request)
        try:
            motis_response = await adapter.motis_client.one_to_all(motis_request)
        except Exception as e:
            pytest.skip(f"MOTIS one-to-all service unavailable: {e}")

        parsed_response = parse_motis_one_to_all_response(motis_response, request)

        assert parsed_response is not None, "Should successfully parse response"
        assert hasattr(parsed_response, "polygons"), "Should have polygons attribute"
        assert len(parsed_response.polygons) > 0, "Should generate at least one polygon"

        max_cutoff = max(request.cutoffs)
        for polygon in parsed_response.polygons:
            assert hasattr(polygon, "travel_time"), "Polygon should have travel_time"
            assert polygon.travel_time > 0, "Travel time should be positive"
            assert (
                polygon.travel_time <= max_cutoff
            ), "Travel time should not exceed maximum"

    finally:
        await adapter.motis_client.close()


@pytest.mark.network
@pytest.mark.asyncio
async def test_adapter_consistency(plausibility_tester):
    """Test that adapter and direct parsing produce consistent results."""
    adapter = create_motis_adapter()

    try:
        request = TransitCatchmentAreaRequest(
            starting_points=[Coordinates(lat=48.1351, lon=11.5820)],
            transit_modes=[
                CatchmentAreaRoutingModePT.bus,
                CatchmentAreaRoutingModePT.subway,
            ],
            cutoffs=[10, 20],
            access_settings=AccessEgressSettings.create_walk_settings(),
            egress_settings=AccessEgressSettings.create_walk_settings(),
        )

        try:
            adapter_response = await adapter._get_transit_catchment_area(request)
        except Exception as e:
            pytest.skip(f"MOTIS adapter service unavailable: {e}")

        motis_request = translate_to_motis_one_to_all_request(request)
        try:
            motis_response = await adapter.motis_client.one_to_all(motis_request)
        except Exception as e:
            pytest.skip(f"MOTIS one-to-all service unavailable: {e}")

        direct_response = parse_motis_one_to_all_response(motis_response, request)

        assert adapter_response is not None, "Adapter should return a response"
        assert direct_response is not None, "Direct parsing should return a response"

        adapter_polygon_count = len(adapter_response.polygons)
        direct_polygon_count = len(direct_response.polygons)

        assert adapter_polygon_count == direct_polygon_count, (
            f"Adapter and direct parsing should produce same number of polygons: "
            f"{adapter_polygon_count} vs {direct_polygon_count}"
        )

    finally:
        await adapter.motis_client.close()


@pytest.mark.network
@pytest.mark.asyncio
async def test_comprehensive_plausibility(plausibility_tester):
    """Run comprehensive plausibility test and verify results."""
    try:
        results = await plausibility_tester.run_comprehensive_test()
    except Exception as e:
        pytest.skip(f"MOTIS plausibility test service unavailable: {e}")

    assert (
        "error" not in results
    ), f"Test should not error: {results.get('error', 'N/A')}"
    assert "validation_results" in results
    assert "raw_response_stats" in results
    assert "parsed_response_stats" in results
    assert (
        results["raw_response_stats"]["total_locations"] > 0
    ), "Should find reachable locations"
    assert (
        results["parsed_response_stats"]["polygon_count"] > 0
    ), "Should generate polygons"

    logger.info(
        f"Plausibility test results: {json.dumps(results, indent=2, default=str)}"
    )


def test_location_entry_validation(plausibility_tester):
    """Test validation of individual location entries."""
    valid_location = {
        "place": {"lat": 48.1351, "lon": 11.5820, "name": "Test Station"},
        "duration": 15,
    }

    issues = plausibility_tester._validate_location_entry(valid_location, 0)
    assert len(issues) == 0, f"Valid location should have no issues: {issues}"

    invalid_location = {
        "place": {"lat": "invalid", "lng": 200},
        "duration": 150,
    }

    issues = plausibility_tester._validate_location_entry(invalid_location, 0)
    assert len(issues) > 0, "Invalid location should have issues"


def test_place_data_validation(plausibility_tester):
    """Test validation of place data within location entries."""
    valid_place = {"lat": 48.1351, "lon": 11.5820, "name": "Test Location"}
    issues = plausibility_tester._validate_place_data(valid_place, 0)
    assert len(issues) == 0, f"Valid place should have no issues: {issues}"

    place_with_lng = {"lat": 48.1351, "lng": 11.5820, "name": "Test Location"}
    issues = plausibility_tester._validate_place_data(place_with_lng, 0)
    assert len(issues) == 0, f"Place with lng field should be valid: {issues}"

    invalid_place = {"lat": 200, "lon": -200}
    issues = plausibility_tester._validate_place_data(invalid_place, 0)
    assert len(issues) > 0, "Invalid place should have issues"
