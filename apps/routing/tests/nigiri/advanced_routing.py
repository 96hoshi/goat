"""
Advanced routing example: Using advanced query features with real German stations.
"""
import pynigiri as ng
from datetime import datetime
from consts import GTFS_PATH, STATION_ID_A, STATION_ID_B, STATION_ID_C, STATION_ID_D, STATION_ID_E

def main():
    # Load timetable
    sources = [ng.TimetableSource("my_gtfs", str(GTFS_PATH))]
    timetable = ng.load_timetable(sources, "2026-01-01", "2026-12-31")
    
    print("=== Advanced Routing Examples ===\n")
    
    # Use a date within the GTFS data range
    query_date = datetime(2026, 1, 15, 10, 0, 0)
    query_time = int(query_date.timestamp()) // 60
    
    # Example 1: Routing with via stops
    print("1. Routing with intermediate stops (via):")
    query = ng.Query()
    
    start_loc = timetable.find_location(STATION_ID_A)  # Aachen Hbf
    via_loc = timetable.find_location(STATION_ID_C)    # Aschaffenburg Hbf
    dest_loc = timetable.find_location(STATION_ID_D)   # Augsburg Hbf
    
    if all([start_loc, via_loc, dest_loc]):
        print(f"   Route: {timetable.get_location_name(start_loc)} -> "
              f"{timetable.get_location_name(via_loc)} -> "
              f"{timetable.get_location_name(dest_loc)}")
        
        query.start = [ng.Offset(start_loc, 0, 0)]
        query.destination = [ng.Offset(dest_loc, 0, 0)]
        query.start_match_mode = ng.LocationMatchMode.EQUIVALENT
        query.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
        
        # Add via stop with 10 minute minimum stay
        query.via_stops = [ng.ViaStop()]
        query.via_stops[0].location = via_loc
        query.via_stops[0].stay = 10  # 10 minutes
        
        query.start_time = query_time
        query.max_transfers = 5
        query.min_connection_count = 1
        
        journeys = ng.route(timetable, query)
        print(f"   Found {len(journeys)} journey(s) with via stop\n")
    else:
        print("   Could not find all locations\n")
    
    # Example 2: Time interval search
    print("2. Search within time interval:")
    query2 = ng.Query()
    
    start_loc = timetable.find_location(STATION_ID_A)  # Aachen Hbf
    dest_loc = timetable.find_location(STATION_ID_B)   # Bremen Hbf
    
    if start_loc and dest_loc:
        print(f"   Route: {timetable.get_location_name(start_loc)} -> "
              f"{timetable.get_location_name(dest_loc)}")
        
        # Create time interval: 2-hour window
        start_minutes = query_time
        end_minutes = query_time + 120  # 2 hours later
        
        query2.start_time = (start_minutes, end_minutes)
        query2.start = [ng.Offset(start_loc, 0, 0)]
        query2.destination = [ng.Offset(dest_loc, 0, 0)]
        query2.start_match_mode = ng.LocationMatchMode.EQUIVALENT
        query2.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
        query2.max_transfers = 6
        query2.min_connection_count = 1
        
        journeys = ng.route(timetable, query2)
        print(f"   Found {len(journeys)} journey(s) in time window\n")
    else:
        print("   Could not find all locations\n")
    
    # Example 3: Multiple transfer limit
    print("3. Routing with different transfer limits:")
    query3 = ng.Query()
    
    start_loc = timetable.find_location(STATION_ID_A)  
    dest_loc = timetable.find_location(STATION_ID_E) 
    
    if start_loc and dest_loc:
        print(f"   Route: {timetable.get_location_name(start_loc)} -> "
              f"{timetable.get_location_name(dest_loc)}")
        
        query3.start = [ng.Offset(start_loc, 0, 0)]
        query3.destination = [ng.Offset(dest_loc, 0, 0)]
        query3.start_match_mode = ng.LocationMatchMode.EQUIVALENT
        query3.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
        query3.start_time = query_time
        query3.min_connection_count = 1
        
        # Try with different transfer limits
        for max_transfers in [0, 2, 5]:
            query3.max_transfers = max_transfers
            journeys = ng.route(timetable, query3)
            print(f"   Max {max_transfers} transfers: {len(journeys)} journey(s)")
        print()
    else:
        print("   Could not find all locations\n")
    
    # Example 4: Maximum travel time constraint
    print("4. Routing with maximum travel time:")
    query4 = ng.Query()
    
    start_loc = timetable.find_location(STATION_ID_A)  # Aachen Hbf
    dest_loc = timetable.find_location(STATION_ID_B)   # Bremen Hbf
    
    if start_loc and dest_loc:
        print(f"   Route: {timetable.get_location_name(start_loc)} -> "
              f"{timetable.get_location_name(dest_loc)}")
        
        query4.start = [ng.Offset(start_loc, 0, 0)]
        query4.destination = [ng.Offset(dest_loc, 0, 0)]
        query4.start_match_mode = ng.LocationMatchMode.EQUIVALENT
        query4.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
        query4.start_time = query_time
        query4.max_travel_time = 600  # 10 hours maximum
        query4.max_transfers = 6
        query4.min_connection_count = 1
        
        journeys = ng.route(timetable, query4)
        print(f"   Found {len(journeys)} journey(s) under 5 hours\n")
    else:
        print("   Could not find all locations\n")
    
    # Example 5: Multiple start/destination points
    print("5. Routing with multiple start/destination points:")
    query5 = ng.Query()
    
    start_a = timetable.find_location(STATION_ID_A)
    start_b = timetable.find_location(STATION_ID_C)
    dest_a = timetable.find_location(STATION_ID_D)
    dest_b = timetable.find_location(STATION_ID_E)
    
    if all([start_a, start_b, dest_a, dest_b]):
        print(f"   Starts: {timetable.get_location_name(start_a)} OR "
              f"{timetable.get_location_name(start_b)}")
        print(f"   Destinations: {timetable.get_location_name(dest_a)} OR "
              f"{timetable.get_location_name(dest_b)}")
        
        # Multiple start points (with walking offsets)
        query5.start = [
            ng.Offset(start_a, 0, 0),
            ng.Offset(start_b, 5, 1)  # 5 min walk from alternative start
        ]
        
        # Multiple destination points
        query5.destination = [
            ng.Offset(dest_a, 0, 0),
            ng.Offset(dest_b, 3, 1)  # 3 min walk to alternative destination
        ]
        
        query5.start_time = query_time
        query5.start_match_mode = ng.LocationMatchMode.EQUIVALENT
        query5.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
        query5.max_transfers = 6
        query5.min_connection_count = 1
        
        journeys = ng.route(timetable, query5)
        print(f"   Found {len(journeys)} journey(s) with multiple points\n")
    else:
        print("   Could not find all locations\n")

if __name__ == "__main__":
    main()
