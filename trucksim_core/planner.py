"""The rolling re-planner (PLAN.md M4): the reference operator a good model should match, and the rival carriers'
policy. Each turn it re-plans the whole seat on what is announced, with the same tools an agent has:

1. for each unit, candidate chains of up to `max_chain` loads it can carry (board loads and the seat's own unpicked
   loads), one after another or, for two partial or small loads that fit together, both aboard at once; each
   simulated by plan_trip with the real driver model under hours of service;
2. a chain's value is its estimated profit minus `time_value_per_hour` for every hour it keeps the truck busy, so a
   long, thin chain doesn't beat two good short ones;
3. CP-SAT picks at most one chain per truck, no load twice, covering every load the seat already owns or paying to
   release it (cheaper than a missed pickup);
4. idle trucks with nothing worth taking move toward the origin with the most recent offers, and a truck with a fault
   warning sitting idle at a shop gets maintenance;
5. before all that, slip-seating at the hub (a tired or missing driver swapped for a rested one at home), and no plan
   an owner-operator would turn down.

Everything is ordered by id and solved with one CP-SAT worker and a fixed seed, so a turn's plan is deterministic."""

import hashlib
from dataclasses import dataclass

from ortools.sat.python import cp_model

from .game import ActionError, Game
from .policies import acceptable, handle_breakdowns


@dataclass(frozen=True)
class RollingConfig:
    name: str = "rolling-a"
    max_chain: int = 2
    top_singles: int = 8          # per truck, the best single loads kept as chain starts
    pair_pool: int = 6            # pairs are built from the best this many singles
    time_value_per_hour: float = 10.0
    keep_bonus: float = 50.0          # a chain that starts with the truck's current next pickup: plans don't churn
    release_cost: float = 2_000.0     # in the objective: loads already won are covered unless no truck can make them
    slow_dwell_hours: float = 4.5     # a chain is also run with stops this slow ...
    slow_probability: float = 0.25    # ... and charged this share of the late penalty it would take then
    reposition: bool = True
    reposition_max_miles: float = 250.0
    maintain_on_warning: bool = True
    swap_below_hours: float = 4.0     # slip-seating: a driver with less driving left than this is swapped at the hub


CONFIGS = {   # chosen in M4 by sweeping against greedy over fixed weeks (PLAN.md); repositioning on a hunch lost money
    "rolling-a": RollingConfig(reposition=False),
    "rolling-b": RollingConfig(name="rolling-b", time_value_per_hour=20.0, slow_probability=0.5, reposition=False),
}


def _plan(game: Game, seat: str, tid: str, stops: tuple, cfg: RollingConfig) -> dict | None:
    """The chain's plan (stops: see Game._tasks_for) with average stops if it is feasible, with its estimated profit
    reduced by the late (or missed-pickup) risk it would run if stops were slow; else None."""
    plan = game.plan_trip(seat, tid, [], stops=list(stops))
    if not acceptable(plan):                     # infeasible, or its owner-operator would turn it down
        return None
    slow = game.plan_trip_with(seat, tid, [], cfg.slow_dwell_hours, stops=list(stops))
    risk = slow["est_late_penalty_usd"] - plan["est_late_penalty_usd"]
    if not slow["feasible"]:
        risk += game.p.missed_pickup_penalty_usd * len(slow["problems"])
    return {**plan, "est_profit_usd": plan["est_profit_usd"] - cfg.slow_probability * risk}


def _value(plan: dict, now: int, cfg: RollingConfig) -> float:
    """Estimated profit, less a charge per on-duty hour (driving and stops; not waiting or resting) for the truck
    time the chain uses up."""
    return plan["est_profit_usd"] - cfg.time_value_per_hour * plan["on_duty_hours"]


TIE_SCALE = 2 ** 28                   # above the largest tie-break sum (20-bit weights, a chain per truck, 10 trucks)


def _tie_weight(key: tuple) -> int:
    return int(hashlib.sha256(repr(key).encode()).hexdigest()[:5], 16)


def current_stops(tr) -> tuple:
    """A truck's plan as stops (a new load's id at its pickup and at its delivery, a load aboard at its delivery),
    without a pickup under way: plan_trip keeps that one first by itself."""
    tasks = tr.tasks[1:] if tr.tasks and tr.dwell_left is not None and tr.tasks[0]["kind"] == "pickup" else tr.tasks
    return tuple(t["load"] for t in tasks if t.get("load"))


