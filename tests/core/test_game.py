"""The sim core: deterministic, fair to every seat, honest in its books, and strict about what it accepts."""

import copy
import datetime as dt
import json

import pytest

from trucksim_core.audit import audit
from trucksim_core.data import DataPack
from trucksim_core.game import ActionError, Game
from trucksim_core.policies import greedy
from trucksim_core.scenario import build_scenario, failure_draw

PACK = DataPack()
SEATS = [("A", "DAL"), ("B", "HOU"), ("C", "SA")]
WEEKS = ["2021-02-15", "2023-03-06", "2024-06-03", "2025-09-08"]


def scenario(seed=1, monday="2024-06-03"):
    return build_scenario(PACK, seed, dt.date.fromisoformat(monday), SEATS)


def npc_week(seed=1, monday="2024-06-03") -> Game:
    return Game(PACK, scenario(seed, monday), model_seats=set(), npc_policy=greedy)


def lazy(game, seat):
    game.end_turn(seat)


def test_same_seed_same_decisions_same_log():
    assert npc_week(3).event_log_jsonl() == npc_week(3).event_log_jsonl()


def test_every_week_passes_the_audit():
    for monday in WEEKS:
        for seed in (1, 2):
            g = npc_week(seed, monday)
            assert g.done
            result = audit(g)
            assert result["ok"], (monday, seed, result["problems"][:5])


def test_a_model_seat_playing_greedy_gets_the_npc_result():
    """Seats are symmetric: driving seat A through the tool methods, turn by turn, changes nothing."""
    reference = npc_week(2)
    g = Game(PACK, scenario(2), model_seats={"A"}, npc_policy=greedy)
    while not g.done:
        greedy(g, "A")
    strip = lambda r: {**r, "seats": {k: {**v, "model": None} for k, v in r["seats"].items()}}  # noqa: E731
    assert strip(g.result()) == strip(reference.result())
    # the same events; only their order within a turn differs (seat A now acts after B and C)
    as_rows = lambda game: sorted(json.dumps(e, sort_keys=True) for e in game.log)  # noqa: E731
    assert as_rows(g) == as_rows(reference)


def test_breakdowns_happen_at_the_pre_drawn_mileage_whatever_the_policy():
    """Common random numbers: a component's k-th failure is at a mileage fixed by the seed. The policy only decides
    when (or whether) the truck gets there."""
    found = 0
    for monday in WEEKS:
        s = scenario(1, monday)
        g = npc_week(1, monday)
        specs = {t.id: t for t in s.trucks}
        for e in g.log:
            if e["kind"] == "breakdown" and e["failure"] == 0:
                spec = specs[e["truck"]]
                draw = failure_draw(s.key, spec.id, e["component"], 0, spec.service_miles0[e["component"]], s.params)
                assert e["odometer"] == pytest.approx(spec.odometer0 + draw["miles_to_failure"], abs=1e-3)
                found += 1
        idle = Game(PACK, s, model_seats=set(), npc_policy=lazy)
        assert [e for e in idle.log if e["kind"] == "offer_posted"] == [e for e in g.log if e["kind"] == "offer_posted"]
        assert not any(e["kind"] == "breakdown" for e in idle.log)   # trucks that never move never break
    assert found > 0


def advance_until(g: Game, want) -> list:
    """End every seat's turn until list_loads("A") has a load matching `want`."""
    while True:
        found = [x for x in g.list_loads("A")["loads"] if want(x)]
        if found:
            return found
        for seat in sorted(g.seats):
            g.end_turn(seat)


def test_contested_claim_goes_to_the_closer_truck_and_the_loser_drops_it():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    offer = g.loads[advance_until(g, lambda x: x["orig"] == "HOU" and x["equipment"] == "dry")[0]["id"]].offer
    for seat, truck in (("A", "A01"), ("B", "B01")):
        g.accept_load(seat, offer.id)
        assert g.dispatch(seat, [{"truck": truck, "load_ids": [offer.id]}])["results"][0]["ok"]
    for seat in ("A", "B", "C"):
        g.end_turn(seat)
    ld = g.loads[offer.id]
    assert ld.owner == "B" and ld.truck == "B01"                     # B's truck is in Houston
    assert not any(t.get("load") == offer.id for t in g.trucks["A01"].tasks)
    lost = [e for e in g.log if e["kind"] == "claim_lost" and e["load"] == offer.id]
    assert [e["seat"] for e in lost] == ["A"]


