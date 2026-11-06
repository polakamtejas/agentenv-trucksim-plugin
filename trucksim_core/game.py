"""The game: seats, trucks and their drivers, the load board, the turn loop, the driver model, the ledger and the
event log.

A turn is 6 virtual hours. While a turn is open the clock is stopped: every seat reads the world and acts through the
tool methods below (situation, list_loads, get_truck, get_market, get_forecast, plan_trip, accept_load, release_load,
dispatch, reposition, schedule_maintenance, handle_breakdown, end_turn). Claims on loads are resolved when the last
seat ends the turn; then the world runs 24 ticks of 15 minutes and the next turn opens.

The operator decides what each truck does next (its tasks); the driver model (Executor) decides how: when to drive,
when to rest under hours of service, how long a stop takes. A tool call that can't be done raises ActionError with
the reason; nothing is silently fixed."""

import copy
import heapq
import math
from dataclasses import dataclass, field
from types import SimpleNamespace

from .data import DataPack
from .hos import DriverClock, Rules
from .params import TICK_HOURS, VEHICLES, Params, hours, ticks
from .rng import stream
from .scenario import Offer, Scenario, TruckSpec, failure_draw

REVENUE = ("linehaul", "fuel_surcharge", "detention_pay", "tonu_fee", "terminal_in_transit")
COSTS = ("fuel", "driver_wages", "temp_drivers", "owner_operator_pay", "owner_operator_units", "per_mile_wear", "fixed", "overhead",
         "lodging", "repairs", "tow", "maintenance", "late_penalty",
         "missed_pickup_penalty", "release_penalty", "terminal_deadhead", "terminal_unrepaired")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ActionError(ValueError):
    """A tool call that can't be done, with the reason in its message."""


def clock(tick: int) -> str:
    day, rem = divmod(tick, 96)
    return f"{DAYS[day % 7] if day < 7 else 'Mon+'} {rem // 4:02d}:{(rem % 4) * 15:02d}"


# --------------------------------------------------------------------------------------------------------------
# state

@dataclass
class Position:
    city: str | None                  # at a city, or None while on a road
    a: str | None = None              # on the road from a to b, `into` miles from a
    b: str | None = None
    into: float = 0.0

    def text(self, pack: DataPack) -> str:
        if self.city:
            return pack.cities[self.city].name
        e = pack.edge(self.a, self.b)
        return f"{e.route}, {self.into:.0f} mi from {pack.cities[self.a].name} toward {pack.cities[self.b].name}"


@dataclass
class Component:
    name: str
    k: int                            # this is the k-th draw of the component's failure stream
    failure_odo: float
    warn_odo: float | None
    draw: dict
    warned: bool = False


@dataclass
class Driver:
    """A driver (DESIGN.md "v1.1", M15): on a unit's crew, or off duty at home. Each keeps their own hours clock."""
    id: str
    name: str
    seat: str
    kind: str                         # "company" (CDL, by the mile), "local" (no CDL, hourly), "owner_operator", "temp"
    home: str
    clock: DriverClock
    truck: str | None = None          # the unit whose crew they're on
    available_from: int = 0           # a temp: arrives at the hub then ...
    available_until: int = 10**9      # ... and leaves once back at the hub after this
    duty_today: int = 0               # a temp's on-duty ticks today, for the agency's daily minimum

    @property
    def cdl(self) -> bool:
        return self.kind != "local"


@dataclass
class Truck:
    spec: TruckSpec
    crew: list                        # its drivers (1, or 2 for a team); empty: parked, nobody to drive it
    pos: Position
    odometer: float
    components: dict
    tasks: list = field(default_factory=list)   # [{"kind": pickup|deliver|goto|maintain, "city": c, "load": id?}]
    route: list = field(default_factory=list)   # cities still to reach on the way to tasks[0]["city"]
    dwell_left: int | None = None               # ticks of work left at the current stop
    stop_started: int | None = None
    onboard: list = field(default_factory=list)  # loads aboard, in pickup order
    onboard_odo: dict = field(default_factory=dict)   # load -> odometer when it was picked up
    broken: dict | None = None
    rest_until: int = 0
    dispatched_at: int = 0                      # when its current tasks were given (the adverse-conditions test)
    held_since: int | None = None               # a closed road has kept it from moving since then
    held_by: str | None = None                  # ... which closure
    at_wheel: int = 0                           # which of the crew drives now (a team swaps when one is out of hours)

    @property
    def clock(self) -> DriverClock:
        """The hours clock of the driver at the wheel."""
        return self.crew[self.at_wheel].clock


def fork_truck(tr: "Truck", tick: int) -> "Truck":
    """A copy of a truck and its crew to try a plan on (the driver model mutates both); see DriverClock.fork."""
    crew = [Driver(d.id, d.name, d.seat, d.kind, d.home, d.clock.fork(tick), d.truck, d.available_from, d.available_until,
                   d.duty_today) for d in tr.crew]
    return Truck(tr.spec, crew, Position(tr.pos.city, tr.pos.a, tr.pos.b, tr.pos.into), tr.odometer,
                 {name: copy.copy(c) for name, c in tr.components.items()}, [dict(t) for t in tr.tasks], list(tr.route),
                 tr.dwell_left, tr.stop_started, list(tr.onboard), dict(tr.onboard_odo),
                 copy.deepcopy(tr.broken), tr.rest_until, tr.dispatched_at, tr.held_since, tr.held_by, tr.at_wheel)


@dataclass
class Load:
    offer: Offer
    status: str = "open"              # open, owned, picked, delivered, cancelled, expired, missed, released
    owner: str | None = None
    truck: str | None = None
    claims: set = field(default_factory=set)
    pending_trucks: dict = field(default_factory=dict)   # seat -> truck, for claims not yet settled
    picked_at: int | None = None
    pickup_dwell: int | None = None


# --------------------------------------------------------------------------------------------------------------
# worlds the driver model runs in: the real one, and the expected one plan_trip uses

class RealWorld:
    breakdowns = True

    def __init__(self, game: "Game"):
        self.g = game

    def offer(self, load_id: str) -> Offer:
        return self.g.loads[load_id].offer

    def dwell(self, load_id: str, stop: str) -> int:
        o = self.offer(load_id)
        return o.dwell_pickup if stop == "pickup" else o.dwell_delivery

    def stretches(self, a: str, b: str) -> list:
        """Storms on the road from a to b: [(from mile, to mile, start tick, end tick, speed share)], miles from a."""
        return self.g.storm_stretches.get((a, b), [])

    def closures(self, a: str, b: str) -> list:
        """Closures on the road from a to b, as they really are: [(from mile, to mile, start, end, Closure)]."""
        return self.g.closed_roads.get((a, b), [])

    def known_closures(self, a: str, b: str, tick: int) -> list:
        """What a driver knows at `tick`: the closures under way on the road, each with its expected reopening (the
        announced one, or an hour from now once that has passed): [(from mile, to mile, expected end, Closure)]."""
        return [(m0, m1, max(c.estimate, tick + 4), c) for m0, m1, start, end, c in self.g.closed_roads.get((a, b), [])
                if start <= tick < end]


class ExpectedWorld(RealWorld):
    """What the operator can expect: no breakdowns, the average dwell, and only the weather already under way
    (lasting to its end). Detention, failures and storms still to come are the risks plan_trip doesn't show."""
    breakdowns = False

    def __init__(self, game: "Game", at: int):
        super().__init__(game)
        p = game.p
        self.dwell_override: int | None = None          # the reference planner's slow-stop check sets it
        self.active = {road: [x for x in xs if x[2] <= at < x[3]] for road, xs in game.storm_stretches.items()}
        # the closures under way at `at`, expected to reopen when announced: what a plan can know of
        self.known_closed = {road: [(m0, m1, start, max(c.estimate, at + 4), c) for m0, m1, start, end, c in xs if start <= at < end]
                             for road, xs in game.closed_roads.items()}

    def dwell(self, load_id: str, stop: str) -> int:
        if self.dwell_override is not None:
            return self.dwell_override
        p = self.g.p
        return math.ceil((p.size_handling_hours[self.offer(load_id).size] + p.detention_probability * p.detention_extra_hours_mean) * 4)

    def stretches(self, a: str, b: str) -> list:
        return self.active.get((a, b), [])

    def closures(self, a: str, b: str) -> list:
        return self.known_closed.get((a, b), [])

    def known_closures(self, a: str, b: str, tick: int) -> list:
        return [(m0, m1, end, c) for m0, m1, start, end, c in self.known_closed.get((a, b), []) if start <= tick < end]


# --------------------------------------------------------------------------------------------------------------
# the driver model

