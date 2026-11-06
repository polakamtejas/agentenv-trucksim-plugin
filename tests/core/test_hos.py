"""Hours of service: the live clock never lets a driver break 49 CFR 395.3, and the independent checker agrees."""

from trucksim_core.data import DataPack
from trucksim_core.hos import DriverClock, Rules, check_log

RULES = Rules.from_pack(DataPack().hos)


def drive_whenever_allowed(n_ticks: int, on_duty_every: int = 0) -> DriverClock:
    """A driver who drives every tick the clock allows, rests exactly as long as told, and optionally spends one
    tick on duty (a stop) every `on_duty_every` ticks."""
    clock, rest_until = DriverClock(RULES), 0
    for t in range(n_ticks):
        if t < rest_until:
            clock.record(t, "OFF")
            continue
        if on_duty_every and t % on_duty_every == 0:
            clock.record(t, "ON")
            continue
        blocker = clock.drive_blocker(t)
        if blocker is None:
            clock.record(t, "D")
        else:
            rest_until = t + clock.rest_ticks(blocker)
            clock.record(t, "OFF")
    return clock


def driving_hours(log) -> float:
    return sum(e - s for s, e, st in log if st == "D") / 4


def test_a_maximal_driver_is_legal_for_two_weeks():
    for every in (0, 7, 23):
        clock = drive_whenever_allowed(4 * 24 * 14, on_duty_every=every)
        assert check_log(clock.log, RULES) == [], every


def test_first_shift_is_8h_break_3h_then_reset():
    clock = drive_whenever_allowed(4 * 24)
    d = [(s / 4, e / 4, st) for s, e, st in clock.log]
    assert d[0] == (0.0, 8.0, "D")          # 8 h, then the 30-minute break is due
    assert d[1] == (8.0, 8.5, "OFF")
    assert d[2] == (8.5, 11.5, "D")         # 3 more hours makes 11 h of driving
    assert d[3] == (11.5, 21.5, "OFF")      # a 10-hour reset


def test_seventy_hours_forces_a_34h_restart():
    clock = drive_whenever_allowed(4 * 24 * 8)
    offs = [(e - s) / 4 for s, e, st in clock.log if st == "OFF"]
    assert 34.0 in offs                      # the cycle runs out within 8 days of maximal driving
    assert check_log(clock.log, RULES) == []


def test_checker_catches_each_rule():
    h = lambda x: int(x * 4)  # noqa: E731
    assert any("30-minute" in p for p in check_log([[0, h(8.25), "D"]], RULES))
    assert any("11 h" in p for p in check_log([[0, h(6), "D"], [h(6), h(7), "OFF"], [h(7), h(12.25), "D"]], RULES))
    assert any("14th hour" in p for p in check_log([[0, h(10), "ON"], [h(10), h(14.25), "D"]], RULES))
    ok = [[0, h(8), "D"], [h(8), h(8.5), "ON"], [h(8.5), h(11.5), "D"]]   # on duty counts as the break
    assert check_log(ok, RULES) == []