def test_plan_trip_changes_nothing():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    advance_until(g, lambda x: True)
    before = copy.deepcopy((g.trucks, g.loads, g.ledger, len(g.log)))
    for ld in g.list_loads("A")["loads"][:5]:
        try:
            g.plan_trip("A", "A05", [ld["id"]])                         # the reefer tractor
        except ActionError:
            pass
    assert copy.deepcopy((g.trucks, g.loads, g.ledger, len(g.log))) == before


def test_bad_calls_are_refused_with_a_reason():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    loads = advance_until(g, lambda x: x["equipment"] == "dry")
    some = next(x["id"] for x in loads if x["equipment"] == "dry")
    res = g.dispatch("A", [{"truck": "A01", "load_ids": [some]}])["results"][0]
    assert not res["ok"] and "accept it first" in res["error"]
    with pytest.raises(ActionError, match="not one of seat A's trucks"):
        g.get_truck("A", "B01")
    with pytest.raises(ActionError, match="no breakdown"):
        g.handle_breakdown("A", "A01", "roadside")
    reefer = next((x["id"] for x in loads if x["equipment"] == "reefer"), None)
    if reefer:
        g.accept_load("A", reefer)
        res = g.dispatch("A", [{"truck": "A01", "load_ids": [reefer]}])["results"][0]
        assert not res["ok"] and "doesn't fit A01" in res["error"]
    g.end_turn("A")
    with pytest.raises(ActionError, match="has ended turn"):
        g.accept_load("A", some)


def test_event_log_is_json_lines():
    lines = npc_week(1).event_log_jsonl().splitlines()
    rows = [json.loads(x) for x in lines]
    assert rows[0]["kind"] == "offer_posted" or rows[0]["t"] == 0
    assert rows[-1]["kind"] == "week_over"


def test_removing_one_load_leaves_another_stop_under_way_alone():
    """Regression: losing a contested claim on one load once reset the truck's loading of a different load."""
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    tr = g.trucks["A01"]
    tr.tasks = [{"kind": "pickup", "city": "DAL", "load": "X"}, {"kind": "deliver", "city": "HOU", "load": "X"},
                {"kind": "pickup", "city": "HOU", "load": "Y"}, {"kind": "deliver", "city": "SA", "load": "Y"}]
    tr.dwell_left, tr.route = 3, ["HOU"]
    Game._remove_load_tasks(tr, "Y")
    assert [t["load"] for t in tr.tasks] == ["X", "X"] and tr.dwell_left == 3
    Game._remove_load_tasks(tr, "X")
    assert tr.tasks == [] and tr.dwell_left is None


class _StormWorld:
    """A world with one storm on one stretch of one road, for the driver model alone."""
    breakdowns = False

    def __init__(self, road, stretch):
        self.road, self.stretch = road, stretch

    def stretches(self, a, b):
        return [self.stretch] if (a, b) == self.road else []

    def closures(self, a, b):
        return []

    def known_closures(self, a, b, tick):
        return []


def test_a_storm_slows_only_its_stretch_of_road():
    from trucksim_core.game import Executor
    from trucksim_core.params import VEHICLES, Params
    ex, vc = Executor(PACK, Params(), "test"), VEHICLES["tractor_dry"]
    edge = PACK.edge("DAL", "HOU")
    free = min(vc.speed_cap_mph, vc.speed_factor * edge.car_mph)
    world = _StormWorld(("DAL", "HOU"), (80.0, 120.0, 0, 10**6, 0.25))
    assert ex.speed_at("DAL", "HOU", 10.0, 5, world, vc) == (free, 70.0, None)       # free road, the storm 70 mi ahead
    assert ex.speed_at("DAL", "HOU", 90.0, 5, world, vc) == (free * 0.25, 30.0, None)  # inside it, to its far edge
    assert ex.speed_at("DAL", "HOU", 130.0, 5, world, vc) == (free, edge.miles - 130.0, None)
    assert ex.speed_at("HOU", "DAL", 90.0, 5, world, vc)[0] == free                  # the other way has its own stretches