class Executor:
    """Runs one truck for one tick: repair, mandatory rest, then the next task. Returns the driver's duty status,
    the road miles driven [(a, b, miles)], and events [dict]."""

    def __init__(self, pack: DataPack, p: Params, scenario_key: str):
        self.pack, self.p, self.key = pack, p, scenario_key

    def free_mph(self, a: str, b: str, vc) -> float:
        """A vehicle class's speed on a free road: its share of OSRM's car speed, up to its cap."""
        return min(vc.speed_cap_mph, vc.speed_factor * self.pack.edge(a, b).car_mph)

    def speed_at(self, a: str, b: str, into: float, tick: int, world, vc) -> tuple[float, float, tuple | None]:
        """The truck's mph on the road from a to b, `into` miles along it at `tick`, how many miles that speed holds
        (to the next edge of a storm's stretch or a closure, or the road's end), and the closure stopping it if one
        does: (its from mile, to mile, Closure). No truck moves on a closed stretch, nor past its start."""
        e = self.pack.edge(a, b)
        mph = self.free_mph(a, b, vc)
        share, holds = 1.0, e.miles - into
        for m0, m1, start, end, f in world.stretches(a, b):
            if not start <= tick < end:
                continue
            if m0 <= into < m1:
                share, holds = min(share, f), min(holds, m1 - into)
            elif into < m0:
                holds = min(holds, m0 - into)
        for m0, m1, start, end, closure in world.closures(a, b):
            if not start <= tick < end:
                continue
            if m0 <= into < m1:
                return 0.0, 0.0, (m0, m1, closure)
            if into < m0:
                holds = min(holds, m0 - into)
        return mph * share, max(holds, 1e-6), None

    def route_from(self, pos: Position, target: str, tick: int | None = None, world=None, vc=None) -> tuple[Position, list, float]:
        """The way from a position to a city: the (possibly turned-around) position, the cities to pass, miles. The
        shortest way, unless (given a world) a closure a driver knows of is on it: then the quickest way, waits
        included."""
        way = self._shortest(pos, target)
        if world is not None and self._closed_on(way, tick, world):
            return self.quickest_route(pos, target, tick, world, vc)
        return way

    def _closed_on(self, way: tuple, tick: int, world) -> bool:
        pos, route, _ = way
        roads = [(pos.a, pos.b, pos.into)] if not pos.city else []
        cities = ([pos.b] if not pos.city else [pos.city]) + (route[1:] if not pos.city else route)
        roads += [(x, y, 0.0) for x, y in zip(cities, cities[1:])]
        return any(m1 > into for a, b, into in roads for m0, m1, _, _ in world.known_closures(a, b, tick))

    def road_ticks(self, a: str, b: str, from_mile: float, tick: int, at: float, world, vc) -> float:
        """Ticks from `from_mile` to the end of the road from a to b, entered `at` ticks after `tick`, at free speed,
        held at each known closure ahead until its expected reopening."""
        mph, t, pos = self.free_mph(a, b, vc), at, from_mile
        for m0, m1, end, _ in sorted(world.known_closures(a, b, tick), key=lambda x: x[0]):
            if m1 <= from_mile:
                continue
            t += max(0.0, m0 - pos) / mph * 4
            pos = max(pos, m0)
            t = max(t, end - tick)
        return t + (self.pack.edge(a, b).miles - pos) / mph * 4 - at

    def quickest_route(self, pos: Position, target: str, tick: int, world, vc) -> tuple[Position, list, float]:
        """The quickest way to a city around the closures a driver knows of (waits included): from a city, or from a
        road going on or turning back. The position, the cities to pass, and the miles."""
        if pos.city:
            starts = [(0.0, pos.city, (), pos, 0.0)]
        else:
            length = self.pack.edge(pos.a, pos.b).miles
            turned = Position(None, pos.b, pos.a, length - pos.into)
            starts = [(self.road_ticks(pos.a, pos.b, pos.into, tick, 0.0, world, vc), pos.b, (pos.b,), pos, length - pos.into),
                      (self.road_ticks(pos.b, pos.a, length - pos.into, tick, 0.0, world, vc), pos.a, (pos.a,), turned, pos.into)]
        heap = [(t, i, city, path, p0, miles) for i, (t, city, path, p0, miles) in enumerate(starts)]
        heapq.heapify(heap)
        done, n = set(), len(heap)
        while heap:
            t, _, city, path, p0, miles = heapq.heappop(heap)
            if city == target:
                return p0, list(path), miles
            if city in done:
                continue
            done.add(city)
            for nxt in self.pack.neighbours(city):
                if nxt not in done:
                    n += 1
                    heapq.heappush(heap, (t + self.road_ticks(city, nxt, 0.0, tick, t, world, vc), n, nxt, path + (nxt,), p0,
                                          miles + self.pack.edge(city, nxt).miles))
        raise ValueError(f"no road from {pos} to {target}")

    def _shortest(self, pos: Position, target: str) -> tuple[Position, list, float]:
        if pos.city:
            path = self.pack.path(pos.city, target)
            return pos, list(path[1:]), self.pack.miles(pos.city, target)
        length = self.pack.edge(pos.a, pos.b).miles
        ahead = (length - pos.into) + self.pack.miles(pos.b, target)
        back = pos.into + self.pack.miles(pos.a, target)
        if ahead <= back:
            return pos, [pos.b] + list(self.pack.path(pos.b, target)[1:]), ahead
        turned = Position(None, pos.b, pos.a, length - pos.into)
        return turned, [pos.a] + list(self.pack.path(pos.a, target)[1:]), back

    def redraw(self, tr: Truck, comp: Component) -> None:
        comp.k += 1
        draw = failure_draw(self.key, tr.spec.id, comp.name, comp.k, 0.0, self.p)
        comp.failure_odo = tr.odometer + draw["miles_to_failure"]
        comp.warn_odo = comp.failure_odo - draw["warn_lead_miles"] if draw["warn_lead_miles"] else None
        comp.draw, comp.warned = draw, False

    @staticmethod
    def record(tr: Truck, tick: int, status: str) -> None:
        """The crew's duty for the tick: the driver at the wheel has the truck's status, a team partner is in the
        sleeper (off duty)."""
        for i, d in enumerate(tr.crew):
            d.clock.record(tick, status if i == tr.at_wheel else "OFF")

    @staticmethod
    def wheel(tr: Truck, tick: int) -> str | None:
        """Put a driver who may drive now at the wheel (a team swaps); else why the one at the wheel may not."""
        blocker = tr.clock.drive_blocker(tick)
        if blocker:
            for i, d in enumerate(tr.crew):
                if i != tr.at_wheel and d.clock.drive_blocker(tick) is None:
                    tr.at_wheel = i
                    return None
        return blocker

    def step(self, tr: Truck, tick: int, world) -> tuple[str, list, list]:
        events, moved = [], []
        if not tr.crew:                                   # nobody to drive it: it stays put
            return "PARKED", moved, events
        if tr.broken:
            status = self._repair_tick(tr, tick, world, events, moved)
            self.record(tr, tick, status)
            return status, moved, events
        if tick < tr.rest_until:
            self.record(tr, tick, "OFF")
            return "OFF", moved, events
        status = "OFF"
        while tr.tasks:
            task = tr.tasks[0]
            if tr.dwell_left is None and tr.pos.city != task["city"]:
                blocker = self.wheel(tr, tick)
                if blocker:
                    tr.rest_until = tick + min(d.clock.rest_ticks(d.clock.drive_blocker(tick) or blocker) for d in tr.crew)
                    events.append({"kind": "rest", "truck": tr.spec.id, "reason": blocker, "until": tr.rest_until})
                    status = "OFF"
                else:
                    closure = self._drive_tick(tr, tick, world, task["city"], events, moved)
                    if closure is None:
                        tr.held_since = tr.held_by = None
                        status = "D"
                    else:
                        status = self.held(tr, tick, closure, events)
                break
            if task["kind"] == "goto":
                tr.tasks.pop(0)
                events.append({"kind": "reached", "truck": tr.spec.id, "city": task["city"]})
                continue
            if task["kind"] == "pickup":
                o = world.offer(task["load"])
                if tr.dwell_left is None:
                    if tick < o.pickup_start:
                        status = "OFF"   # waiting at the shipper for the window to open
                        break
                    if tick >= o.pickup_end:
                        tr.tasks = [t for t in tr.tasks if t.get("load") != o.id]
                        events.append({"kind": "late_for_pickup", "truck": tr.spec.id, "load": o.id})
                        continue
                    tr.dwell_left, tr.stop_started = world.dwell(o.id, "pickup"), tick
                    events.append({"kind": "pickup_start", "truck": tr.spec.id, "load": o.id})
                status = "ON"
                tr.dwell_left -= 1
                if tr.dwell_left == 0:
                    events.append({"kind": "picked", "truck": tr.spec.id, "load": o.id, "dwell": tick + 1 - tr.stop_started})
                    tr.onboard.append(o.id)
                    tr.onboard_odo[o.id], tr.dwell_left = tr.odometer, None
                    tr.tasks.pop(0)
                break
            if task["kind"] == "deliver":
                o = world.offer(task["load"])
                if o.id not in tr.onboard:
                    raise RuntimeError(f"{tr.spec.id} was told to deliver {o.id} but carries {tr.onboard}: {tr.tasks}")
                if tr.dwell_left is None:
                    tr.dwell_left, tr.stop_started = world.dwell(o.id, "delivery"), tick
                    events.append({"kind": "deliver_start", "truck": tr.spec.id, "load": o.id,
                                   "late": max(0, tick - o.appointment)})
                status = "ON"
                tr.dwell_left -= 1
                if tr.dwell_left == 0:
                    events.append({"kind": "delivered", "truck": tr.spec.id, "load": o.id,
                                   "dwell": tick + 1 - tr.stop_started, "late": max(0, tr.stop_started - o.appointment)})
                    tr.onboard.remove(o.id)
                    tr.onboard_odo.pop(o.id, None)
                    tr.dwell_left = None
                    tr.tasks.pop(0)
                break
            if task["kind"] == "maintain":
                if tr.dwell_left is None:
                    tr.dwell_left, tr.stop_started = ticks(self.p.maintenance_hours), tick
                    events.append({"kind": "maintenance_start", "truck": tr.spec.id, "city": task["city"]})
                status = "OFF"           # the truck is in the shop; the driver is off duty
                tr.dwell_left -= 1
                if tr.dwell_left == 0:
                    for comp in tr.components.values():
                        self.redraw(tr, comp)
                    tr.dwell_left = None
                    tr.tasks.pop(0)
                    events.append({"kind": "maintenance_done", "truck": tr.spec.id})
                break
            raise ValueError(f"unknown task {task}")
        self.record(tr, tick, status)
        return status, moved, events

    def _drive_tick(self, tr: Truck, tick: int, world, target: str, events: list, moved: list):
        """Drive toward `target` for a tick. Returns the Closure that kept the truck from moving at all this tick (it
        waits at a city or at the closure's start), or None."""
        if not tr.route or tr.route[-1] != target:
            usual = self._shortest(tr.pos, target)[1]
            tr.pos, tr.route, _ = self.route_from(tr.pos, target, tick, world, VEHICLES[tr.spec.vclass])
            if tr.route != usual:
                self._detour(tr, tick, world, usual, events)
        time_left, turned = TICK_HOURS, False
        while time_left > 1e-9:
            if tr.pos.city:                               # leave the city onto the next road
                wait_for = self._leave_city(tr, tick, world, target, events)
                if wait_for is not None:
                    return wait_for if time_left == TICK_HOURS else None
                tr.pos = Position(None, tr.pos.city, tr.route[0], 0.0)
                events.append({"kind": "depart", "truck": tr.spec.id, "a": tr.pos.a, "b": tr.pos.b})
            a, b = tr.pos.a, tr.pos.b
            mph, holds, block = self.speed_at(a, b, tr.pos.into, tick, world, VEHICLES[tr.spec.vclass])
            if block is not None:
                m0, _, closure = block
                if not turned and tr.pos.into - m0 < 1e-3:   # at the closure's start, not caught inside: go back?
                    turned = True
                    pos, route, _ = self.quickest_route(tr.pos, target, tick, world, VEHICLES[tr.spec.vclass])
                    if pos.a != a:
                        tr.pos, tr.route = pos, route
                        events.append({"kind": "turned_back", "truck": tr.spec.id, "closure": closure.closure_id,
                                       "via": route})
                        continue
                return closure if time_left == TICK_HOURS else None
            remaining = self.pack.edge(a, b).miles - tr.pos.into
            step = min(remaining, mph * time_left, holds)
            broke = None
            if world.breakdowns:
                due = min(tr.components.values(), key=lambda c: (c.failure_odo, c.name))
                if tr.odometer + step >= due.failure_odo:
                    step, broke = max(0.0, due.failure_odo - tr.odometer), due
            tr.pos.into += step
            tr.odometer += step
            time_left -= step / mph if mph > 0 else time_left
            moved.append((a, b, step))
            for comp in sorted(tr.components.values(), key=lambda c: c.name):
                if comp.warn_odo is not None and not comp.warned and tr.odometer >= comp.warn_odo:
                    comp.warned = True
                    events.append({"kind": "fault_warning", "truck": tr.spec.id, "component": comp.name})
            if broke:
                tr.broken = {"component": broke.name, "at": tick, "option": None}
                events.append({"kind": "breakdown", "truck": tr.spec.id, "component": broke.name,
                               "failure": broke.k, "odometer": round(tr.odometer, 3), "where": tr.pos.text(self.pack)})
                return
            if remaining - step <= 1e-9:                  # reached b
                tr.pos = Position(b)
                tr.route = tr.route[1:]
                events.append({"kind": "arrive", "truck": tr.spec.id, "city": b})
                if b == target:
                    return None
        return None

    def _leave_city(self, tr: Truck, tick: int, world, target: str, events: list):
        """At a city with a known closure on the way: take the quickest way (a detour, or the same road), and return
        the closure to wait for here when the next road would still be closed by the time the truck got to it."""
        if not self._closed_on((tr.pos, tr.route, 0.0), tick, world):
            return None
        _, route, _ = self.quickest_route(tr.pos, target, tick, world, VEHICLES[tr.spec.vclass])
        if route != tr.route:
            old, tr.route = tr.route, route
            self._detour(tr, tick, world, old, events)
        city, nxt = tr.pos.city, tr.route[0]
        mph = self.free_mph(city, nxt, VEHICLES[tr.spec.vclass])
        for m0, _, end, closure in world.known_closures(city, nxt, tick):
            if tick + m0 / mph * 4 < end:
                return closure
        return None

    def _detour(self, tr: Truck, tick: int, world, instead_of: list, events: list) -> None:
        """Note a way around closed roads; it claims the adverse-conditions exception when a road closed after the
        trip was dispatched."""
        start = tr.pos.city or tr.pos.a
        events.append({"kind": "detour", "truck": tr.spec.id, "at": start, "via": tr.route, "instead_of": instead_of})
        closed = [c for x, y in zip([start] + instead_of, instead_of) for _, _, _, c in world.known_closures(x, y, tick)]
        if any(c.start > tr.dispatched_at for c in closed) and tr.clock.claim_adverse(tick):
            events.append({"kind": "adverse_conditions", "truck": tr.spec.id, "driver": tr.crew[tr.at_wheel].id, "closure": closed[0].closure_id,
                           "why": "detour around a road closed after dispatch"})

    def held(self, tr: Truck, tick: int, closure, events: list) -> str:
        """The duty status of a driver a closed road keeps from moving: parked at a city, off duty; stopped on the
        road, on duty for held_on_duty_hours, then in the sleeper. The adverse driving conditions exception is
        claimed when the road closed after the trip was dispatched."""
        if tr.held_by != closure.closure_id:
            tr.held_since, tr.held_by = tick, closure.closure_id
            events.append({"kind": "held", "truck": tr.spec.id, "closure": closure.closure_id,
                           "road": self.pack.road_name(closure.a, closure.b), "where": tr.pos.text(self.pack),
                           "reopens_about": max(closure.estimate, tick + 4)})
            if closure.start > tr.dispatched_at and tr.clock.claim_adverse(tick):
                events.append({"kind": "adverse_conditions", "truck": tr.spec.id, "driver": tr.crew[tr.at_wheel].id, "closure": closure.closure_id,
                               "why": "held by a road closed after dispatch"})
        if tr.pos.city:
            return "OFF"
        if not VEHICLES[tr.spec.vclass].sleeper:          # no sleeper berth: stuck on the road, the driver stays on duty
            return "ON"
        return "ON" if tick - tr.held_since < ticks(self.p.held_on_duty_hours) else "OFF"

    def _repair_tick(self, tr: Truck, tick: int, world, events: list, moved: list) -> str:
        b = tr.broken
        if b["option"] is None:
            return "OFF"                                  # waiting for the operator's decision
        if b["option"] == "roadside":
            b["left"] -= 1
            if b["left"] == 0:
                self._fixed(tr, events)
            return "ON"
        if b["phase"] == "tow":                           # towed at tow speed; the driver rides along on duty
            time_left = TICK_HOURS
            while time_left > 1e-9 and b["phase"] == "tow":
                if tr.pos.city == b["shop"]:
                    b["phase"], b["left"] = "shop", b["shop_ticks"]
                    events.append({"kind": "at_shop", "truck": tr.spec.id, "city": b["shop"]})
                    break
                if tr.pos.city:
                    tr.pos = Position(None, tr.pos.city, tr.route[0], 0.0)
                a, bb = tr.pos.a, tr.pos.b
                remaining = self.pack.edge(a, bb).miles - tr.pos.into
                step = min(remaining, self.p.tow_mph * time_left)
                tr.pos.into += step
                time_left -= step / self.p.tow_mph
                if remaining - step <= 1e-9:
                    tr.pos, tr.route = Position(bb), tr.route[1:]
            return "ON"
        b["left"] -= 1                                    # in the shop; the driver is off duty
        if b["left"] == 0:
            self._fixed(tr, events)
        return "OFF"

    def _fixed(self, tr: Truck, events: list) -> None:
        self.redraw(tr, tr.components[tr.broken["component"]])
        events.append({"kind": "repaired", "truck": tr.spec.id, "component": tr.broken["component"]})
        tr.broken, tr.route = None, []