def _new_loads(stops: tuple) -> list:
    """The loads a chain picks up (listed twice), in order."""
    return [lid for lid in dict.fromkeys(stops) if stops.count(lid) == 2]


def _can_share(game: Game, tid: str, a: str, b: str) -> bool:
    """Two partial or small loads that fit the truck together (pallets, weight, one kind of freight)."""
    oa, ob = game.loads[a].offer, game.loads[b].offer
    return oa.size != "full" and ob.size != "full" and game._ride_together(game.trucks[tid], [a, b]) is None


def slip_seat(game: Game, seat: str, cfg: RollingConfig) -> None:
    """At the hub, a company unit with no driver, or whose driver has less than `swap_below_hours` of driving left,
    goes out with a rested driver who is at home there (CDL for a tractor); the one coming off goes home."""
    home = sorted((d for d in game.drivers.values() if d.seat == seat and d.truck is None and d.kind != "owner_operator"
                   and d.available_from <= game.tick < d.available_until), key=lambda d: d.id)
    for tid in game.seat_trucks(seat):
        tr = game.trucks[tid]
        if tr.spec.owner != "company" or tr.pos.city != tr.spec.hub or tr.dwell_left is not None or tr.broken or len(tr.crew) > 1:
            continue
        tired = not tr.crew or tr.clock.summary(game.tick)["drive_left_h"] < cfg.swap_below_hours
        if not tired:
            continue
        needs_cdl = tr.spec.vclass.startswith("tractor")
        fresh = next((d for d in home if (d.cdl or not needs_cdl) and d.clock.drive_blocker(game.tick) is None
                      and d.clock.summary(game.tick)["drive_left_h"] >= 8.0), None)
        if fresh:
            game.assign_drivers(seat, tid, [fresh.id])
            home.remove(fresh)