def test_a_road_storm_on_the_board_slows_the_road_both_ways_and_nowhere_else():
    s = scenario(2, "2021-02-15")                   # Winter Storm Uri week
    g = Game(PACK, s, model_seats=set(), npc_policy=lazy)
    w = next(w for w in s.weather if w.city is None and w.spans)       # a storm filed for a county between cities
    a, b, m0, m1 = w.spans[0]
    length = PACK.edge(a, b).miles
    assert (m0, m1, w.start, w.end, w.speed_factor) in g.storm_stretches[(a, b)]
    assert (length - m1, length - m0, w.start, w.end, w.speed_factor) in g.storm_stretches[(b, a)]
    assert set(w.spans) == set(PACK.county_spans[w.county])           # every road stretch in that county


# ---- road closures (M13) ----------------------------------------------------------------------------------------
def _closure(a, b, m0, m1, start, end, direction="both", estimate=None):
    from trucksim_core.scenario import Closure
    return Closure(f"T-{a}-{b}-{start}", a, b, m0, m1, direction, start, end, end if estimate is None else estimate,
                   "Ice Storm", "Test County, TX", "modeled", "a test closure")


def _calm_game(closures):
    """A Uri-free week with no storms and only the given closures; every seat a model, so nobody moves unasked."""
    import dataclasses
    s = dataclasses.replace(scenario(1), weather=(), closures=tuple(closures))
    return Game(PACK, s, model_seats={"A", "B", "C"})


def _run(g, ticks):
    for _ in range(ticks):
        g._simulate(1)


def test_a_known_closure_is_driven_around_when_the_detour_is_quicker():
    g = _calm_game([_closure("DAL", "HOU", 100.0, 104.0, 0, 96)])          # I-45 shut for a day
    g.reposition("A", "A01", "HOU")
    _run(g, 40)
    events = [e for e in g.log if e.get("truck") == "A01"]
    detour = next(e for e in events if e["kind"] == "detour")
    assert "HOU" == detour["via"][-1] and detour["via"][0] != "HOU"
    assert not any(e["kind"] == "depart" and {e["a"], e["b"]} == {"DAL", "HOU"} for e in events)   # never on I-45
    assert any(e["kind"] == "reached" and e["city"] == "HOU" for e in events)


def test_a_truck_reaching_a_closure_waits_at_its_start_and_claims_the_exception_once():
    g = _calm_game([_closure("DAL", "HOU", 100.0, 104.0, 4, 16)])            # closes an hour after dispatch, for 3 h
    g.reposition("A", "A01", "HOU")
    _run(g, 30)
    tr, events = g.trucks["A01"], [e for e in g.log if e.get("truck") == "A01"]
    held = next(e for e in events if e["kind"] == "held")
    assert held["closure"] == "T-DAL-HOU-4"
    claims = [e for e in events if e["kind"] == "adverse_conditions"]
    assert len(claims) == 1 and claims[0]["t"] == held["t"] and tr.clock.adverse_claims == [held["t"]]
    log = {t: st for s, e, st in tr.clock.log for t in range(s, e)}
    stopped = [t for t in range(held["t"], 16)]
    assert [log[t] for t in stopped[:4]] == ["ON"] * 4 and all(log[t] == "OFF" for t in stopped[4:])   # then sleeper
    assert log[16] == "D"                                                      # rolls again when it reopens
    from trucksim_core.hos import check_log
    assert check_log(tr.clock.log, g.rules, tr.clock.adverse_claims) == []


