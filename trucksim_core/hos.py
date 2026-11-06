"""Hours of service, 49 CFR 395.3, for a property-carrying driver whose carrier runs every day (70 h / 8 days), with
the adverse driving conditions exception of 395.1(b)(1): when conditions not known at dispatch (in the sim, a road
closed after the trip was dispatched) delay a driver, the shift's driving limit and 14-hour window grow by 2 hours.

DriverClock is the live clock the executor consults each tick. check_log is a separate, from-scratch check of a
finished duty log, used by the audit, so a bug in the clock can't also hide itself.

Duty statuses: "D" driving, "ON" on duty not driving, "OFF" off duty (sleeper berth counts as off duty here; the
sleeper-berth split provisions of 395.1(g) are not modelled)."""

import bisect
from dataclasses import dataclass, field

from .params import ticks


@dataclass(frozen=True)
class Rules:
    off_duty_before_shift: int   # ticks
    duty_window: int
    driving_per_shift: int
    driving_before_break: int
    break_len: int
    cycle: int
    cycle_days_ticks: int
    restart: int
    adverse_extension: int = ticks(2)   # 49 CFR 395.1(b)(1), since 2020-09-29: driving and the window, 2 h each

    @classmethod
    def from_pack(cls, hos: dict) -> "Rules":
        return cls(ticks(hos["off_duty_before_shift_h"]), ticks(hos["duty_window_h"]), ticks(hos["driving_per_shift_h"]),
                   ticks(hos["driving_before_break_h"]), ticks(hos["break_min"] / 60), ticks(hos["cycle_h"]),
                   ticks(24 * hos["cycle_days"]), ticks(hos["restart_h"]))


@dataclass
class DriverClock:
    """The driver starts the week rested: past a 34-hour restart, no duty window open, cycle at zero."""
    rules: Rules
    log: list = field(default_factory=list)          # [[start_tick, end_tick, status], ...], merged
    off_streak: int = 10_000                          # consecutive OFF ticks
    nondrive_streak: int = 10_000                     # consecutive non-driving ticks (OFF or ON)
    shift_drive: int = 0                              # driving ticks since the last 10-hour reset
    since_break: int = 0                              # driving ticks since the last 30-minute interruption
    window_start: int | None = None                   # tick the 14-hour window opened
    restart_tick: int = 0                             # cycle hours count from here
    onduty: list = field(default_factory=list)        # sorted ticks that were ON or D
    adverse: bool = False                             # the adverse driving conditions exception claimed this shift
    adverse_claims: list = field(default_factory=list)   # ticks it was claimed, for the log's checker

    def status_at(self, tick: int) -> str | None:
        for s, e, st in reversed(self.log):
            if s <= tick < e:
                return st
        return None

    def record(self, tick: int, status: str) -> None:
        """The driver's status for [tick, tick + 1). Ticks must be recorded in order, with no gaps."""
        if self.log and self.log[-1][1] != tick:
            raise ValueError(f"duty log gap: last entry ends at {self.log[-1][1]}, recording {tick}")
        if self.log and self.log[-1][2] == status:
            self.log[-1][1] = tick + 1
        else:
            self.log.append([tick, tick + 1, status])
        if status == "OFF":
            self.off_streak += 1
            self.nondrive_streak += 1
            if self.off_streak == self.rules.off_duty_before_shift:
                self.shift_drive, self.window_start, self.adverse = 0, None, False
            if self.off_streak == self.rules.restart:
                self.restart_tick = tick + 1
        else:
            if self.window_start is None:
                self.window_start = tick              # coming on duty after a 10-hour reset opens the window
            self.off_streak = 0
            self.onduty.append(tick)
            if status == "D":
                self.shift_drive += 1
                self.since_break += 1
                self.nondrive_streak = 0
            else:
                self.nondrive_streak += 1
        if status != "D" and self.nondrive_streak >= self.rules.break_len:
            self.since_break = 0

    def fork(self, tick: int) -> "DriverClock":
        """A copy for trying a plan from `tick` on: its counters, the log's last entry (record merges into it) and only
        the on-duty ticks the 8-day cycle can still see. Copying the week's whole history was most of plan_trip's time."""
        lo = bisect.bisect_left(self.onduty, tick + 1 - self.rules.cycle_days_ticks)
        return DriverClock(self.rules, [list(self.log[-1])] if self.log else [], self.off_streak, self.nondrive_streak,
                           self.shift_drive, self.since_break, self.window_start, self.restart_tick, self.onduty[lo:],
                           self.adverse, list(self.adverse_claims))

    def claim_adverse(self, tick: int) -> bool:
        """Claim the adverse driving conditions exception for this shift; False if it already was, or if the driver
        has just had 10 hours off (a new shift, with nothing to extend: the exception is for a trip under way)."""
        if self.adverse or self.off_streak >= self.rules.off_duty_before_shift:
            return False
        self.adverse = True
        self.adverse_claims.append(tick)
        return True

    def cycle_used(self, tick: int) -> int:
        lo = max(self.restart_tick, tick + 1 - self.rules.cycle_days_ticks)   # the 8 days ending with this tick
        return len(self.onduty) - bisect.bisect_left(self.onduty, lo)

    def drive_blocker(self, tick: int) -> str | None:
        """Why the driver may not drive the tick starting at `tick`, or None if they may."""
        r = self.rules
        extra = r.adverse_extension if self.adverse else 0
        if self.since_break + 1 > r.driving_before_break:
            return "break"
        if self.shift_drive + 1 > r.driving_per_shift + extra:
            return "shift"
        if self.window_start is not None and tick + 1 > self.window_start + r.duty_window + extra:
            return "window"
        if self.cycle_used(tick) + 1 > r.cycle:
            return "cycle"
        return None

    def rest_ticks(self, blocker: str) -> int:
        """Off-duty time that clears the blocker, counting off duty already taken."""
        r = self.rules
        need = {"break": r.break_len, "shift": r.off_duty_before_shift, "window": r.off_duty_before_shift,
                "cycle": r.restart}[blocker]
        done = self.nondrive_streak if blocker == "break" else self.off_streak
        return max(1, need - done)

    def summary(self, tick: int) -> dict:
        r = self.rules
        extra = r.adverse_extension if self.adverse else 0
        window_left = None if self.window_start is None else max(0, self.window_start + r.duty_window + extra - tick)
        return {"drive_left_h": (r.driving_per_shift + extra - self.shift_drive) / 4,
                "until_break_h": (r.driving_before_break - self.since_break) / 4,
                "window_left_h": None if window_left is None else window_left / 4,
                "cycle_left_h": (r.cycle - self.cycle_used(tick)) / 4,
                "off_duty_streak_h": min(self.off_streak, 10_000) / 4 if self.off_streak < 10_000 else None,
                "adverse_conditions_exception": self.adverse}


