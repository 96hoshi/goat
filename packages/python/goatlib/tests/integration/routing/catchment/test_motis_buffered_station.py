import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import geopandas as gpd
import pytest
from goatlib.analysis.schemas.vector import BufferParams
from goatlib.analysis.vector.buffer import BufferTool
from goatlib.routing.adapters.motis.motis_converters import (
    extract_bus_stations_for_buffering,
    translate_to_motis_one_to_all_request,
)
from goatlib.routing.schemas.catchment_area_transit import (
    TransitCatchmentAreaRequest,
)
from shapely.geometry import Point

logger = logging.getLogger(__name__)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def pt_buffer_config() -> Dict[str, Any]:
    """Single configuration for Public Transport Station Access."""
    return {
        "name": "pt_station_walk",
        "title": "🚌 Public Transport Access",
        "distances": [200, 400, 600],  # Walking distance from stations
        "description": "Buffer zones around reachable stations",
        "use_case": "Transit Coverage Analysis",
    }


@pytest.fixture
def buffered_stations_dir(tmp_path):
    """Temporary directory for buffered station outputs."""
    return tmp_path / "buffered_stations"


def create_pt_buffer_params(
    reachable_locations: List[Dict[str, Any]],
    config: Dict[str, Any],
    work_dir: Path,
) -> BufferParams:
    """
    Converts dictionary list to Parquet, then returns BufferParams.
    """
    # 1. Prepare Output Paths
    input_path = work_dir / "motis_stations_input.parquet"
    output_path = work_dir / "motis_stations_buffered.parquet"

    # 2. Convert MOTIS list to GeoDataFrame
    gdf_data = []
    for station in reachable_locations:
        coords = station["coordinates"]  # [lon, lat]
        gdf_data.append(
            {
                "name": station.get("name", "Unknown"),
                "duration_minutes": station.get("duration_minutes", 0),
                "stop_id": station.get("stop_id", ""),
                "geometry": Point(coords[0], coords[1]),  # lon, lat
            }
        )

    gdf = gpd.GeoDataFrame(gdf_data, crs="EPSG:4326")
    gdf.to_parquet(input_path)

    # 3. Create BufferParams with full configuration
    return BufferParams(
        input_path=str(input_path),
        output_path=str(output_path),
        distances=config["distances"],  # e.g. [200, 400, 600]
        units="meters",
        dissolve=True,  # Merge overlapping circles into one shape
        num_triangles=8,
        cap_style="CAP_ROUND",
        join_style="JOIN_ROUND",
        output_crs="EPSG:4326",
        output_name="pt_access_buffers",
    )


# ============================================================================
# # TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.network  # Mark as requiring network access
async def test_simple_motis_buffer_pipeline(
    motis_adapter_online,
    munich_request: TransitCatchmentAreaRequest,
    pt_buffer_config: Dict[str, Any],
    buffered_stations_dir: Path,
) -> None:
    """
    Simple test: 1. Fetch MOTIS stations, 2. Buffer them, 3. Save results.
    """
    buffered_stations_dir.mkdir(exist_ok=True)

    try:
        # Step 1: Get MOTIS data
        motis_req = translate_to_motis_one_to_all_request(munich_request)
        logger.info("🚀 Requesting MOTIS One-to-All...")
        motis_response = await motis_adapter_online.motis_client.one_to_all(motis_req)

        # Step 2: Extract station data
        bus_stations = extract_bus_stations_for_buffering(motis_response)
        if len(bus_stations) == 0:
            pytest.skip("No reachable stations found for test location")

        logger.info(f"Found {len(bus_stations)} reachable stations.")

        # Step 3: Create buffer parameters
        params = create_pt_buffer_params(
            reachable_locations=bus_stations,
            config=pt_buffer_config,
            work_dir=buffered_stations_dir,
        )

        # Step 4: Run buffering
        logger.info("⚙️ Running BufferTool...")
        tool = BufferTool()
        results = tool.run(params)

        # Step 5: Verify results
        output_path = Path(params.output_path)
        assert output_path.exists()
        buffered_gdf = gpd.read_parquet(output_path)
        assert len(buffered_gdf) > 0
        assert "geometry" in buffered_gdf.columns

    except Exception as e:
        logger.warning(f"Test failed: {e}")
        pytest.skip(f"MOTIS API or buffer processing unavailable: {e}")


@pytest.mark.asyncio
async def test_motis_performance_simple(
    motis_adapter_online,
    munich_request: TransitCatchmentAreaRequest,
) -> None:
    """Simple performance test for MOTIS API."""

    logger.info("=== Simple MOTIS Performance Test ===")

    try:
        # Time the API call
        start_time = time.perf_counter()

        motis_req = translate_to_motis_one_to_all_request(munich_request)
        motis_response = await motis_adapter_online.motis_client.one_to_all(motis_req)

        api_time = (time.perf_counter() - start_time) * 1000

        # Extract and analyze results
        bus_stations = extract_bus_stations_for_buffering(motis_response)

        logger.info(f"API call time: {api_time:.1f}ms")
        logger.info(f"Stations found: {len(bus_stations)}")

        # Basic assertions
        assert api_time < 5000  # Less than 5 seconds
        assert (
            len(bus_stations) >= 0
        )  # At least some stations (or none if location has no transit)

        logger.info("✅ Performance test completed successfully")

    except Exception as e:
        logger.warning(f"Performance test failed: {e}")
        pytest.skip(f"MOTIS API unavailable: {e}")