def test_a_closure_known_at_dispatch_earns_no_exception():
    g = _calm_game([_closure("DAL", "HOU", 100.0, 104.0, 0, 12, estimate=12)])
    _run(g, 1)
    g.reposition("A", "A01", "HOU")                                             # dispatched with the road already shut
    _run(g, 30)
    assert not [e for e in g.log if e.get("truck") == "A01" and e["kind"] == "adverse_conditions"]


def test_a_one_way_closure_stops_only_its_direction():
    g = _calm_game([_closure("DAL", "HOU", 0.0, 238.0, 0, 200, direction="a>b")])   # southbound I-45 only
    tr = g.trucks["B01"]                                                        # a Houston truck going north
    g.reposition("B", "B01", "DAL")
    _run(g, 30)
    events = [e for e in g.log if e.get("truck") == "B01"]
    assert not any(e["kind"] in ("held", "detour") for e in events)
    assert any(e["kind"] == "depart" and (e["a"], e["b"]) == ("HOU", "DAL") for e in events)


def test_the_checker_refuses_the_two_extra_hours_without_a_claim():
    from trucksim_core.hos import check_log
    log = [[0, 32, "D"], [32, 34, "OFF"], [34, 54, "D"]]                         # 13 h of driving, a 30-minute break
    assert any("past 11 h" in p for p in check_log(log, PACK_RULES()))
    assert check_log(log, PACK_RULES(), adverse_claims=[10]) == []                # claimed before the extra hours


def PACK_RULES():
    from trucksim_core.hos import Rules
    return Rules.from_pack(PACK.hos)


def test_real_closures_come_from_the_noaa_narratives_in_their_week():
    s = scenario(1, "2024-01-15")                                              # the January 2024 freeze
    real = [c for c in s.closures if c.source == "NOAA narrative"]
    assert {(c.a, c.b) for c in real} >= {("JAN", "NOL"), ("BTR", "NOL")}
    assert all(c.end - c.start >= 8 for c in real)                               # at least real_closure_min_hours


# ---- units, load sizes and sharing a trailer (M14) --------------------------------------------------------------
def _claimed(g, seat, lids):
    """Claim loads for a seat and settle the claims (every seat ends the turn)."""
    for lid in lids:
        g.accept_load(seat, lid)
    for s in ("A", "B", "C"):
        if s not in g.ended:
            g.end_turn(s)
    assert all(g.loads[lid].owner == seat for lid in lids), [(lid, g.loads[lid].status, g.loads[lid].owner) for lid in lids]


def _batch(s, origin=None):
    """A shipper's batch in a scenario: partial loads from one origin with pickups within 2 h, that fit one trailer."""
    from collections import defaultdict
    groups = defaultdict(list)
    for o in s.offers:
        if o.size == "partial" and o.equipment == "dry" and (origin is None or o.orig == origin):
            groups[(o.orig, o.posted)].append(o)
    return next(sorted(g, key=lambda o: o.id)[:2] for g in groups.values()
                if len(g) >= 2 and sum(o.pallets for o in g[:2]) <= 26 and abs(g[0].pickup_start - g[1].pickup_start) <= 8)


def test_a_load_goes_only_on_a_unit_it_fits():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    loads = advance_until(g, lambda x: x["size"] == "full" and x["equipment"] == "dry")
    full = next(x for x in loads if x["size"] == "full" and x["equipment"] == "dry")
    assert "van" not in full["fits"] and "tractor_dry" in full["fits"]
    with pytest.raises(ActionError, match="doesn't fit A08"):
        g.plan_trip("A", "A08", [full["id"]])                                   # A08 is the van
    g.plan_trip("A", "A01", [full["id"]])                                       # a dry-van tractor can