def rolling(game: Game, seat: str, cfg: RollingConfig = CONFIGS["rolling-a"]) -> None:
    handle_breakdowns(game, seat)
    slip_seat(game, seat, cfg)
    now = game.tick
    trucks = [t for t in game.seat_trucks(seat) if not game.trucks[t].broken and game.trucks[t].crew]
    # A load already won and given to a truck stays on that truck, in its order: re-planning only adds after it, so
    # trucks don't turn around and waste the miles they have driven toward it.
    committed = {tid: current_stops(game.trucks[tid]) for tid in trucks}
    locked = {lid for stops in committed.values() for lid in _new_loads(stops)}
    owned_free = sorted(lid for lid, ld in game.loads.items()
                        if ld.owner == seat and ld.status == "owned" and lid not in locked and not game._loading_now(ld))
    board = [x["id"] for x in game.list_loads(seat)["loads"]]
    free = sorted(set(owned_free) | set(board))

    chains: dict[tuple[str, tuple], float] = {}       # (truck, stops) -> value
    for tid in trucks:
        tr = game.trucks[tid]
        base = committed[tid]
        base_plan = _plan(game, seat, tid, base, cfg) if _new_loads(base) else None
        if _new_loads(base) and base_plan is None:  # its own plan no longer fits: free those loads too
            free = sorted(set(free) | set(_new_loads(base)))
            base = tuple(lid for lid in base if lid in tr.onboard)
        if _new_loads(base):
            chains[(tid, base)] = _value(base_plan, now, cfg) + cfg.keep_bonus
        if len(_new_loads(base)) >= cfg.max_chain:
            continue
        singles = []
        for lid in free:
            if not game.fits(tr.spec.vclass, game.loads[lid].offer):
                continue
            try:
                plan = _plan(game, seat, tid, base + (lid, lid), cfg)
            except ActionError:
                continue
            if plan:
                singles.append((_value(plan, now, cfg), lid))
        singles.sort(key=lambda v: (-v[0], v[1]))
        for v, lid in singles[: cfg.top_singles]:
            chains[(tid, base + (lid, lid))] = v
        for v, lid in singles:                       # loads the seat already owns stay options even if they rank low
            if lid in owned_free:
                chains[(tid, base + (lid, lid))] = v
        if len(_new_loads(base)) + 2 <= cfg.max_chain:
            pool = [lid for _, lid in singles[: cfg.pair_pool]]
            for a in pool:
                for b in pool:
                    if a == b:
                        continue
                    ways = [base + (a, a, b, b)]                  # one after the other ...
                    if a < b and _can_share(game, tid, a, b):     # ... or both aboard at once, either drop first
                        ways += [base + (a, b, a, b), base + (a, b, b, a)]
                    for stops in ways:
                        try:
                            plan = _plan(game, seat, tid, stops, cfg)
                        except ActionError:
                            continue
                        if plan:
                            chains[(tid, stops)] = _value(plan, now, cfg)

    # choose: one chain per truck (a truck with commitments keeps them), each load at most once, owned loads covered
    model = cp_model.CpModel()
    keys = sorted(chains)
    x = {k: model.NewBoolVar(f"x{i}") for i, k in enumerate(keys)}
    for tid in trucks:
        mine = [v for (t, _), v in x.items() if t == tid]
        base = committed[tid]
        if _new_loads(base) and any(k[0] == tid and k[1][: len(base)] == base for k in keys):
            model.Add(sum(mine) == 1)
        elif mine:
            model.Add(sum(mine) <= 1)
    covered = {}
    for lid in sorted(set(free) | locked):
        uses = [v for (_, stops), v in x.items() if lid in _new_loads(stops)]
        if uses:
            model.Add(sum(uses) <= 1)
        if lid in owned_free or lid in locked:
            covered[lid] = sum(uses) if uses else 0
    # The value in cents decides; a tie between plans of equal value is broken by a fixed weight per chain (from a
    # hash of its name, under 2^20, at most one chain per truck), so the optimum is unique: CP-SAT breaks ties
    # differently on different platforms, and a reference must be the same everywhere.
    value = sum(int(round(chains[k] * 100)) * v for k, v in x.items()) - sum(int(cfg.release_cost * 100) * (1 - c) for c in covered.values())
    model.Maximize(value * TIE_SCALE + sum(_tie_weight(k) * v for k, v in x.items()))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"seat {seat}: CP-SAT found no plan ({solver.StatusName(status)})")
    chosen = {k[0]: k[1] for k, v in x.items() if solver.Value(v)}

    # act: claim new loads, release owned ones nothing can carry, dispatch only trucks whose plan changed
    for tid in sorted(chosen):
        for lid in _new_loads(chosen[tid]):
            if game.loads[lid].status == "open":
                game.accept_load(seat, lid)
    assigned = {lid for stops in chosen.values() for lid in _new_loads(stops)}
    for lid in sorted(set(owned_free) | locked):
        if lid not in assigned and game.loads[lid].status == "owned" and not game._loading_now(game.loads[lid]):
            game.release_load(seat, lid)
    for tid in trucks:
        want = chosen.get(tid, tuple(lid for lid in committed[tid] if lid in game.trucks[tid].onboard))
        if want != current_stops(game.trucks[tid]):
            game.dispatch(seat, [{"truck": tid, "stops": list(want)}])
    for tid in trucks:
        tr = game.trucks[tid]
        if tr.tasks or tr.onboard:
            continue
        if cfg.maintain_on_warning and tr.spec.owner == "company" and any(c.warned for c in tr.components.values()) \
                and tr.pos.city and game.pack.cities[tr.pos.city].freight_node:
            game.schedule_maintenance(seat, tid, tr.pos.city)
            continue
        if cfg.reposition and tr.pos.city and tr.spec.owner == "company":
            counts = _recent_origins(game, tr.spec.vclass)
            here = counts.get(tr.pos.city, 0)
            near = [c for c in counts if c != tr.pos.city and game.pack.miles(tr.pos.city, c) <= cfg.reposition_max_miles]
            target = min(near, key=lambda c: (-counts[c], c)) if near else None
            if target and counts[target] >= 2 * max(1, here):    # clearly busier, and close enough
                game.reposition(seat, tid, target)
    game.end_turn(seat)


def _recent_origins(game: Game, vclass: str) -> dict[str, int]:
    """Offers posted in the last 24 hours that this unit could carry, counted by origin."""
    counts = {}
    for ld in game.loads.values():
        o = ld.offer
        if game.tick - 96 <= o.posted <= game.tick and game.fits(vclass, o):
            counts[o.orig] = counts.get(o.orig, 0) + 1
    return counts


def policy(name: str):
    cfg = CONFIGS[name]
    return lambda game, seat: rolling(game, seat, cfg)
