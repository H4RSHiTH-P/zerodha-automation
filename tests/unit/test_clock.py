from datetime import datetime, time, timedelta

from zt.core import clock


def test_ensure_local_tz_is_ist():
    clock.ensure_local_tz()
    assert datetime.now().astimezone().utcoffset() == timedelta(hours=5, minutes=30)


def test_as_ist_handles_naive_and_aware():
    naive = datetime(2026, 9, 18, 12, 33, 5)
    aware = clock.as_ist(naive)
    assert aware.utcoffset() == timedelta(hours=5, minutes=30) and aware.hour == 12
    assert clock.as_ist(aware) == aware


def test_epoch_round_trip():
    dt = clock.at(datetime(2026, 9, 18).date(), time(12, 33))
    assert clock.from_epoch(clock.to_epoch(dt)) == dt


def test_bucket_start_is_anchored_at_0915():
    d = datetime(2026, 9, 18).date()
    assert clock.bucket_start(clock.at(d, time(9, 16, 30)), 3) == clock.at(d, time(9, 15))
    assert clock.bucket_start(clock.at(d, time(9, 18)), 3) == clock.at(d, time(9, 18))
    assert clock.bucket_start(clock.at(d, time(9, 19, 59)), 5) == clock.at(d, time(9, 15))
    assert clock.bucket_start(clock.at(d, time(15, 29, 59)), 5) == clock.at(d, time(15, 25))
    assert clock.bucket_start(clock.at(d, time(15, 29, 59)), 3) == clock.at(d, time(15, 27))


def test_session_times():
    assert clock.session_end(False) == time(15, 30) and clock.session_end(True) == time(15, 15)
    assert clock.square_off(False) == time(15, 25) and clock.square_off(True) == time(15, 12)