# --------------------------------------------------------------------------------------------------------------
# the game

class Game:
    def __init__(self, pack: DataPack, scenario: Scenario, model_seats: set[str], npc_policy=None):
        """model_seats are played by agents through the tool methods; every other seat is played by npc_policy(game,
        seat), which must end its seat's turn."""
        self.pack, self.scenario, self.p = pack, scenario, scenario.params
        self.rules = Rules.from_pack(pack.hos)
        self.c = pack.costs
        self.costs_by_class = {name: self.unit_costs(name) for name in VEHICLES}
        self.seats = {seat: hub for seat, hub in scenario.seats}
        unknown = set(model_seats) - set(self.seats)
        if unknown:
            raise ValueError(f"unknown seats {sorted(unknown)}")
        self.model_seats, self.npc_policy = set(model_seats), npc_policy
        if self.npc_policy is None and set(self.seats) - self.model_seats:
            raise ValueError("seats without a model need an npc_policy")
        self.executor = Executor(pack, self.p, scenario.key)
        self.trucks: dict[str, Truck] = {}
        for spec in scenario.trucks:
            comps = {}
            for name, service in spec.service_miles0.items():
                draw = failure_draw(scenario.key, spec.id, name, 0, service, self.p)
                fo = spec.odometer0 + draw["miles_to_failure"]
                comps[name] = Component(name, 0, fo, fo - draw["warn_lead_miles"] if draw["warn_lead_miles"] else None, draw)
            self.trucks[spec.id] = Truck(spec, [], Position(spec.hub), spec.odometer0, comps)
        self.drivers: dict[str, Driver] = {}
        for ds in scenario.drivers:
            d = Driver(ds.id, ds.name, ds.seat, ds.kind, ds.home, DriverClock(self.rules), ds.truck)
            self.drivers[d.id] = d
            if ds.truck:
                self.trucks[ds.truck].crew.append(d)
        self.loads = {o.id: Load(o) for o in scenario.offers}
        # each storm on the stretches of road in its county, both ways: (a, b) -> [(m0, m1, start, end, share)]
        self.storm_stretches: dict[tuple[str, str], list] = {}
        for w in scenario.weather:
            for a, b, m0, m1 in w.spans:
                length = pack.edge(a, b).miles
                self.storm_stretches.setdefault((a, b), []).append((m0, m1, w.start, w.end, w.speed_factor))
                self.storm_stretches.setdefault((b, a), []).append((length - m1, length - m0, w.start, w.end, w.speed_factor))
        # each closure on its directed road(s): (a, b) -> [(m0, m1, start, end, Closure)], miles from a
        self.closed_roads: dict[tuple[str, str], list] = {}
        for c in scenario.closures:
            length = pack.edge(c.a, c.b).miles
            if c.direction in ("both", "a>b"):
                self.closed_roads.setdefault((c.a, c.b), []).append((c.m0, c.m1, c.start, c.end, c))
            if c.direction in ("both", "b>a"):
                self.closed_roads.setdefault((c.b, c.a), []).append((length - c.m1, length - c.m0, c.start, c.end, c))
        self.weather_factor: dict[str, list] = {}       # per city (its own county's storms), for the brief
        for w in scenario.weather:
            if w.city is None:
                continue
            row = self.weather_factor.setdefault(w.city, [1.0] * (self.p.week_ticks + 1))
            for t in range(w.start, w.end):
                row[t] = min(row[t], w.speed_factor)
        self.tick, self.turn, self.done = 0, 0, False
        self.ended: set[str] = set()
        self.log: list[dict] = []
        self.ledger = {seat: {k: 0.0 for k in REVENUE + COSTS} for seat in self.seats}
        self._turn_ledger = {seat: {} for seat in self.seats}
        self.turn_start_log_index = 0
        self.world = RealWorld(self)
        self._npc_phase = False
        self._exogenous(0)
        self._open_turn()
        self._advance_while_all_ended()

    # ---- helpers -------------------------------------------------------------------------------------------
    def emit(self, kind: str, **data) -> None:
        self.log.append({"t": self.tick, "kind": kind, **data})

    def book(self, seat: str, category: str, usd: float) -> None:
        if category not in self.ledger[seat]:
            raise KeyError(category)
        self.ledger[seat][category] += usd
        self._turn_ledger[seat][category] = self._turn_ledger[seat].get(category, 0.0) + usd

    def profit(self, seat: str) -> float:
        led = self.ledger[seat]
        return sum(led[k] for k in REVENUE) - sum(led[k] for k in COSTS)

    def _truck(self, seat: str, truck_id: str) -> Truck:
        tr = self.trucks.get(truck_id)
        if tr is None or tr.spec.seat != seat:
            raise ActionError(f"{truck_id} is not one of seat {seat}'s trucks ({', '.join(self.seat_trucks(seat))})")
        return tr

    def seat_trucks(self, seat: str) -> list[str]:
        return sorted(t for t, tr in self.trucks.items() if tr.spec.seat == seat)

    def _require_open_turn(self, seat: str) -> None:
        if self.done:
            raise ActionError("the week is over")
        if seat not in self.seats:
            raise ActionError(f"unknown seat {seat}")
        if seat in self.ended:
            raise ActionError(f"seat {seat} has ended turn {self.turn}; wait for the next turn")

    def _visible_offer(self, load_id: str) -> Load:
        ld = self.loads.get(load_id)
        if ld is None or ld.offer.posted > self.tick:
            raise ActionError(f"no load {load_id} on the board")
        return ld

    @staticmethod
    def fits(vclass: str, o: Offer) -> bool:
        """Whether a unit of this class can carry the load (alone): its pallets, weight and kind of freight."""
        vc = VEHICLES[vclass]
        return o.pallets <= vc.pallets and o.weight_lb <= vc.payload_lb and (o.equipment == "dry" or vc.reefer)

    def _ride_together(self, tr: Truck, load_ids: list) -> str | None:
        """Why these loads can't be aboard the truck at once, or None: pallets, weight, one kind of freight."""
        vc, offers = VEHICLES[tr.spec.vclass], [self.loads[lid].offer for lid in load_ids]
        kinds = {o.equipment for o in offers}
        if "reefer" in kinds and not vc.reefer:
            return f"reefer freight needs a reefer; {tr.spec.id} is a {vc.label}"
        if len(kinds) > 1:
            return "dry and reefer freight can't share a trailer"
        pallets, lb = sum(o.pallets for o in offers), sum(o.weight_lb for o in offers)
        if pallets > vc.pallets:
            return f"{pallets} pallets aboard; {tr.spec.id} ({vc.label}) takes {vc.pallets}"
        if lb > vc.payload_lb:
            return f"{lb:,} lb aboard; {tr.spec.id} ({vc.label}) carries {vc.payload_lb:,} lb"
        return None

    def _offer_view(self, ld: Load) -> dict:
        o = ld.offer
        return {"id": o.id, "orig": o.orig, "dest": o.dest, "equipment": o.equipment, "miles": o.miles,
                "size": o.size, "pallets": o.pallets, "weight_lb": o.weight_lb, "expedite": o.expedite,
                "fits": [name for name in VEHICLES if self.fits(name, o)],
                "pickup_window": f"{clock(o.pickup_start)}-{clock(o.pickup_end)}", "pickup_start_tick": o.pickup_start,
                "pickup_end_tick": o.pickup_end, "appointment": clock(o.appointment), "appointment_tick": o.appointment,
                "linehaul_usd": o.linehaul_usd, "fuel_surcharge_per_mile": o.fsc_per_mile,
                "revenue_usd": round(o.linehaul_usd + o.fsc_per_mile * o.miles, 2),
                "expires": clock(o.expires), "expires_tick": o.expires}

    # ---- the world between turns ----------------------------------------------------------------------------
    def _exogenous(self, t: int) -> None:
        """What happens at tick t that no operator controls: offers post, expire and get cancelled, pickup windows
        close on loads nobody loaded, weather starts and ends."""
        for ld in self.loads.values():
            o = ld.offer
            if o.posted == t:
                self.emit("offer_posted", load=o.id, orig=o.orig, dest=o.dest, equipment=o.equipment)
            if o.expires == t and ld.status == "open":
                ld.status = "expired"
                self.emit("offer_expired", load=o.id)
            if o.cancel_at == t and ld.status in ("open", "owned"):
                self._cancel(ld)
            if o.pickup_end == t and ld.status == "owned" and not self._loading_now(ld):
                ld.status = "missed"
                self.book(ld.owner, "missed_pickup_penalty", self.p.missed_pickup_penalty_usd)
                self._drop_tasks(ld)
                self.emit("missed_pickup", seat=ld.owner, load=o.id, penalty=self.p.missed_pickup_penalty_usd)
        for w in self.scenario.weather:
            if w.start == t:
                self.emit("weather_start", event_id=w.event_id, type=w.type, where=w.where, city=w.city,
                          spans=[list(x) for x in w.spans], speed_factor=w.speed_factor)
            if w.end == t:
                self.emit("weather_end", event_id=w.event_id, type=w.type, where=w.where, city=w.city)
        for c in self.scenario.closures:
            if c.start == t:
                self.emit("closure_start", closure_id=c.closure_id, road=self.pack.road_name(c.a, c.b), a=c.a, b=c.b,
                          m0=c.m0, m1=c.m1, direction=c.direction, cause=c.cause, where=c.where, source=c.source,
                          what=c.what, reopens_about=c.estimate)
            if c.end == t:
                self.emit("closure_end", closure_id=c.closure_id, road=self.pack.road_name(c.a, c.b))

    def _loading_now(self, ld: Load) -> bool:
        if ld.truck is None:
            return False
        tr = self.trucks[ld.truck]
        return bool(tr.tasks) and tr.tasks[0].get("load") == ld.offer.id and tr.tasks[0]["kind"] == "pickup" and tr.dwell_left is not None

    def _cancel(self, ld: Load) -> None:
        if ld.status == "open":
            ld.status = "cancelled"
            ld.claims.clear()
            self.emit("offer_withdrawn", load=ld.offer.id)
            return
        dispatched = ld.truck is not None
        ld.status = "cancelled"
        if dispatched:
            self.book(ld.owner, "tonu_fee", self.c["tonu_fee_usd"])
        self._drop_tasks(ld)
        self.emit("load_cancelled", seat=ld.owner, load=ld.offer.id, tonu_usd=self.c["tonu_fee_usd"] if dispatched else 0.0)

    @staticmethod
    def _remove_load_tasks(tr: Truck, load_id: str) -> None:
        """Take a load's stops off a truck's plan. The stop under way is reset only if it was that load's."""
        if tr.tasks and tr.tasks[0].get("load") == load_id:
            tr.dwell_left, tr.route = None, []
        tr.tasks = [t for t in tr.tasks if t.get("load") != load_id]

    def _drop_tasks(self, ld: Load) -> None:
        if ld.truck is None:
            return
        self._remove_load_tasks(self.trucks[ld.truck], ld.offer.id)
        ld.truck = None

    def unit_costs(self, vclass: str) -> dict:
        """What a unit of a vehicle class costs (costs.json, by the class's keys): fuel economy, its driver per on-duty
        hour (wage and benefits), wear per mile, its fixed cost per day, and its reefer unit's fuel per hour."""
        vc, c = VEHICLES[vclass], self.c
        return {"mpg": c[vc.mpg], "driver_hour": c[vc.driver_wage_hourly] * (1 + c["driver_benefits_load"]),
                "wear_mile": c[vc.repair_per_mile] + c[vc.tires_per_mile], "fixed_day": c[vc.lease_per_day] + c[vc.insurance_per_day],
                "reefer_gph": c["reefer_unit_gallons_per_hour"] if vc.reefer else 0.0}

    def _reefer_running(self, tr: Truck) -> bool:
        return any(self.loads[lid].offer.equipment == "reefer" for lid in tr.onboard)

    def _simulate(self, n: int) -> None:
        for _ in range(n):
            t = self.tick
            for tid in sorted(self.trucks):
                tr = self.trucks[tid]
                seat, k = tr.spec.seat, self.costs_by_class[tr.spec.vclass]
                status, moved, events = self.executor.step(tr, t, self.world)
                self._pay(seat, tr, status, sum(m for _, _, m in moved))
                self.book(seat, "overhead", self.c["overhead_per_unit_per_day_usd"] / 96)
                if tr.spec.owner == "owner_operator":           # its owner pays the truck; the carrier, its trailer and cover
                    self.book(seat, "owner_operator_units", self.c["owner_operator_carrier_cost_per_day_usd"] / 96)
                else:
                    self.book(seat, "fixed", k["fixed_day"] / 96)
                    for a, b, miles in moved:
                        price = self.scenario.diesel[self.pack.cities[a].padd]
                        self.book(seat, "fuel", miles / k["mpg"] * price)
                        self.book(seat, "per_mile_wear", miles * k["wear_mile"])
                    if k["reefer_gph"] and self._reefer_running(tr):     # the reefer unit runs while reefer freight is aboard
                        where = tr.pos.city or tr.pos.a
                        self.book(seat, "fuel", k["reefer_gph"] * TICK_HOURS * self.scenario.diesel[self.pack.cities[where].padd])
                for ev in events:
                    self._apply(seat, tr, ev)
            for d in self.drivers.values():           # drivers on no unit are off duty at home (a temp: before arriving)
                if d.truck is None:
                    d.clock.record(t, "OFF")
            self._temps(t)
            self.tick += 1
            if self.tick % 4 == 0:                      # every hour: where each truck is and what it's doing
                self.emit("snapshot", trucks={tid: self._snapshot(self.trucks[tid]) for tid in sorted(self.trucks)})
            if self.tick <= self.p.week_ticks:
                self._exogenous(self.tick)

    def oo_pay(self, o: Offer, detention_hours: float = 0.0) -> float:
        """An owner-operator's settlement for a load: their share of the linehaul, all of the fuel surcharge and of
        the detention pay (truth in leasing, 49 CFR 376.12: the lease says how it is figured)."""
        return round(self.c["owner_operator_linehaul_share"] * o.linehaul_usd + o.fsc_per_mile * o.miles
                     + detention_hours * self.c["detention_rate_per_hour_usd"], 2)

    def _pay(self, seat: str, tr: Truck, status: str, miles: float) -> None:
        """Driver pay for a unit's tick: a company driver by the mile, every mile (on a team, each gets
        team_pay_factor / 2 of a solo driver's rate), a local driver by the on-duty hour, a temp at the agency's hourly
        rate (its daily minimum is topped up at midnight). Owner-operators are paid per load, at delivery."""
        share = self.c["team_pay_factor"] / len(tr.crew) if len(tr.crew) > 1 else 1.0
        on_duty = status in ("ON", "D")
        for i, d in enumerate(tr.crew):
            if d.kind == "company" and miles:
                self.book(seat, "driver_wages", miles * self.c["company_driver_pay_per_mile_usd"] * share)
            elif d.kind == "local" and i == tr.at_wheel and on_duty:
                self.book(seat, "driver_wages", self.c["light_driver_wage_hourly_usd"] * (1 + self.c["driver_benefits_load"]) * TICK_HOURS)
            elif d.kind == "temp" and i == tr.at_wheel and on_duty:
                self.book(seat, "temp_drivers", self.c["temp_driver_hourly_usd"] * TICK_HOURS)
                d.duty_today += 1

    def _temps(self, t: int) -> None:
        """At the end of each 24 hours of a temp's hire, they're paid up to the agency's daily minimum; a temp past
        their hire who is back at the hub leaves (off their unit's crew)."""
        for d in self.drivers.values():
            if d.kind != "temp":
                continue
            if d.available_from <= t < d.available_until and (t + 1 - d.available_from) % 96 == 0:
                short = ticks(self.c["temp_driver_minimum_hours_per_day"]) - d.duty_today
                if short > 0:
                    self.book(d.seat, "temp_drivers", short * self.c["temp_driver_hourly_usd"] * TICK_HOURS)
                d.duty_today = 0
            if t >= d.available_until and d.truck and self.trucks[d.truck].pos.city == d.home and not self.trucks[d.truck].tasks:
                self._crew_off(d)
                self.emit("temp_left", seat=d.seat, driver=d.id)

    def _crew_off(self, d: Driver) -> None:
        tr = self.trucks[d.truck]
        tr.crew.remove(d)
        tr.at_wheel = 0
        d.truck = None

    def _apply(self, seat: str, tr: Truck, ev: dict) -> None:
        kind = ev["kind"]
        if kind == "breakdown" and tr.spec.owner == "owner_operator":     # the owner takes the cheaper repair, and pays
            options = self.repair_options(tr)
            self._order_repair(tr, min(options, key=lambda k: (options[k]["usd"], k)), options, book=False)
        if kind == "rest" and ev["reason"] != "break" and not VEHICLES[tr.spec.vclass].sleeper and tr.pos.city != tr.spec.hub:
            self.book(seat, "lodging", self.c["lodging_per_night_usd"])      # no sleeper berth: a motel, away from home
            ev = {**ev, "lodging_usd": self.c["lodging_per_night_usd"]}
        if kind in ("picked", "delivered", "pickup_start", "deliver_start", "late_for_pickup"):
            ld = self.loads[ev["load"]]
            if kind == "picked":
                ld.status, ld.picked_at, ld.pickup_dwell = "picked", self.tick, ev["dwell"]
            elif kind == "delivered":
                o = ld.offer
                ld.status = "delivered"
                self.book(seat, "linehaul", o.linehaul_usd)
                self.book(seat, "fuel_surcharge", round(o.fsc_per_mile * o.miles, 2))
                billable = sum(max(0.0, hours(d) - self.c["detention_free_hours"]) for d in (ld.pickup_dwell, ev["dwell"]))
                if billable:
                    self.book(seat, "detention_pay", billable * self.c["detention_rate_per_hour_usd"])
                late_h = hours(ev["late"])
                if late_h:
                    self.book(seat, "late_penalty", min(late_h * self.p.late_penalty_per_hour_usd, 0.5 * o.linehaul_usd))
                if tr.spec.owner == "owner_operator":     # the owner's settlement: their share, the surcharge, detention
                    self.book(seat, "owner_operator_pay", self.oo_pay(o, billable))
                ev = {**ev, "revenue_usd": round(o.linehaul_usd + o.fsc_per_mile * o.miles + billable * self.c["detention_rate_per_hour_usd"], 2),
                      "late_h": late_h}
            elif kind == "late_for_pickup" and ld.status == "owned":
                ld.status = "missed"
                ld.truck = None
                self.book(seat, "missed_pickup_penalty", self.p.missed_pickup_penalty_usd)
        self.emit(kind, seat=seat, **{k: v for k, v in ev.items() if k != "kind"})

    # ---- turns ------------------------------------------------------------------------------------------------
    def _open_turn(self) -> None:
        """Open the turn and let every rule-based seat play it. Their end_turn calls don't advance the clock;
        _advance_while_all_ended does, in a plain loop (no recursion across turns)."""
        self.turn_start_log_index = len(self.log)
        self.emit("turn", turn=self.turn)
        self._npc_phase = True
        try:
            for seat in sorted(self.seats):
                if seat not in self.model_seats:
                    self.npc_policy(self, seat)
                    if seat not in self.ended:
                        raise RuntimeError(f"the policy for seat {seat} did not end its turn")
        finally:
            self._npc_phase = False

    def _advance_while_all_ended(self) -> None:
        while not self.done and self.ended == set(self.seats):
            self._advance()

    def end_turn(self, seat: str) -> dict:
        self._require_open_turn(seat)
        self.ended.add(seat)
        if not self._npc_phase:
            self._advance_while_all_ended()
        return {"ended": True, "turn": self.turn, "done": self.done}

    def _advance(self) -> None:
        self._resolve_claims()
        self._simulate(self.p.turn_ticks)
        for seat in sorted(self.seats):
            self.emit("ledger", seat=seat, turn=self.turn, items={k: round(v, 2) for k, v in sorted(self._turn_ledger[seat].items())})
            self._turn_ledger[seat] = {}
        self.emit("positions", trucks={tid: {"city": tr.pos.city, "a": tr.pos.a, "b": tr.pos.b, "into": round(tr.pos.into, 2),
                                             "odometer": round(tr.odometer, 1)} for tid, tr in sorted(self.trucks.items())})
        self.turn += 1
        self.ended.clear()
        if self.tick >= self.p.week_ticks:
            self._finalize()
            return
        self._open_turn()

    def _resolve_claims(self) -> None:
        for lid in sorted(self.loads):
            ld = self.loads[lid]
            if not ld.claims:
                continue
            claimants = sorted(ld.claims)
            ld.claims = set()
            if ld.status != "open":
                for seat in claimants:
                    self.emit("claim_lost", seat=seat, load=lid, reason=f"the load is {ld.status}")
                    for tr in self.trucks.values():
                        if tr.spec.seat == seat:
                            self._remove_load_tasks(tr, lid)
                ld.pending_trucks = {}
                continue
            if len(claimants) == 1:
                winner = claimants[0]
            else:
                dist = {s: self._nearest_deadhead(s, ld.offer) for s in claimants}
                best = min(dist.values())
                tied = [s for s in claimants if dist[s] == best]
                winner = tied[0] if len(tied) == 1 else stream(self.scenario.key, "conflict", lid).choice(tied)
            ld.status, ld.owner = "owned", winner
            ld.truck = ld.pending_trucks.get(winner)
            ld.pending_trucks = {}
            self.emit("claim_won", seat=winner, load=lid, contested=len(claimants) > 1)
            for seat in claimants:
                if seat != winner:
                    self.emit("claim_lost", seat=seat, load=lid, reason=f"seat {winner} had a truck closer to the pickup")
                    for tr in self.trucks.values():
                        if tr.spec.seat == seat:
                            self._remove_load_tasks(tr, lid)

    def _nearest_deadhead(self, seat: str, o: Offer) -> float:
        best = math.inf
        for tid in self.seat_trucks(seat):
            tr = self.trucks[tid]
            if not self.fits(tr.spec.vclass, o):
                continue
            best = min(best, self.executor.route_from(tr.pos, o.orig)[2])
        return round(best, 3)

    def _finalize(self) -> None:
        """End-of-week value, so a run can't win by stranding trucks or ignoring breakdowns at the end."""
        for tid in sorted(self.trucks):
            tr = self.trucks[tid]
            seat, items, k = tr.spec.seat, {}, self.costs_by_class[tr.spec.vclass]
            if tr.spec.owner == "owner_operator":       # their truck, their trip home; the carrier keeps its margin
                for lid in tr.onboard:
                    o = self.loads[lid].offer
                    share = min(1.0, max(0.0, (tr.odometer - tr.onboard_odo[lid]) / o.miles)) if o.miles else 1.0
                    credit = share * (1 - self.c["owner_operator_linehaul_share"]) * o.linehaul_usd
                    self.book(seat, "terminal_in_transit", credit)
                    items.setdefault("in_transit", []).append({"load": lid, "share": round(share, 3), "usd": round(credit, 2)})
                if items:
                    self.emit("terminal", seat=seat, truck=tid, **items)
                continue
            if tr.broken and tr.broken["option"] is None:
                options = self.repair_options(tr)
                cheapest = min(v["usd"] for v in options.values())
                self.book(seat, "terminal_unrepaired", cheapest)
                items["unrepaired_usd"] = round(cheapest, 2)
            miles_home = self.executor.route_from(tr.pos, tr.spec.hub)[2]
            if miles_home > 0:
                price = self.scenario.diesel[self.pack.cities[tr.spec.hub].padd]
                cost = miles_home / k["mpg"] * price + miles_home * k["wear_mile"] + miles_home / self.p.terminal_deadhead_mph * k["driver_hour"]
                self.book(seat, "terminal_deadhead", cost)
                items["deadhead_home_miles"], items["deadhead_home_usd"] = round(miles_home, 1), round(cost, 2)
            for lid in tr.onboard:                        # each load aboard, for the share of its miles driven
                o = self.loads[lid].offer
                share = min(1.0, max(0.0, (tr.odometer - tr.onboard_odo[lid]) / o.miles)) if o.miles else 1.0
                credit = share * (o.linehaul_usd + o.fsc_per_mile * o.miles)
                self.book(seat, "terminal_in_transit", credit)
                items.setdefault("in_transit", []).append({"load": lid, "share": round(share, 3), "usd": round(credit, 2)})
            if items:
                self.emit("terminal", seat=seat, truck=tid, **items)
        self.done = True
        self.emit("week_over", profit={s: round(self.profit(s), 2) for s in sorted(self.seats)})

    # ---- tools: reading ----------------------------------------------------------------------------------------
    def situation(self, seat: str) -> dict:
        if seat not in self.seats:
            raise ActionError(f"unknown seat {seat}")
        since = [e for e in self.log[self._seat_mark(seat):] if e.get("seat") == seat]   # storms: weather_now
        posted = sum(1 for e in self.log[self._seat_mark(seat):] if e["kind"] == "offer_posted")
        return {"seat": seat, "hub": self.seats[seat], "clock": clock(self.tick), "tick": self.tick, "turn": self.turn,
                "turns_total": self.p.week_ticks // self.p.turn_ticks, "done": self.done,
                "profit_usd": round(self.profit(seat), 2),
                "ledger_usd": {k: round(v, 2) for k, v in self.ledger[seat].items() if v},
                "fleet": [self._truck_line(self.trucks[t]) for t in self.seat_trucks(seat)],
                "drivers_at_home": [f"{d.id} {d.name} ({d.kind.replace('_', '-')})" for d in sorted(self.drivers.values(), key=lambda d: d.id)
                                    if d.seat == seat and d.truck is None and d.available_from <= self.tick < d.available_until],
                "my_loads": [self._my_load(ld) for ld in sorted(self.loads.values(), key=lambda x: x.offer.id)
                             if ld.owner == seat and ld.status in ("owned", "picked")],
                "pending_claims": sorted(lid for lid, ld in self.loads.items() if seat in ld.claims),
                "open_offers": sum(1 for ld in self.loads.values() if ld.status == "open" and ld.offer.posted <= self.tick),
                "offers_posted_since_last_turn": posted,
                "weather_now": self._weather_now(),
                "closures_now": self._closures_now(),
                "events_since_last_turn": since[-60:]}

    def _seat_mark(self, seat: str) -> int:
        """Events since the start of the previous turn (what happened while the clock ran)."""
        marks = [i for i, e in enumerate(self.log) if e["kind"] == "turn"]
        return marks[-2] if len(marks) >= 2 else 0

    def _truck_line(self, tr: Truck) -> dict:
        state = ("broken down" if tr.broken else "resting (HOS)" if self.tick < tr.rest_until else
                 f"{tr.tasks[0]['kind']} {tr.tasks[0].get('load') or tr.tasks[0]['city']}" if tr.tasks else "idle")
        vc, aboard = VEHICLES[tr.spec.vclass], [self.loads[lid].offer for lid in tr.onboard]
        return {"truck": tr.spec.id, "unit": vc.label, "class": vc.name,
                **({"owner_operator": True} if tr.spec.owner == "owner_operator" else {}),
                "crew": [f"{d.id} {d.name}" for d in tr.crew] or "none: parked at the hub",
                "where": tr.pos.text(self.pack), "state": state, "aboard": list(tr.onboard),
                "room": f"{vc.pallets - sum(o.pallets for o in aboard)} of {vc.pallets} pallets, "
                        f"{vc.payload_lb - sum(o.weight_lb for o in aboard):,} of {vc.payload_lb:,} lb",
                "next_stops": [f"{t['kind']} {t.get('load') or ''} @ {t['city']}".replace("  ", " ") for t in tr.tasks],
                "hos": self._hos(tr), "fault_warnings": sorted(c.name for c in tr.components.values() if c.warned)}

    def _hos(self, tr: Truck) -> dict | list:
        """Hours left for the driver at the wheel (a solo), or for each of a team."""
        rows = []
        for i, d in enumerate(tr.crew):
            s = d.clock.summary(self.tick)
            s.update(driver=d.name, at_wheel=i == tr.at_wheel)
            rows.append(s)
        out = rows[0] if len(rows) == 1 else rows
        if rows and self.tick < tr.rest_until:
            (out if isinstance(out, dict) else out[0])["resting_until"] = clock(tr.rest_until)
        return out if rows else {"no_driver": True}

    def _my_load(self, ld: Load) -> dict:
        v = self._offer_view(ld)
        v.update(status=ld.status, truck=ld.truck)
        return v

    def _closures_now(self) -> list:
        """Every road closed now, as announced: where, which way, why, and the expected reopening."""
        out = []
        for c in self.scenario.closures:
            if c.start <= self.tick < c.end:
                a, b = self.pack.cities[c.a].name, self.pack.cities[c.b].name
                way = {"both": "both ways", "a>b": f"toward {b} only", "b>a": f"toward {a} only"}[c.direction]
                out.append({"road": self.pack.road_name(c.a, c.b), "miles": f"{c.m0:.0f}-{c.m1:.0f} from {a}", "closed": way,
                            "cause": f"{c.cause}, {c.where}", "source": c.source, "since": clock(c.start),
                            "reopens_about": clock(max(c.estimate, self.tick + 4))})
        return out

    def _weather_now(self) -> list:
        """The storms under way, one line per road (its worst storm, the stretch it covers) and per city (storms in
        its own county). NOAA files one storm for many counties, so a line per storm record would be hundreds."""
        return self._weather_summary([w for w in self.scenario.weather if w.start <= self.tick < w.end],
                                     lambda w: (w.start, w.end))

    def _weather_summary(self, storms: list, when) -> list:
        """One line per road: its storms, the miles they cover, the slowest speed on it, and the hours they add to a
        trip down the whole road (piece by piece, each piece at its worst storm's speed)."""
        roads: dict[tuple[str, str], dict] = {}
        for w in storms:
            start, end = when(w)
            for a, b, m0, m1 in w.spans:
                r = roads.setdefault((a, b), {"types": set(), "pieces": [], "from": start, "to": end})
                r["types"].add(w.type)
                r["pieces"].append((m0, m1, w.speed_factor))
                r["from"], r["to"] = min(r["from"], start), max(r["to"], end)
        out = []
        for (a, b), r in sorted(roads.items()):
            mph = self.executor.free_mph(a, b, VEHICLES["tractor_dry"])      # hours added for a tractor
            cuts = sorted({m for m0, m1, _ in r["pieces"] for m in (m0, m1)})
            added, slowest = 0.0, 1.0
            for x0, x1 in zip(cuts, cuts[1:]):
                f = min((f for m0, m1, f in r["pieces"] if m0 <= x0 and x1 <= m1), default=1.0)
                added += (x1 - x0) / mph * (1 / f - 1)
                slowest = min(slowest, f)
            out.append({"road": self.pack.road_name(a, b), "what": ", ".join(sorted(r["types"])),
                        "miles": f"{cuts[0]:.0f}-{cuts[-1]:.0f} from {self.pack.cities[a].name}",
                        "slowest_speed_share": slowest, "adds_hours": round(added, 1),
                        "from": clock(r["from"]), "until": clock(r["to"])})
        return out

    def list_loads(self, seat: str, origin: str | None = None, equipment: str | None = None, size: str | None = None,
                   fits: str | None = None, limit: int | None = None) -> dict:
        """The open offers, optionally from one origin, of one equipment or size, or that a vehicle class can carry;
        the newest first when `limit` cuts the list (the count left out is reported)."""
        if origin is not None and origin not in self.pack.cities:
            raise ActionError(f"unknown city {origin}; cities are {', '.join(sorted(self.pack.cities))}")
        if equipment not in (None, "dry", "reefer"):
            raise ActionError("equipment is 'dry' or 'reefer'")
        if size not in (None, "full", "partial", "small"):
            raise ActionError("size is 'full', 'partial' or 'small'")
        if fits is not None and fits not in VEHICLES:
            raise ActionError(f"fits is a vehicle class: {', '.join(VEHICLES)}")
        out = []
        for lid in sorted(self.loads):
            ld = self.loads[lid]
            o = ld.offer
            if ld.status != "open" or o.posted > self.tick or (origin and o.orig != origin) or (equipment and o.equipment != equipment) \
                    or (size and o.size != size) or (fits and not self.fits(fits, o)):
                continue
            v = self._offer_view(ld)
            v["claimed_by_you"] = seat in ld.claims
            out.append(v)
        left_out = 0
        if limit is not None and len(out) > limit:
            out.sort(key=lambda v: (-self.loads[v["id"]].offer.posted, v["id"]))
            left_out, out = len(out) - limit, sorted(out[:limit], key=lambda v: v["id"])
        return {"clock": clock(self.tick), "loads": out, **({"more_not_listed": left_out} if left_out else {})}

    def get_truck(self, seat: str, truck_id: str) -> dict:
        tr = self._truck(seat, truck_id)
        out = self._truck_line(tr)
        out.update(odometer=round(tr.odometer, 1), hub=tr.spec.hub,
                   duty_log_today=[[clock(s), clock(e), st] for s, e, st in tr.clock.log if e > self.tick - 96])
        if tr.broken:
            out["breakdown"] = {"component": tr.broken["component"], "since": clock(tr.broken["at"]),
                                "decision": tr.broken["option"], "options": self.repair_options(tr) if tr.broken["option"] is None else None}
        return out

    def repair_options(self, tr: Truck) -> dict:
        draw, factor = tr.components[tr.broken["component"]].draw, VEHICLES[tr.spec.vclass].repair_cost_factor
        opts = {}
        if draw["roadside"]:
            opts["roadside"] = {"hours": hours(draw["roadside"]["ticks"]), "usd": round(draw["roadside"]["usd"] * factor, 2)}
        shop, miles = self._nearest_shop(tr)
        tow_usd = self.p.tow_base_usd + self.p.tow_per_mile_usd * miles
        opts["tow"] = {"shop": shop, "tow_miles": round(miles, 1), "tow_hours": round(miles / self.p.tow_mph, 2),
                       "repair_hours": hours(draw["shop"]["ticks"]), "usd": round(tow_usd + draw["shop"]["usd"] * factor, 2)}
        return opts

    def _nearest_shop(self, tr: Truck) -> tuple[str, float]:
        best = None
        for cid in sorted(self.pack.cities):
            if self.pack.cities[cid].freight_node:
                miles = self.executor.route_from(tr.pos, cid)[2]
                if best is None or miles < best[1]:
                    best = (cid, miles)
        return best

    def get_market(self, seat: str) -> dict:
        window = [ld.offer for ld in self.loads.values() if self.tick - 96 <= ld.offer.posted <= self.tick]
        by_orig = {}
        for o in window:
            by_orig.setdefault(o.orig, []).append(o.linehaul_usd / o.miles)
        return {"week_of": self.scenario.monday,
                "diesel_usd_per_gal": {f"PADD {k} ({'Midwest' if k == 2 else 'Gulf Coast'})": v for k, v in self.scenario.diesel.items()},
                "cities_by_padd": {c.id: c.padd for c in self.pack.cities.values()},
                "fuel_surcharge_per_mile": {f"PADD {k}": round(max(0.0, (v - self.p.fsc_base_price) / self.p.fsc_mpg), 3) for k, v in self.scenario.diesel.items()},
                "linehaul_per_mile_by_origin_last_24h": {k: round(sum(v) / len(v), 2) for k, v in sorted(by_orig.items())},
                "costs_by_unit": {VEHICLES[name].label: {"fuel_economy_mpg": k["mpg"], "driver_per_on_duty_hour_usd": round(k["driver_hour"], 2),
                                                         "wear_per_mile_usd": round(k["wear_mile"], 3), "fixed_per_day_usd": k["fixed_day"],
                                                         **({"reefer_unit_gallons_per_hour": k["reefer_gph"]} if k["reefer_gph"] else {})}
                                  for name, k in self.costs_by_class.items()},
                "drivers": {"company_driver_per_mile_usd": self.c["company_driver_pay_per_mile_usd"],
                            "team_per_mile_usd": round(self.c["company_driver_pay_per_mile_usd"] * self.c["team_pay_factor"], 3),
                            "local_driver_per_on_duty_hour_usd": round(self.c["light_driver_wage_hourly_usd"] * (1 + self.c["driver_benefits_load"]), 2),
                            "temp_driver": f"${self.c['temp_driver_hourly_usd']:.0f}/h on duty, at least {self.c['temp_driver_minimum_hours_per_day']:.0f} h a day, {self.p.temp_notice_hours:.0f} h notice",
                            "owner_operators": f"you pay {self.c['owner_operator_linehaul_share']:.0%} of the linehaul, all of the fuel surcharge and detention, "
                                               f"and ${self.c['owner_operator_carrier_cost_per_day_usd']:.0f}/day for their trailer and cover; they pay the rest"},
                "costs": {"lodging_per_night_usd": self.c["lodging_per_night_usd"],
                          "detention": f"{self.c['detention_rate_per_hour_usd']:.0f}/h after {self.c['detention_free_hours']:.0f} h free, paid to you",
                          "late_penalty_per_hour_usd": self.p.late_penalty_per_hour_usd,
                          "missed_pickup_penalty_usd": self.p.missed_pickup_penalty_usd,
                          "release_penalty_usd": self.p.release_penalty_usd}}

    def get_forecast(self, seat: str) -> dict:
        """Weather in the next 72 h as a forecaster would have it: each coming event is reported with a probability
        that falls with lead time, with its timing off by an error that grows with lead time; some reports are false
        alarms. Drawn per (event, turn), so every seat gets the same forecast in the same turn."""
        if seat not in self.seats:
            raise ActionError(f"unknown seat {seat}")
        p, key, now = self.p, self.scenario.key, self.tick
        reports = []
        for w in self.scenario.weather:
            lead_h = hours(w.start - now)
            if not (0 < lead_h <= 72):
                continue
            rng = stream(key, "wx", w.event_id, self.turn)
            p_detect = next(pr for lim, pr in p.weather_detect_by_lead_hours if lead_h <= lim)
            if rng.random() < p_detect:
                err = p.weather_timing_error_per_lead_hour * lead_h
                s = max(now + 1, w.start + ticks(rng.gauss(0, err)))
                e = max(s + 4, w.end + ticks(rng.gauss(0, err)))
                reports.append((w, s, e))
        false_alarms = []
        for cid in sorted(self.pack.cities):
            rng = stream(key, "wx_false_alarm", cid, self.turn)
            if rng.random() < p.weather_false_alarm_per_city_turn:
                s = now + ticks(rng.uniform(6, 48))
                kind = rng.choice(["Flash Flood", "Thunderstorm Wind", "Dense Fog", "High Wind"])
                county = next(g for g, c in self.pack.county_city.items() if c == cid)
                false_alarms.append((SimpleNamespace(type=kind, spans=tuple(self.pack.county_spans[county]), speed_factor=p.weather_speed[kind]),
                                     s, s + ticks(rng.uniform(2, 8))))
        reported = reports + false_alarms
        when = {id(w): (s, e) for w, s, e in reported}
        reports = self._weather_summary([w for w, _, _ in reported], lambda w: when[id(w)])
        for r in reports:                         # how likely its storms are to close the road (the modeled rates)
            chance = max(p.closure_probability.get(t, 0.0) for t in r["what"].split(", "))
            r["closure_risk"] = next((name for floor, name in p.closure_risk_levels if chance > floor), "none")
        reports.sort(key=lambda r: (r["from"], r["road"]))
        outlook = None
        if self.scenario.diesel_next:
            rng = stream(key, "diesel_outlook", self.turn)
            outlook = {f"PADD {k}": round(v + rng.gauss(0, p.diesel_outlook_sd), 3) for k, v in self.scenario.diesel_next.items()}
        return {"clock": clock(now), "weather_now": self._weather_now(), "weather_next_72h": reports,
                "diesel_next_week_outlook": outlook,
                "fault_warnings": {t: sorted(c.name for c in self.trucks[t].components.values() if c.warned)
                                   for t in self.seat_trucks(seat) if any(c.warned for c in self.trucks[t].components.values())}}

    # ---- tools: planning ---------------------------------------------------------------------------------------
    def _tasks_for(self, tr: Truck, load_ids: list[str], seat: str, check_ownership: bool, stops: list | None = None) -> list:
        """The truck's tasks. A stop under way finishes first (a load being picked up is then aboard). Then, with
        `stops`, those stops in that order: each new load's id twice, its first appearance the pickup and its second
        the delivery, and a load already aboard once, for its delivery (any aboard and not listed are delivered
        first). Without, the loads aboard are delivered, then each of `load_ids` picked up and delivered in turn. At
        every stop the loads aboard must fit together: pallets, weight, one kind of freight."""
        tasks, aboard = [], list(tr.onboard)
        if tr.tasks and tr.dwell_left is not None and tr.tasks[0]["kind"] != "deliver":
            running = dict(tr.tasks[0])                      # a stop already under way finishes first
            tasks.append(running)
            if running["kind"] == "pickup":                  # ... and the load it is loading is then aboard
                aboard.append(running["load"])
        if stops is None:
            order = [("deliver", lid) for lid in aboard]
            for lid in dict.fromkeys(load_ids):
                if lid not in aboard:
                    order += [("pickup", lid), ("deliver", lid)]
        else:
            listed, order = {}, []
            for lid in stops:
                n = listed.get(lid, 0)
                if lid in aboard and n:
                    raise ActionError(f"{lid} is aboard: list it once in stops, for its delivery")
                if n >= 2:
                    raise ActionError(f"{lid} is in stops more than twice")
                order.append(("deliver" if lid in aboard or n else "pickup", lid))
                listed[lid] = n + 1
            once = sorted(lid for lid, n in listed.items() if lid not in aboard and n != 2)
            if once:
                raise ActionError(f"a new load goes in stops twice (its pickup, then its delivery): {', '.join(once)}")
            order = [("deliver", lid) for lid in aboard if lid not in listed] + order
        for kind, lid in order:
            if kind != "pickup":
                continue
            ld = self.loads.get(lid)
            if ld is None:
                raise ActionError(f"no load {lid}")
            o = ld.offer
            if not self.fits(tr.spec.vclass, o):
                raise ActionError(f"{lid} ({o.pallets} pallets, {o.weight_lb:,} lb, {o.equipment}) doesn't fit {tr.spec.id}, "
                                  f"a {VEHICLES[tr.spec.vclass].label}")
            if check_ownership:
                if not (ld.owner == seat and ld.status == "owned") and seat not in ld.claims:
                    raise ActionError(f"{lid} is not yours: accept it first (it is {ld.status})")
            elif not (ld.status == "open" or (ld.status == "owned" and ld.owner == seat)) or o.posted > self.tick:
                raise ActionError(f"{lid} is not on the board (it is {ld.status})")
        riding = list(aboard)
        for kind, lid in order:
            if kind == "pickup":
                riding.append(lid)
                problem = self._ride_together(tr, riding)
                if problem:
                    raise ActionError(f"after picking up {lid}: {problem}")
            else:
                riding.remove(lid)
        tasks += [{"kind": kind, "city": self.loads[lid].offer.orig if kind == "pickup" else self.loads[lid].offer.dest, "load": lid}
                  for kind, lid in order]
        return tasks

    def _simulate_plan(self, tr: Truck, tasks: list, horizon_ticks: int = 96 * 7, dwell_hours: float | None = None) -> dict:
        """Run the driver model on a copy of the truck. dwell_hours replaces the average stop time (the reference
        planner checks its plans against slow stops); agents' plan_trip always uses the average."""
        sim = fork_truck(tr, self.tick)
        sim.tasks = [dict(t) for t in tasks]
        sim.dispatched_at = self.tick
        if not (tr.tasks and tr.dwell_left is not None and tasks and tasks[0] == tr.tasks[0]):
            sim.dwell_left, sim.route = None, []
        world = ExpectedWorld(self, self.tick)
        if dwell_hours is not None:
            world.dwell_override = math.ceil(dwell_hours * 4)
        unit = self.costs_by_class[tr.spec.vclass]
        lodging = 0.0
        stops, rests, problems, closed = [], [], [], []
        miles_empty = miles_loaded = 0.0
        on_duty = 0
        fuel = 0.0
        t = self.tick
        end = self.tick + horizon_ticks
        while sim.tasks and t < end:
            status, moved, events = self.executor.step(sim, t, world)
            on_duty += status in ("ON", "D")
            if unit["reefer_gph"] and self._reefer_running(sim):
                fuel += unit["reefer_gph"] * TICK_HOURS * self.scenario.diesel[self.pack.cities[sim.pos.city or sim.pos.a].padd]
            for a, b, miles in moved:
                fuel += miles / unit["mpg"] * self.scenario.diesel[self.pack.cities[a].padd]
                if sim.onboard:
                    miles_loaded += miles
                else:
                    miles_empty += miles
            for ev in events:
                k = ev["kind"]
                if k in ("pickup_start", "deliver_start"):
                    stops.append({"stop": "pickup" if k == "pickup_start" else "delivery", "load": ev["load"],
                                  "city": world.offer(ev["load"]).orig if k == "pickup_start" else world.offer(ev["load"]).dest,
                                  "starts": clock(t), "late_h": hours(ev.get("late", 0)) or None})
                elif k == "rest":
                    rests.append({"from": clock(t), "until": clock(ev["until"]), "rule": ev["reason"]})
                    if ev["reason"] != "break" and not VEHICLES[sim.spec.vclass].sleeper and sim.pos.city != sim.spec.hub:
                        lodging += self.c["lodging_per_night_usd"]
                elif k == "late_for_pickup":
                    o = world.offer(ev["load"])
                    problems.append(f"{ev['load']}: arrives {clock(t)}, after its pickup window closes at {clock(o.pickup_end)}")
                elif k == "reached":
                    stops.append({"stop": "arrive", "city": ev["city"], "starts": clock(t)})
                elif k == "held":
                    closed.append({"waits_for": ev["road"], "at": ev["where"], "from": clock(t),
                                   "until_about": clock(ev["reopens_about"])})
                elif k in ("detour", "turned_back"):
                    closed.append({"detour": " - ".join(ev["via"]), "from": clock(t)})
            t += 1
        if sim.tasks:
            problems.append(f"the plan isn't finished by {clock(end)}")
        loads = [x["load"] for x in tasks if x["kind"] == "deliver"]
        kept = [lid for lid in loads if not any(f"{lid}:" in pr for pr in problems)]
        revenue = sum(self.loads[lid].offer.linehaul_usd + self.loads[lid].offer.fsc_per_mile * self.loads[lid].offer.miles for lid in kept)
        miles = miles_empty + miles_loaded
        late_penalty = sum(min(st["late_h"] * self.p.late_penalty_per_hour_usd, 0.5 * self.loads[st["load"]].offer.linehaul_usd)
                           for st in stops if st["stop"] == "delivery" and st.get("late_h"))
        owner_operator = None
        if tr.spec.owner == "owner_operator":            # the carrier pays the owner's settlement; the owner, the truck
            cost = sum(self.oo_pay(self.loads[lid].offer) for lid in kept) + late_penalty
            owner_operator = self._owner_operator_answer(tr, kept, miles, fuel, t, sim)
        else:
            if not tr.crew:
                problems.append(f"{tr.spec.id} has no driver: assign_drivers first")
            share = self.c["team_pay_factor"] / len(tr.crew) if len(tr.crew) > 1 else 1.0
            pay = sum(miles * self.c["company_driver_pay_per_mile_usd"] * share for d in tr.crew if d.kind == "company")
            hourly = {"local": self.c["light_driver_wage_hourly_usd"] * (1 + self.c["driver_benefits_load"]),
                      "temp": self.c["temp_driver_hourly_usd"]}.get(tr.crew[tr.at_wheel].kind if tr.crew else "", 0.0)
            cost = fuel + miles * unit["wear_mile"] + pay + on_duty * hourly * TICK_HOURS + lodging + late_penalty
        return {"feasible": not problems, "problems": problems, "stops": stops, "hos_rests": rests, "road_closures": closed,
                **({"owner_operator": owner_operator} if owner_operator else {}),
                "late_deliveries": [st["load"] for st in stops if st["stop"] == "delivery" and st.get("late_h")],
                "est_late_penalty_usd": round(late_penalty, 2),
                "finishes": clock(t) if not sim.tasks else None, "finish_tick": t if not sim.tasks else None,
                "deadhead_miles": round(miles_empty, 1),
                "loaded_miles": round(miles_loaded, 1), "on_duty_hours": hours(on_duty), "est_revenue_usd": round(revenue, 2),
                "est_cost_usd": round(cost, 2), "est_profit_usd": round(revenue - cost, 2),
                "assumes": "average dwell, no breakdowns, only the weather already under way"}

    def _owner_operator_answer(self, tr: Truck, loads: list, miles: float, fuel: float, finish: int, sim: Truck) -> dict:
        """Whether a unit's owner-operator takes the trip, by a fixed rule (DESIGN.md "v1.1"): what they clear per
        mile over all its miles (their settlement less fuel and their own costs per mile) must reach their floor, and
        it must get them home by their home-time (Saturday noon), unless it ends at home."""
        owner = tr.crew[0] if tr.crew else None
        pay = sum(self.oo_pay(self.loads[lid].offer) for lid in loads)
        net = (pay - fuel - miles * self.c["owner_operator_cost_per_mile_usd"]) / miles if miles else 0.0
        floor = self.c["owner_operator_net_floor_per_mile_usd"]
        end_city = sim.pos.city or sim.pos.b
        home_by = ticks(self.p.owner_operator_home_by_hours)
        home_at = finish + ticks(self.pack.miles(end_city, tr.spec.hub) / 50.0) if end_city != tr.spec.hub else finish
        why = []
        if loads and net < floor:
            why.append(f"pays ${net:.2f}/mi after their fuel and costs; they take ${floor:.2f}/mi or more")
        if home_at > home_by:
            why.append(f"wouldn't get them home to {self.pack.cities[tr.spec.hub].name} by {clock(home_by)}")
        return {"owner": owner.name if owner else None, "accepts": not why, "net_per_mile_usd": round(net, 2),
                "their_settlement_usd": round(pay, 2), "why_not": "; ".join(why) or None}

    def plan_trip(self, seat: str, truck_id: str, load_ids: list[str], reposition_to: str | None = None,
                  stops: list[str] | None = None) -> dict:
        """What the truck would do with these loads (any on the board, or yours), in order or in `stops` order (see
        _tasks_for), without changing anything."""
        self._require_open_turn(seat)
        tr = self._truck(seat, truck_id)
        tasks = self._tasks_for(tr, list(load_ids or []), seat, check_ownership=False, stops=stops)
        if reposition_to:
            if reposition_to not in self.pack.cities:
                raise ActionError(f"unknown city {reposition_to}")
            tasks.append({"kind": "goto", "city": reposition_to})
        if tr.broken:
            raise ActionError(f"{truck_id} is broken down; repair it first (handle_breakdown)")
        return self._simulate_plan(tr, tasks)

    def plan_trip_with(self, seat: str, truck_id: str, load_ids: list[str], dwell_hours: float, stops: list | None = None) -> dict:
        """plan_trip with every stop taking `dwell_hours`: for the reference planner, not an agent tool."""
        self._require_open_turn(seat)
        tr = self._truck(seat, truck_id)
        if tr.broken:
            raise ActionError(f"{truck_id} is broken down; repair it first (handle_breakdown)")
        return self._simulate_plan(tr, self._tasks_for(tr, list(load_ids), seat, check_ownership=False, stops=stops),
                                   dwell_hours=dwell_hours)

    # ---- tools: acting -----------------------------------------------------------------------------------------
    def accept_load(self, seat: str, load_id: str) -> dict:
        self._require_open_turn(seat)
        ld = self._visible_offer(load_id)
        if ld.status != "open":
            raise ActionError(f"{load_id} is no longer on the board (it is {ld.status})")
        if not any(self.fits(self.trucks[t].spec.vclass, ld.offer) for t in self.seat_trucks(seat)):
            raise ActionError(f"{load_id} ({ld.offer.pallets} pallets, {ld.offer.weight_lb:,} lb, {ld.offer.equipment}) fits none of seat {seat}'s units")
        ld.claims.add(seat)
        return {"claimed": load_id, "note": "claims are settled when every seat has ended the turn; if another seat "
                "claims it too, the seat with a truck closer to the pickup wins"}

    def release_load(self, seat: str, load_id: str) -> dict:
        self._require_open_turn(seat)
        ld = self.loads.get(load_id)
        if ld is None:
            raise ActionError(f"no load {load_id}")
        if seat in ld.claims:
            ld.claims.discard(seat)
            ld.pending_trucks.pop(seat, None)
            for tr in self.trucks.values():
                if tr.spec.seat == seat:
                    self._remove_load_tasks(tr, load_id)
            return {"released": load_id, "penalty_usd": 0.0, "note": "your claim was withdrawn before it was settled"}
        if ld.owner != seat or ld.status != "owned":
            raise ActionError(f"{load_id} is not yours to release (it is {ld.status})")
        if self._loading_now(ld):
            raise ActionError(f"{load_id} is being loaded; it can't be released now")
        ld.status = "released"
        self._drop_tasks(ld)
        self.book(seat, "release_penalty", self.p.release_penalty_usd)
        self.emit("released", seat=seat, load=load_id, penalty=self.p.release_penalty_usd)
        return {"released": load_id, "penalty_usd": self.p.release_penalty_usd}

    def dispatch(self, seat: str, assignments: list[dict]) -> dict:
        """Give trucks their next loads. Each assignment is {"truck": id, "load_ids": [...]} (one load after another)
        or {"truck": id, "stops": [...]} (see _tasks_for), and replaces that truck's plan; a stop under way finishes."""
        self._require_open_turn(seat)
        results = []
        for a in assignments:
            tid, lids, stops = a.get("truck"), list(a.get("load_ids") or []), a.get("stops")
            try:
                tr = self._truck(seat, tid)
                tasks = self._tasks_for(tr, lids, seat, check_ownership=True, stops=list(stops) if stops is not None else None)
                plan = self._simulate_plan(tr, tasks) if not tr.broken else {"feasible": True, "problems": [], "note": "runs once the truck is repaired"}
                if not plan["feasible"]:
                    raise ActionError("; ".join(plan["problems"]))
                answer = plan.get("owner_operator")
                if answer and not answer["accepts"]:
                    self.emit("declined", seat=seat, truck=tid, owner=answer["owner"], why=answer["why_not"],
                              loads=[t["load"] for t in tasks if t["kind"] == "pickup"])
                    raise ActionError(f"{tid}'s owner-operator, {answer['owner']}, turns it down: {answer['why_not']}")
                self._assign(tr, tasks)
                self.emit("dispatch", seat=seat, truck=tid, loads=[t["load"] for t in tasks if t["kind"] == "pickup"])
                results.append({"truck": tid, "ok": True, "plan": plan})
            except ActionError as e:
                results.append({"truck": tid, "ok": False, "error": str(e)})
        return {"results": results}

    def _assign(self, tr: Truck, tasks: list) -> None:
        keep_running = bool(tr.tasks) and tr.dwell_left is not None and tasks and tasks[0] == tr.tasks[0]
        seat = tr.spec.seat
        planned = {t.get("load") for t in tasks}
        for ld in self.loads.values():                    # loads this truck no longer carries become unassigned
            if ld.truck == tr.spec.id and ld.status == "owned" and ld.offer.id not in planned:
                ld.truck = None
            if ld.pending_trucks.get(seat) == tr.spec.id and ld.offer.id not in planned:
                del ld.pending_trucks[seat]
        for t in tasks:
            if t["kind"] != "pickup":
                continue
            ld = self.loads[t["load"]]
            if ld.status == "owned":                      # it's this seat's: it may move between its trucks
                prev, ld.truck = ld.truck, tr.spec.id
            else:                                         # a claim not yet settled: only this seat's own dispatch
                prev = ld.pending_trucks.get(seat)
                ld.pending_trucks[seat] = tr.spec.id
            if prev and prev != tr.spec.id:
                self._remove_load_tasks(self.trucks[prev], ld.offer.id)
        tr.tasks = [dict(t) for t in tasks]
        tr.dispatched_at = self.tick                      # what's known now isn't "unforeseen" for this trip
        if not keep_running:
            tr.dwell_left, tr.route = None, []

    def reposition(self, seat: str, truck_id: str, city: str) -> dict:
        self._require_open_turn(seat)
        tr = self._truck(seat, truck_id)
        if city not in self.pack.cities:
            raise ActionError(f"unknown city {city}; cities are {', '.join(sorted(self.pack.cities))}")
        if tr.spec.owner == "owner_operator" and city != tr.spec.hub:
            raise ActionError(f"{truck_id}'s owner-operator doesn't run empty for nothing, except home to {self.pack.cities[tr.spec.hub].name}")
        if not tr.crew:
            raise ActionError(f"{truck_id} has no driver: assign_drivers first")
        tasks = self._tasks_for(tr, [], seat, check_ownership=True) + [{"kind": "goto", "city": city}]
        self._assign(tr, tasks)
        self.emit("reposition", seat=seat, truck=truck_id, city=city)
        return {"truck": truck_id, "plan": self._simulate_plan(tr, tasks) if not tr.broken else None}

    def schedule_maintenance(self, seat: str, truck_id: str, city: str | None = None) -> dict:
        self._require_open_turn(seat)
        tr = self._truck(seat, truck_id)
        if tr.spec.owner == "owner_operator":
            raise ActionError(f"{truck_id} is an owner-operator's: they maintain their own truck")
        city = city or tr.spec.hub
        if city not in self.pack.cities or not self.pack.cities[city].freight_node:
            raise ActionError(f"{city} has no shop; shops are in {', '.join(sorted(c for c, x in self.pack.cities.items() if x.freight_node))}")
        tasks = self._tasks_for(tr, [], seat, check_ownership=True) + [{"kind": "maintain", "city": city}]
        self._assign(tr, tasks)
        usd = round(self.p.maintenance_usd * VEHICLES[tr.spec.vclass].repair_cost_factor, 2)
        self.book(seat, "maintenance", usd)
        self.emit("maintenance_booked", seat=seat, truck=truck_id, city=city, usd=usd)
        return {"truck": truck_id, "city": city, "usd": usd, "hours": self.p.maintenance_hours}

    def handle_breakdown(self, seat: str, truck_id: str, option: str) -> dict:
        self._require_open_turn(seat)
        tr = self._truck(seat, truck_id)
        if not tr.broken or tr.broken["option"] is not None:
            raise ActionError(f"{truck_id} has no breakdown waiting for a decision")
        if tr.spec.owner == "owner_operator":
            raise ActionError(f"{truck_id} is an owner-operator's: they decide and pay for its repairs")
        options = self.repair_options(tr)
        if option not in options:
            raise ActionError(f"option must be one of {sorted(options)} for a {tr.broken['component']} failure")
        self._order_repair(tr, option, options, book=True)
        return {"truck": truck_id, "option": option, **options[option]}

    def _order_repair(self, tr: Truck, option: str, options: dict, book: bool) -> None:
        seat, draw = tr.spec.seat, tr.components[tr.broken["component"]].draw
        if option == "roadside":
            tr.broken.update(option="roadside", left=draw["roadside"]["ticks"])
            if book:
                self.book(seat, "repairs", round(draw["roadside"]["usd"] * VEHICLES[tr.spec.vclass].repair_cost_factor, 2))
        else:
            shop = options["tow"]["shop"]
            tr.pos, tr.route, _ = self.executor.route_from(tr.pos, shop)
            tr.broken.update(option="tow", phase="tow", shop=shop, shop_ticks=draw["shop"]["ticks"])
            if book:
                self.book(seat, "tow", self.p.tow_base_usd + self.p.tow_per_mile_usd * options["tow"]["tow_miles"])
                self.book(seat, "repairs", round(draw["shop"]["usd"] * VEHICLES[tr.spec.vclass].repair_cost_factor, 2))
        self.emit("repair_ordered", seat=seat, truck=tr.spec.id, option=option, usd=options[option]["usd"],
                  paid_by="the carrier" if book else "the owner-operator")

    # ---- for the verifier and the audit -----------------------------------------------------------------------
    def event_log_jsonl(self) -> str:
        """The event log, one JSON object per line: what the viewer replays and the audit reads."""
        import json
        return "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in self.log)

    def _snapshot(self, tr: Truck) -> list:
        """[city, a, b, into, state, duty, crew] for the viewer. state is broken, held (by a closed road), loaded, empty (driving without a load),
        detention (a stop past its free time), stopped (a stop, or idle) or resting (a required rest or off duty)."""
        duty = tr.clock.log[-1][2] if tr.crew and tr.clock.log else "OFF"
        if tr.broken:
            state = "broken"
        elif tr.held_by is not None and duty != "D":
            state = "held"
        elif duty == "D":
            state = "loaded" if tr.onboard else "empty"
        elif tr.dwell_left is not None and tr.stop_started is not None and tr.tasks and tr.tasks[0]["kind"] in ("pickup", "deliver") \
                and hours(self.tick - tr.stop_started) > self.c["detention_free_hours"]:
            state = "detention"
        elif self.tick < tr.rest_until or (duty == "OFF" and tr.tasks):
            state = "resting"
        else:
            state = "stopped"
        return [tr.pos.city, tr.pos.a, tr.pos.b, round(tr.pos.into, 1), state, duty, [d.id for d in tr.crew]]

    def duty_logs(self) -> dict:
        """Each driver's duty log (DriverClock.log)."""
        return {did: [list(x) for x in d.clock.log] for did, d in sorted(self.drivers.items())}

    # ---- tools: drivers (M15) -------------------------------------------------------------------------------
    def _driver_line(self, d: Driver) -> dict:
        kind = {"company": "company driver, paid by the mile", "local": "local driver (no CDL), hourly",
                "owner_operator": "owner-operator, paid a share of each load", "temp": "temp driver from an agency, hourly"}[d.kind]
        if d.truck:
            tr = self.trucks[d.truck]
            where = f"on {d.truck} ({tr.pos.text(self.pack)})"
        elif self.tick < d.available_from:
            where = f"arrives at {self.pack.cities[d.home].name} {clock(d.available_from)}"
        else:
            where = f"off duty at home, {self.pack.cities[d.home].name}"
        out = {"driver": d.id, "name": d.name, "kind": kind, "where": where, "hours": d.clock.summary(self.tick)}
        if d.kind == "temp":
            out["hired_until"] = clock(d.available_until)
        return out

    def list_drivers(self, seat: str) -> dict:
        if seat not in self.seats:
            raise ActionError(f"unknown seat {seat}")
        return {"clock": clock(self.tick), "drivers": [self._driver_line(d) for d in sorted(self.drivers.values(), key=lambda d: d.id)
                                                        if d.seat == seat and self.tick < d.available_until + 96 * 7]}

    def assign_drivers(self, seat: str, truck_id: str, driver_ids: list[str]) -> dict:
        """Put a crew on a unit at its hub (slip-seating happens at the terminal): one driver, or two on a tractor (a
        team). Each must be at home there, or on a unit that is at the hub too (who then leaves it); a tractor needs
        CDL drivers. The unit's current crew who aren't kept go home. Owner-operators drive only their own truck."""
        self._require_open_turn(seat)
        tr = self._truck(seat, truck_id)
        if tr.spec.owner == "owner_operator":
            raise ActionError(f"{truck_id} is an owner-operator's truck: only its owner drives it")
        if tr.pos.city != tr.spec.hub:
            raise ActionError(f"crews change at the hub; {truck_id} is at {tr.pos.text(self.pack)}")
        if tr.dwell_left is not None or tr.broken:
            raise ActionError(f"{truck_id} is busy (a stop under way, or in repair)")
        ids = list(dict.fromkeys(driver_ids))
        vc = VEHICLES[tr.spec.vclass]
        if not 1 <= len(ids) <= (2 if vc.sleeper else 1):
            raise ActionError(f"a {vc.label} takes 1 driver" + (" or 2 (a team)" if vc.sleeper else ""))
        crew = []
        for did in ids:
            d = self.drivers.get(did)
            if d is None or d.seat != seat:
                raise ActionError(f"{did} is not one of seat {seat}'s drivers")
            if d.kind == "owner_operator":
                raise ActionError(f"{did} ({d.name}) is an owner-operator: they drive only their own truck")
            if not (d.available_from <= self.tick < d.available_until):
                raise ActionError(f"{did} ({d.name}) isn't available now ({self._driver_line(d)['where']})")
            if not d.cdl and vc.name.startswith("tractor"):
                raise ActionError(f"{did} ({d.name}) has no CDL: a tractor needs one")
            if d.truck and d.truck != truck_id:
                other = self.trucks[d.truck]
                if other.pos.city != tr.spec.hub or other.dwell_left is not None:
                    raise ActionError(f"{did} ({d.name}) is on {d.truck}, away from the hub or busy")
            crew.append(d)
        for d in [x for x in tr.crew if x not in crew]:     # the current crew not kept go home
            self._crew_off(d)
        for d in crew:
            if d.truck and d.truck != truck_id:
                self._crew_off(d)
            if d.truck != truck_id:
                d.truck = truck_id
                tr.crew.append(d)
        tr.crew.sort(key=lambda d: ids.index(d.id))
        tr.at_wheel, tr.rest_until = 0, self.tick
        self.emit("crew_change", seat=seat, truck=truck_id, crew=[d.id for d in tr.crew])
        return {"truck": truck_id, "crew": [self._driver_line(d) for d in tr.crew]}

    def hire_temp_driver(self, seat: str, days: int = 1, arrive_in_hours: float | None = None) -> dict:
        """Call a CDL temp from an agency: at the hub after the notice, for whole days, at the agency's hourly rate
        with a daily minimum (costs.json). Up to params.temp_max_per_seat at a time."""
        self._require_open_turn(seat)
        notice = self.p.temp_notice_hours if arrive_in_hours is None else arrive_in_hours
        if notice < self.p.temp_notice_hours:
            raise ActionError(f"an agency needs {self.p.temp_notice_hours:.0f} h notice")
        if not 1 <= int(days) <= 7:
            raise ActionError("days is 1 to 7")
        temps = [d for d in self.drivers.values() if d.seat == seat and d.kind == "temp" and (d.truck or self.tick < d.available_until)]
        if len(temps) >= self.p.temp_max_per_seat:
            raise ActionError(f"seat {seat} has {len(temps)} temps already (at most {self.p.temp_max_per_seat})")
        n = 1 + sum(1 for d in self.drivers.values() if d.seat == seat and d.kind == "temp")
        start = self.tick + ticks(notice)
        d = Driver(f"{seat}-T{n}", f"Temp {n}", seat, "temp", self.seats[seat], DriverClock(self.rules), None, start, start + 96 * int(days))
        for t in range(self.tick):                    # the log starts with the week, off duty
            d.clock.record(t, "OFF")
        self.drivers[d.id] = d
        self.emit("temp_hired", seat=seat, driver=d.id, arrives=start, until=d.available_until)
        return {"driver": d.id, "arrives": clock(start), "until": clock(d.available_until),
                "rate": f"${self.c['temp_driver_hourly_usd']:.0f}/h on duty, at least {self.c['temp_driver_minimum_hours_per_day']:.0f} h a day"}

    def result(self) -> dict:
        return {"done": self.done, "turn": self.turn, "clock": clock(self.tick), "monday": self.scenario.monday,
                "seed": self.scenario.seed,
                "seats": {s: {"hub": h, "model": s in self.model_seats, "profit_usd": round(self.profit(s), 2),
                              "ledger_usd": {k: round(v, 2) for k, v in self.ledger[s].items()},
                              "delivered": sum(1 for ld in self.loads.values() if ld.owner == s and ld.status == "delivered")}
                          for s, h in sorted(self.seats.items())}}