def test_two_partials_share_a_trailer_in_stops_order_and_both_pay():
    import dataclasses
    s = dataclasses.replace(scenario(1), weather=(), closures=())
    a, b = _batch(s)
    g = Game(PACK, s, model_seats={"A", "B", "C"})
    while g.tick < a.posted:
        for seat in ("A", "B", "C"):
            g.end_turn(seat)
    _claimed(g, "A", [a.id, b.id])
    res = g.dispatch("A", [{"truck": "A01", "stops": [a.id, b.id, a.id, b.id]}])["results"][0]
    assert res["ok"], res
    while not g.done:
        for seat in ("A", "B", "C"):
            if not g.done and seat not in g.ended:
                g.end_turn(seat)
    picked = [e["t"] for e in g.log if e["kind"] == "picked" and e["load"] in (a.id, b.id)]
    delivered = [e["t"] for e in g.log if e["kind"] == "delivered" and e["load"] in (a.id, b.id)]
    assert len(picked) == 2 and len(delivered) == 2 and max(picked) < min(delivered)   # both aboard at once
    assert g.ledger["A"]["linehaul"] == pytest.approx(a.linehaul_usd + b.linehaul_usd)


def test_stops_are_checked_with_reasons():
    s = scenario(1)
    a, b = _batch(s)
    g = Game(PACK, s, model_seats={"A", "B", "C"})
    while g.tick < a.posted:
        for seat in ("A", "B", "C"):
            g.end_turn(seat)
    with pytest.raises(ActionError, match="goes in stops twice"):
        g.plan_trip("A", "A01", [], stops=[a.id, b.id, a.id])
    with pytest.raises(ActionError, match="more than twice"):
        g.plan_trip("A", "A01", [], stops=[a.id, a.id, a.id])
    fit_box = [o for o in s.offers if o.size == "partial" and o.equipment == "dry" and o.pallets <= 12 and o.weight_lb <= 10_000]
    x, y = next((x, y) for x in fit_box for y in fit_box if x.id < y.id and x.pallets + y.pallets > 12)
    assert "pallets aboard" in g._ride_together(g.trucks["A06"], [x.id, y.id])   # A06 is a box truck: 12 pallets, each fits alone


def test_a_box_truck_driver_resting_away_from_home_pays_for_a_motel_and_a_tractor_driver_does_not():
    from trucksim_core.game import Position
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    for tid, want in (("A06", g.c["lodging_per_night_usd"]), ("A01", 0.0)):
        tr = g.trucks[tid]
        tr.pos = Position("AUS")                                                 # away from Dallas
        before = g.ledger["A"]["lodging"]
        g._apply("A", tr, {"kind": "rest", "truck": tid, "reason": "shift", "until": g.tick + 40})
        assert g.ledger["A"]["lodging"] - before == want
    g._apply("A", g.trucks["A07"], {"kind": "rest", "truck": "A07", "reason": "shift", "until": g.tick + 40})
    assert g.ledger["A"]["lodging"] == g.c["lodging_per_night_usd"]               # A07 rests at home: no motel


def test_offers_come_in_sizes_and_shipper_batches_fit_one_trailer():
    s = scenario(1)
    sizes = {o.size for o in s.offers}
    assert sizes == {"full", "partial", "small"}
    assert all(o.equipment == "dry" for o in s.offers if o.size == "small")
    a, b = _batch(s)
    assert a.orig == b.orig and a.posted == b.posted and a.pallets + b.pallets <= 26


# ---- drivers: teams, slip-seating, owner-operators, temps (M15) -----------------------------------------------
def _driving_ticks(clock_log, upto):
    return sum(min(e, upto) - s for s, e, st in clock_log if st == "D" and s < upto)


def test_a_team_tractor_drives_most_of_a_day_and_each_driver_stays_legal():
    from trucksim_core.hos import check_log
    g = _calm_game([])
    trip = [{"kind": "goto", "city": "MFE"}, {"kind": "goto", "city": "MEM"}]     # about 1,400 miles
    for tid in ("A01", "A02"):                                                 # A01 runs a team, A02 a solo driver
        g._assign(g.trucks[tid], [dict(t) for t in trip])
    _run(g, 96)
    team, solo = g.trucks["A01"], g.trucks["A02"]
    assert len(team.crew) == 2 and len(solo.crew) == 1
    team_drove, solo_drove = sum(_driving_ticks(d.clock.log, 96) for d in team.crew), _driving_ticks(solo.crew[0].clock.log, 96)
    assert team_drove >= ticks_of(20) and team_drove >= solo_drove + ticks_of(8)   # a solo: 11 h, 10 h off, a little more
    for d in team.crew + solo.crew:
        assert check_log(d.clock.log, g.rules) == []