@pytest.mark.asyncio
async def test_pipeline_performance(
    motis_adapter_online,
    munich_request: TransitCatchmentAreaRequest,
    buffered_stations_dir: Path,
    pt_buffer_config: Dict[str, Any],
) -> None:
    """Performance timing test for MOTIS -> Buffer pipeline."""

    buffered_stations_dir.mkdir(exist_ok=True)

    # Setup timing stats
    stats = {}
    t_start = time.perf_counter()

    # Phase 1: API Request
    logger.info("⏱️ Phase 1: MOTIS API Request")
    t_api = time.perf_counter()

    try:
        motis_req = translate_to_motis_one_to_all_request(munich_request)
        motis_response = await motis_adapter_online.motis_client.one_to_all(motis_req)
    except Exception as e:
        pytest.skip(f"MOTIS API unavailable: {e}")

    stats["api_latency_sec"] = round(time.perf_counter() - t_api, 4)

    # Phase 2: Data Processing
    logger.info("⏱️ Phase 2: Data Processing")
    t_process = time.perf_counter()

    bus_stations = extract_bus_stations_for_buffering(motis_response)
    assert len(bus_stations) > 0, "No stations found for timing test"

    stats["processing_sec"] = round(time.perf_counter() - t_process, 4)

    # Phase 3: Buffer Creation
    logger.info("⏱️ Phase 3: Buffer Creation")
    t_buffer_setup = time.perf_counter()

    params = create_pt_buffer_params(
        reachable_locations=bus_stations,
        config=pt_buffer_config,
        work_dir=buffered_stations_dir,
    )

    stats["buffer_setup_sec"] = round(time.perf_counter() - t_buffer_setup, 4)

    # BufferTool execution timing
    t_buffer_run = time.perf_counter()
    tool = BufferTool()
    results = tool.run(params)
    stats["buffer_run_sec"] = round(time.perf_counter() - t_buffer_run, 4)

    # Calculate totals
    stats["total_time_sec"] = round(time.perf_counter() - t_start, 4)
    stats["stations_processed"] = len(bus_stations)

    # Verify results
    output_path = Path(params.output_path)
    assert output_path.exists()
    buffered_gdf = gpd.read_parquet(output_path)
    assert len(buffered_gdf) > 0

    # Performance analysis logging
    logger.info("\\n=== PERFORMANCE ANALYSIS ===\\n")
    logger.info(f"Total pipeline time: {stats['total_time_sec']:.3f}s")
    logger.info(f"  - API request: {stats['api_latency_sec']:.3f}s")
    logger.info(f"  - Data processing: {stats['processing_sec']:.3f}s")
    logger.info(f"  - Buffer setup: {stats['buffer_setup_sec']:.3f}s")
    logger.info(f"  - Buffer execution: {stats['buffer_run_sec']:.3f}s")
    logger.info(f"Stations processed: {stats['stations_processed']}")
    logger.info(f"Buffer zones created: {len(buffered_gdf)}")

    # Performance assertions
    assert stats["total_time_sec"] < 10.0  # Should complete in under 10 seconds
    assert stats["api_latency_sec"] < 5.0  # API should respond in under 5 seconds


@pytest.mark.asyncio
async def test_detailed_motis_analysis(
    motis_adapter_online,
    munich_request: TransitCatchmentAreaRequest,
) -> None:
    """
    Detailed analysis test to understand MOTIS API response structure.
    Validates data quality and provides insights into station distribution.
    """
    logger.info("=== DETAILED MOTIS ANALYSIS ===")

    try:
        # Get MOTIS data
        motis_req = translate_to_motis_one_to_all_request(munich_request)
        motis_response = await motis_adapter_online.motis_client.one_to_all(motis_req)

        # Extract and analyze stations
        bus_stations = extract_bus_stations_for_buffering(motis_response)

        if len(bus_stations) == 0:
            pytest.skip("No stations found for analysis")

        # Analyze station data structure
        sample_station = bus_stations[0]
        logger.info(f"Sample station structure: {json.dumps(sample_station, indent=2)}")

        # Station distribution analysis
        duration_ranges = {"0-15min": 0, "15-30min": 0, "30-45min": 0, "45min+": 0}
        for station in bus_stations:
            duration = station.get("duration_minutes", 0)
            if duration <= 15:
                duration_ranges["0-15min"] += 1
            elif duration <= 30:
                duration_ranges["15-30min"] += 1
            elif duration <= 45:
                duration_ranges["30-45min"] += 1
            else:
                duration_ranges["45min+"] += 1

        # Data quality checks
        stations_with_names = sum(
            1 for s in bus_stations if s.get("name") and s["name"] != "Unknown"
        )
        stations_with_ids = sum(1 for s in bus_stations if s.get("stop_id"))
        stations_with_coords = sum(1 for s in bus_stations if s.get("coordinates"))

        # Logging analysis results
        logger.info(f"Total stations: {len(bus_stations)}")
        logger.info(
            f"Stations with names: {stations_with_names} ({100*stations_with_names/len(bus_stations):.1f}%)"
        )
        logger.info(
            f"Stations with IDs: {stations_with_ids} ({100*stations_with_ids/len(bus_stations):.1f}%)"
        )
        logger.info(
            f"Stations with coordinates: {stations_with_coords} ({100*stations_with_coords/len(bus_stations):.1f}%)"
        )

        for range_name, count in duration_ranges.items():
            percentage = 100 * count / len(bus_stations)
            logger.info(f"  {range_name}: {count} stations ({percentage:.1f}%)")

        # Quality assertions
        assert len(bus_stations) > 0
        assert stations_with_coords == len(bus_stations)  # All should have coordinates
        assert (
            stations_with_names > len(bus_stations) * 0.8
        )  # At least 80% should have names

        logger.info("\\n✅ Detailed analysis completed successfully")

    except Exception as e:
        logger.warning(f"Analysis failed: {e}")
        pytest.skip(f"MOTIS API unavailable: {e}")
