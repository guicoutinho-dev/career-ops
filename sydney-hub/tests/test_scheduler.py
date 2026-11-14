from datetime import datetime, time

from hub.scheduler.slots import latest_due_slot, needs_catchup, next_slot, parse_slots

SLOTS = [time(8, 0), time(14, 0), time(20, 0)]


def ts(dt: datetime) -> float:
    return dt.timestamp()


def test_parse_slots_accepts_hh_mm_and_sorts():
    assert parse_slots(["20:00", "08:00", "14:30"]) == [time(8, 0), time(14, 30), time(20, 0)]


def test_parse_slots_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        parse_slots(["25:00"])


def test_latest_due_slot_same_day():
    assert latest_due_slot(datetime(2026, 12, 1, 15, 0), SLOTS) == datetime(2026, 12, 1, 14, 0)


def test_latest_due_slot_before_first_slot_uses_previous_day():
    assert latest_due_slot(datetime(2026, 12, 1, 6, 0), SLOTS) == datetime(2026, 11, 30, 20, 0)


def test_next_slot_today_and_tomorrow():
    assert next_slot(datetime(2026, 12, 1, 9, 0), SLOTS) == datetime(2026, 12, 1, 14, 0)
    assert next_slot(datetime(2026, 12, 1, 21, 0), SLOTS) == datetime(2026, 12, 2, 8, 0)


def test_catchup_when_a_slot_was_missed():
    now = datetime(2026, 12, 1, 15, 0)
    last = ts(datetime(2026, 12, 1, 7, 55))  # last success before the 08:00 and 14:00 slots
    assert needs_catchup(now, SLOTS, last) is True


def test_no_catchup_when_latest_slot_already_covered():
    now = datetime(2026, 12, 1, 15, 0)
    last = ts(datetime(2026, 12, 1, 14, 2))
    assert needs_catchup(now, SLOTS, last) is False


def test_no_catchup_when_there_has_never_been_a_run():
    assert needs_catchup(datetime(2026, 12, 1, 15, 0), SLOTS, None) is False
