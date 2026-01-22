from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[4]
DATA_DIR = BASE_DIR / "data" 
GTFS_PATH = DATA_DIR / "gtfs" / "toy"


GTFS_RT_URL = "https://example.com/gtfs-rt/tripupdates"  # Update with actual URL
GTFS_RT_PATH = "path/to/gtfs-rt.pb"  # Update with actual path

# Valid station IDs from the toy GTFS data
STATION_ID_A = "station_a"  # Station A
STATION_ID_B = "station_b"  # Station B
STATION_ID_C = "station_c"  # Station C
STATION_ID_D = "station_a"  # Station A (reused for examples)
STATION_ID_E = "station_b"  # Station B (reused for examples)

# Invalid location index constant (UINT32_MAX)
INVALID_LOCATION_IDX = 4294967295