def check_log(log: list, rules: Rules, adverse_claims: list | tuple = ()) -> list[str]:
    """Every 49 CFR 395.3 violation in a duty log, found by looking back from each driving tick over prefix counts:
    a different algorithm from DriverClock's running counters, so the audit doesn't share its bugs. A claim of the
    adverse driving conditions exception (a tick) extends the driving limit and window of the shift it falls in.
    The driver is taken to be rested (past a 34-hour restart) before tick 0."""
    claims = sorted(adverse_claims)
    st = []
    for s, e, status in log:
        if len(st) != s:
            return [f"log is not contiguous at tick {s}"]
        st.extend([status] * (e - s))
    n = len(st)
    drive = [0] * (n + 1)      # drive[t] = driving ticks in [0, t)
    duty = [0] * (n + 1)       # duty[t] = on-duty (ON or D) ticks in [0, t)
    for t, status in enumerate(st):
        drive[t + 1] = drive[t] + (status == "D")
        duty[t + 1] = duty[t] + (status != "OFF")
    next_duty = [n] * (n + 1)  # first on-duty tick at or after t
    for t in range(n - 1, -1, -1):
        next_duty[t] = t if st[t] != "OFF" else next_duty[t + 1]
    problems = []
    reset_end = restart_end = break_end = 0   # where the last 10 h off, 34 h off and 30 min non-driving runs ended
    off_from = nondrive_from = -10_000        # start of the current OFF run / non-driving run
    for t, status in enumerate(st):
        if status != "OFF" and (t == 0 or st[t - 1] == "OFF"):        # an OFF run ends here
            if t - off_from >= rules.off_duty_before_shift:
                reset_end = t
            if t - off_from >= rules.restart:
                restart_end = t
        if status == "D" and (t == 0 or st[t - 1] != "D"):            # a non-driving run ends here
            if t - nondrive_from >= rules.break_len:
                break_end = t
        if status == "D":
            window_start = next_duty[reset_end]
            i = bisect.bisect_left(claims, reset_end)          # a claim since the last 10 h off, up to this tick
            extra = rules.adverse_extension if i < len(claims) and claims[i] <= t else 0
            if t + 1 > window_start + rules.duty_window + extra:
                problems.append(f"tick {t}: driving after the 14th hour")
            if drive[t + 1] - drive[reset_end] > rules.driving_per_shift + extra:
                problems.append(f"tick {t}: driving past 11 h in the shift")
            if drive[t + 1] - drive[break_end] > rules.driving_before_break:
                problems.append(f"tick {t}: driving after 8 h without a 30-minute break")
            lo = max(restart_end, t + 1 - rules.cycle_days_ticks)
            if duty[t + 1] - duty[lo] > rules.cycle:
                problems.append(f"tick {t}: driving past 70 h in 8 days")
        if status == "OFF":
            if t == 0 or st[t - 1] != "OFF":
                off_from = t if t > 0 else -10_000
        if status != "D":
            if t == 0 or st[t - 1] == "D":
                nondrive_from = t if t > 0 else -10_000
    return problems
