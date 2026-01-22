"""
Basic example: Load GTFS data and perform routing.
"""
import pynigiri as ng
from datetime import date, datetime
from consts import GTFS_PATH, STATION_ID_A, STATION_ID_B

def main():
    print("Loading timetable...")

    print("GTFS Path:", GTFS_PATH)
    
    # Configure data source
    sources = [
        ng.TimetableSource(
            tag="my_gtfs",
            path=str(GTFS_PATH),
            config=ng.LoaderConfig()
        )
    ]
    
    # Load timetable for date range, date format "YYYY-MM-DD"
    current_year = date.today().year
    timetable = ng.load_timetable(
        sources=sources,
        start_date=f"{current_year}-01-01",
        end_date=f"{current_year}-12-31"
    )
    
    print(f"Loaded timetable: {timetable}")
    print(f"Number of locations: {timetable.n_locations()}")
    print(f"Number of routes: {timetable.n_routes()}")
    print(f"Date range: {current_year}-01-01 to {current_year}-12-31")
    
    # Find locations
    start_loc_id = timetable.find_location(STATION_ID_A)
    dest_loc_id = timetable.find_location(STATION_ID_B)
    
    if start_loc_id is None or dest_loc_id is None:
        print("Error: Could not find one or both locations")
        return
    
    print(f"\nStart: {timetable.get_location_name(start_loc_id)}")
    print(f"Destination: {timetable.get_location_name(dest_loc_id)}")
    
    # Create routing query
    query = ng.Query()
    
    # Convert timestamp to minutes since epoch (minutes)
    # Use a date that matches the timetable date range
    query_time = datetime(current_year, 1, 15, 10, 0, 0)
    query.start_time = int(query_time.timestamp()) // 60
    
    # Set matching modes
    query.start_match_mode = ng.LocationMatchMode.EQUIVALENT
    query.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
    
    # Set start and destination with offsets (0 minutes offset)
    query.start = [ng.Offset(start_loc_id, 0, 0)]
    query.destination = [ng.Offset(dest_loc_id, 0, 0)]
    
    # Set routing parameters
    query.max_transfers = 6
    query.min_connection_count = 1
    query.max_travel_time = 600  # 10 hours in minutes
    
    print("\nQuery details:")
    print(f"  Start time: {query_time}")
    print(f"  Max transfers: {query.max_transfers}")
    print(f"  Max travel time: {query.max_travel_time}")
    
    print("\nExecuting routing query...")
    journeys = ng.route(timetable, query)
    
    print(f"Found {len(journeys)} journey(s)")
    
    # Print results
    for i, journey in enumerate(journeys, 1):
        print(f"\n--- Journey {i} ---")
        
        # Times are integers in minutes since epoch
        print(f"Travel time: {journey.travel_time()} minutes")
        print(f"Transfers: {journey.transfers}")
        print(f"Number of legs: {len(journey)}")
        
        for j, leg in enumerate(journey.legs, 1):
            # Use getattr() to access 'from' which is a Python keyword
            from_loc = getattr(leg, 'from')
            to_loc = leg.to
            from_name = timetable.get_location_name(from_loc)
            to_name = timetable.get_location_name(to_loc)
            
            # Convert minutes to datetime for display
            dep_time = datetime.fromtimestamp(leg.dep_time * 60)
            arr_time = datetime.fromtimestamp(leg.arr_time * 60)
            
            print(f"  Leg {j}: {from_name} -> {to_name}")
            print(f"         {dep_time.strftime('%H:%M')} -> {arr_time.strftime('%H:%M')}")

if __name__ == "__main__":
    main()
