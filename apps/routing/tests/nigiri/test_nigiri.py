"""
Unit tests for pynigiri.
"""

import pynigiri as ng
from datetime import timedelta, datetime


def test_location_idx():
    """Test LocationIdx creation and operations."""
    idx1 = ng.LocationIdx(42)
    idx2 = ng.LocationIdx(42)
    idx3 = ng.LocationIdx(100)
    
    assert int(idx1) == 42
    assert idx1 == idx2
    assert idx1 != idx3
    assert idx1 < idx3
    assert "LocationIdx(42)" in repr(idx1)


def test_location_id():
    """Test LocationId creation."""
    src = ng.SourceIdx(0)
    loc_id = ng.LocationId("STATION_123", src)
    
    assert loc_id.src == src    

def test_footpath():
    """Test Footpath creation."""
    target = ng.LocationIdx(10)
    duration = timedelta(minutes=5)
    fp = ng.Footpath(target, duration)
    
    # target() and duration() are methods
    assert fp.target() == target
    assert fp.duration() == duration


def test_time_interval():
    """Test TimeInterval creation."""
    t1 = datetime.fromtimestamp(1000)
    t2 = datetime.fromtimestamp(2000)
    interval = ng.TimeInterval(t1, t2)
    
    # TimeInterval stores times internally in minute precision
    # So the values may be rounded
    assert interval.from_ <= t1
    assert interval.to_ <= t2


def test_enums():
    """Test enum values."""
    # Test Clasz enum
    assert ng.Clasz.BUS is not None
    assert ng.Clasz.TRAM is not None
    assert ng.Clasz.SUBWAY is not None
    
    # Test EventType enum
    assert ng.EventType.DEP is not None
    assert ng.EventType.ARR is not None
    
    # Test Direction enum
    assert ng.Direction.FORWARD is not None
    assert ng.Direction.BACKWARD is not None


def test_latlng():
    """Test LatLng creation."""
    coords = ng.LatLng(52.5200, 13.4050)  # Berlin
    
    assert coords.lat == 52.5200
    assert coords.lng == 13.4050
    assert "52.52" in repr(coords)
    
    coords2 = ng.LatLng(52.5200, 13.4050)
    assert coords == coords2


def test_offset():
    """Test Offset creation."""
    target = ng.LocationIdx(10)
    duration = 5  # Minutes as int
    mode = 0  # TransportModeId is just an int
    
    offset = ng.Offset(target, duration, mode)
    
    assert offset.target() == target
    assert offset.duration() == duration
    assert offset.type() == mode


def test_td_offset():
    """Test TdOffset creation."""
    offset = ng.TdOffset()
    offset.duration = timedelta(minutes=10)
    
    assert offset.duration.total_seconds() == 600


def test_via_stop():
    """Test ViaStop creation."""
    via = ng.ViaStop()
    via.location = ng.LocationIdx(5)
    via.stay = 10  # 10 minutes
    
    assert via.location == ng.LocationIdx(5)
    assert via.stay == 10


def test_location_match_mode():
    """Test LocationMatchMode enum."""
    assert ng.LocationMatchMode.EXACT is not None
    assert ng.LocationMatchMode.EQUIVALENT is not None
    assert ng.LocationMatchMode.ONLY_CHILDREN is not None


def test_transfer_time_settings():
    """Test TransferTimeSettings creation."""
    settings = ng.TransferTimeSettings()
    assert settings is not None
    
    # default is a boolean, min_transfer_time is a timedelta
    settings.default = False
    settings.min_transfer_time = timedelta(minutes=2)
    assert settings.default == False
    assert settings.min_transfer_time.total_seconds() == 120
    assert "TransferTimeSettings" in repr(settings)


def test_query_comprehensive():
    """Test Query creation, configuration, offsets, and via stops."""
    query = ng.Query()
    assert query is not None

    # Set basic properties
    query.max_transfers = 3
    query.max_travel_time = 120  # 2 hours in minutes
    query.require_bike_transport = True

    assert query.max_transfers == 3
    assert query.max_travel_time == 120
    assert query.require_bike_transport == True

    # Test with start and destination offsets
    start = ng.Offset(ng.LocationIdx(1), 0, 0)  # 0 minutes
    dest = ng.Offset(ng.LocationIdx(2), 0, 0)
    query.start = [start]
    query.destination = [dest]
    assert len(query.start) == 1
    assert len(query.destination) == 1

    # Test with via stops
    via = ng.ViaStop()
    via.location = ng.LocationIdx(5)
    via.stay = 5  # 5 minutes
    query.via_stops = [via]
    assert len(query.via_stops) == 1
    assert query.via_stops[0].location == ng.LocationIdx(5)


def test_journey_creation():
    """Test Journey creation."""
    journey = ng.Journey()
    assert journey is not None
    assert len(journey.legs) == 0


def test_statistics_creation():
    """Test Statistics creation."""
    stats = ng.Statistics()
    assert stats is not None
    
    stats.total_entities = 100
    stats.total_entities_success = 95
    stats.total_entities_fail = 5
    
    assert stats.total_entities == 100
    assert stats.total_entities_success == 95
    assert stats.total_entities_fail == 5


def test_rt_timetable_creation():
    """Test RtTimetable creation."""
    rt_tt = ng.RtTimetable()
    assert rt_tt is not None

def test_loader_config():
    """Test LoaderConfig creation."""
    config = ng.LoaderConfig()
    assert config is not None
    
    # Test with custom settings
    config.link_stop_distance = 100
    assert config.link_stop_distance == 100


def test_finalize_options():
    """Test FinalizeOptions creation."""
    options = ng.FinalizeOptions()
    assert options is not None

if __name__ == "__main__":
    test_location_idx()
    test_location_id()
    test_footpath()
    test_time_interval()
    test_enums()
    test_latlng()
    test_offset()
    test_td_offset()
    test_via_stop()
    test_location_match_mode()
    test_transfer_time_settings()
    test_query_comprehensive()
    test_journey_creation()
    test_statistics_creation()
    test_rt_timetable_creation()
    test_loader_config()
    test_finalize_options()
    print("All tests passed.")