def ticks_of(h):
    return int(h * 4)


def test_crews_change_at_the_hub_and_a_tractor_needs_a_cdl():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    spare = next(d for d in g.drivers.values() if d.seat == "A" and d.truck is None and d.kind == "company")
    old = g.trucks["A02"].crew[0]
    g.assign_drivers("A", "A02", [spare.id])
    assert [d.id for d in g.trucks["A02"].crew] == [spare.id] and old.truck is None
    local = next(d for d in g.drivers.values() if d.seat == "A" and d.kind == "local")
    with pytest.raises(ActionError, match="no CDL"):
        g.assign_drivers("A", "A03", [local.id])
    with pytest.raises(ActionError, match="only its owner"):
        g.assign_drivers("A", "A09", [old.id])
    g.reposition("A", "A03", "WAC")
    _run(g, 16)
    with pytest.raises(ActionError, match="crews change at the hub"):
        g.assign_drivers("A", "A03", [old.id])


def test_a_unit_with_no_driver_cannot_go_anywhere():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    mover = g.trucks["A02"].crew[0]
    g.assign_drivers("A", "A03", [mover.id])                                   # A02's driver takes A03: A02 is empty
    assert not g.trucks["A02"].crew
    with pytest.raises(ActionError, match="no driver"):
        g.reposition("A", "A02", "HOU")


def test_an_owner_operator_turns_down_a_poor_trip_with_the_numbers_and_takes_a_good_one():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    answers = []
    while g.tick < 96 and not (any(not a["accepts"] for a, _ in answers) and any(a["accepts"] for a, _ in answers)):
        for x in g.list_loads("A", fits="tractor_dry")["loads"]:
            plan = g.plan_trip("A", "A09", [x["id"]])
            if plan["feasible"]:
                answers.append((plan["owner_operator"], x["id"]))
        for seat in ("A", "B", "C"):
            g.end_turn(seat)
    no = next(a for a, _ in answers if not a["accepts"])
    assert "after their fuel and costs" in no["why_not"] or "home" in no["why_not"]
    yes, lid = next((a, lid) for a, lid in answers if a["accepts"])
    assert yes["net_per_mile_usd"] >= g.c["owner_operator_net_floor_per_mile_usd"]
    with pytest.raises(ActionError, match="doesn't run empty"):
        g.reposition("A", "A09", "AUS")


def test_an_owner_operators_load_pays_them_their_share_at_delivery():
    g = Game(PACK, scenario(2), model_seats={"A"}, npc_policy=greedy)
    while not g.done:
        greedy(g, "A")
    for e in g.log:
        if e["kind"] == "delivered" and g.trucks[e["truck"]].spec.owner == "owner_operator":
            break
    else:
        pytest.fail("no owner-operator delivery this week")
    paid = [x for x in g.log if x["kind"] == "ledger" and x["seat"] == e["seat"] and x.get("items", {}).get("owner_operator_pay")]
    assert paid and g.ledger[e["seat"]]["owner_operator_pay"] > 0
    assert g.ledger[e["seat"]]["repairs"] == g.ledger[e["seat"]]["repairs"]          # (their repairs never reach the carrier)


def test_a_temp_arrives_after_the_notice_and_costs_the_daily_minimum_even_idle():
    g = Game(PACK, scenario(1), model_seats={"A", "B", "C"})
    t = g.hire_temp_driver("A", days=1)
    temp = g.drivers[t["driver"]]
    assert temp.available_from == g.tick + 48
    with pytest.raises(ActionError, match="isn't available"):
        g.assign_drivers("A", "A02", [temp.id])
    _run(g, 96 * 2)
    assert g.ledger["A"]["temp_drivers"] == pytest.approx(g.c["temp_driver_minimum_hours_per_day"] * g.c["temp_driver_hourly_usd"])
