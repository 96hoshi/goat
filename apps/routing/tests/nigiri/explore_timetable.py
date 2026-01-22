"""
Example: Exploring timetable data (standardized format).
"""
import pynigiri as ng
from datetime import date, datetime, timedelta
from consts import GTFS_PATH, STATION_ID_A, INVALID_LOCATION_IDX

def main():
    print("Loading timetable...")
    print("GTFS Path:", GTFS_PATH)
    
    # Configure data source
    sources = [
        ng.TimetableSource(
            tag="my_gtfs",
            path=str(GTFS_PATH)
        )
    ]
    
    # Load timetable for date range
    current_year = date.today().year
    timetable = ng.load_timetable(
        sources=sources,
        start_date=f"{current_year}-01-01",
        end_date=f"{current_year}-12-31"
    )
    
    print(f"Loaded timetable: {timetable}")
    print(f"Number of locations: {timetable.n_locations()}")
    print(f"Number of routes: {timetable.n_routes()}")
    print(f"Number of transports: {timetable.n_transports()}")
    
    # Date range
    start_day, end_day = timetable.date_range()
    epoch = datetime(1970, 1, 1)
    start_date = epoch + timedelta(days=start_day)
    end_date = epoch + timedelta(days=end_day)
    print(f"Date range: {start_day} to {end_day} (i.e., {start_date.date()} to {end_date.date()})\n")
    
    # Explore some locations
    print("=== Sample Locations ===")
    for i in range(min(10, timetable.n_locations())):
        loc_idx = ng.LocationIdx(i)
        name = timetable.get_location_name(loc_idx)
        coords = timetable.get_location_coords(loc_idx)
        loc_type = timetable.get_location_type(loc_idx)
        parent = timetable.get_location_parent(loc_idx)
        parent_display = "None" if int(parent) == INVALID_LOCATION_IDX else str(parent)
        print(f"\nLocation {i}:")
        print(f"  Name: {name}")
        print(f"  Type: {loc_type}")
        print(f"  Coordinates: ({coords.lat:.6f}, {coords.lng:.6f})")
        print(f"  Parent: {parent_display}")
    
    # Find specific location
    print("\n=== Location Lookup ===")
    sample_id = STATION_ID_A
    found = timetable.find_location(sample_id)
    if found is not None:
        print(f"Found location '{sample_id}': {found}")
        print(f"  Name: {timetable.get_location_name(found)}")
        coords = timetable.get_location_coords(found)
        print(f"  Coordinates: ({coords.lat:.6f}, {coords.lng:.6f})")
        print(f"  Type: {timetable.get_location_type(found)}")
        print(f"  Parent: {timetable.get_location_parent(found)}")
    else:
        print(f"Location '{sample_id}' not found")

if __name__ == "__main__":
    main()
