# PyNigiri Quick Reference

Fast reference for common PyNigiri operations.

## Installation

```bash
pip install pynigiri
```

## Basic Usage

### Load Timetable

```python
import pynigiri as ng
from datetime import date

current_year = date.today().year
sources = [ng.TimetableSource("gtfs", "/path/to/gtfs")]
tt = ng.load_timetable(sources, f"{current_year}-01-01", f"{current_year}-12-31")
```

### Find Locations

```python
loc = tt.find_location("STATION_ID")
name = tt.get_location_name(loc)
coords = tt.get_location_coords(loc)
```

### Create Query

```python
from datetime import datetime

query = ng.Query()

# Convert datetime to minutes since epoch
query_time = datetime(2026, 1, 15, 10, 0, 0)
query.start_time = int(query_time.timestamp()) // 60

# All durations and offsets are integers (minutes)
query.start = [ng.Offset(start_loc, 0, 0)]  # 0 minutes offset
query.destination = [ng.Offset(dest_loc, 0, 0)]
query.max_transfers = 6
query.max_travel_time = 600  # 10 hours in minutes
query.start_match_mode = ng.LocationMatchMode.EQUIVALENT
query.dest_match_mode = ng.LocationMatchMode.EQUIVALENT
```

### Route

```python
from datetime import datetime

journeys = ng.route(tt, query)

for journey in journeys:
    print(f"Duration: {journey.travel_time()} min")
    print(f"Transfers: {journey.transfers}")
    
    for leg in journey.legs:
        # Use getattr for 'from' (Python keyword)
        from_loc = getattr(leg, 'from')
        from_name = tt.get_location_name(from_loc)
        to_name = tt.get_location_name(leg.to)
        
        # Convert minute timestamps to datetime for display
        dep_dt = datetime.fromtimestamp(leg.dep_time * 60)
        arr_dt = datetime.fromtimestamp(leg.arr_time * 60)
        
        print(f"  {from_name} → {to_name}")
        print(f"    {dep_dt.strftime('%H:%M')} → {arr_dt.strftime('%H:%M')}")
```

## Advanced Features

### Via Stops

```python
via = ng.ViaStop()
via.location = intermediate_loc
via.stay = 10  # 10 minutes minimum stay
query.via_stops = [via]
```

### Time Interval

```python
from datetime import datetime

# For time intervals, use tuple of integers (minutes)
start_minutes = int(datetime.now().timestamp()) // 60
end_minutes = start_minutes + 120  # 2 hours later
query.start_time = (start_minutes, end_minutes)
```

### Transport Filters

```python
query.require_bike_transport = True
query.allowed_claszes = ng.all_clasz_allowed()
```

## Real-Time Updates

### Create RT Timetable

```python
from datetime import date

rt_tt = ng.create_rt_timetable(tt, date.today())
```

### Apply GTFS-RT

```python
# From file
stats = ng.gtfsrt_update_from_file(
    tt, rt_tt, ng.SourceIdx(0), "updates", "gtfsrt.pb"
)

# From bytes
stats = ng.gtfsrt_update_from_bytes(
    tt, rt_tt, ng.SourceIdx(0), "updates", data
)
```

### Route with RT

```python
journeys = ng.route_with_rt(tt, rt_tt, query)
```

## Common Types

| Type | Purpose | Usage Example |
|------|---------|---------------|
| `LocationIdx(n)` | Location index | `ng.LocationIdx(42)` |
| `int` | Duration (minutes) | `duration = 30` |
| `int` | Timestamp (minutes since epoch) | `time = int(dt.timestamp()) // 60` |
| `LatLng(lat, lng)` | Coordinates | `ng.LatLng(52.52, 13.40)` |
| `Offset(loc, dur, mode)` | Start/dest point | `ng.Offset(loc, 0, 0)` |

## Enums

### Transport Classes (Clasz)

`AIR`, `COACH`, `HIGHSPEED`, `LONG_DISTANCE`, `NIGHT`, `REGIONAL`, `REGIONAL_FAST`, `SUBWAY`, `TRAM`, `BUS`, `SHIP`, `OTHER`

### Location Types

`TRACK`, `STATION`, `GENERATED_TRACK`

### Event Types

`DEP`, `ARR`

### Direction

`FORWARD`, `BACKWARD`

### Location Match Modes

- `EXACT`: Match only the exact platform/stop
- `EQUIVALENT`: Match all equivalent stops (recommended)
- `ONLY_CHILDREN`: Match all child stops
- `ON_TRIP`: Match any stop on the trip

## Timetable Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `find_location(id)` | `Optional[LocationIdx]` | Find location by ID |
| `get_location_name(loc)` | `str` | Get location name |
| `get_location_coords(loc)` | `LatLng` | Get coordinates |
| `get_location_type(loc)` | `LocationType` | Get type |
| `n_locations()` | `int` | Total locations |
| `n_routes()` | `int` | Total routes |
| `date_range()` | `(date, date)` | Date range |

## Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start_time` | `int` | - | Start time (minutes since epoch) |
| `start` | `List[Offset]` | `[]` | Start locations with offsets |
| `destination` | `List[Offset]` | `[]` | Destination locations with offsets |
| `max_transfers` | `int` | `7` | Maximum number of transfers |
| `max_travel_time` | `int` | - | Maximum travel time (minutes) |
| `via_stops` | `List[ViaStop]` | `[]` | Intermediate stops |
| `require_bike_transport` | `bool` | `False` | Require bicycle transport |
| `require_car_transport` | `bool` | `False` | Require car transport |

## Journey Properties

| Property | Type | Description |
|----------|------|-------------|
| `legs` | `List[Leg]` | Journey legs |
| `transfers` | `int` | Number of transfers |
| `travel_time()` | `int` | Total travel time in minutes |
| `departure_time()` | `int` | Departure time (minutes since epoch) |
| `arrival_time()` | `int` | Arrival time (minutes since epoch) |
| `start_time` | `int` | Journey start time (minutes since epoch) |
| `dest_time` | `int` | Journey end time (minutes since epoch) |

## Files

| File | Purpose |
|------|---------|
| `API.md` | API reference |
| `tests_nigiri.py` | Unit tests |
| `nigiri/*.py` | Usage examples |
