"""The validity audit (DESIGN.md, Grading): a run is scored only if the world it played was the scenario's.

Checked from the game's final state and its event log, independently of the code that produced them:
- every offer was posted exactly once, at its tick, and every in-week weather event and road closure started once;
- every driver's duty log obeys 49 CFR 395.3 (hos.check_log, a separate algorithm from the live clock), with the
  adverse driving conditions extension only in shifts where it was claimed;
- each seat's ledger equals the sum of its per-turn ledger events plus the end-of-week entries;
- each delivered load was delivered once, by a truck of the seat that owned it."""

from collections import Counter

from .game import Game
from .hos import check_log


def audit(game: Game) -> dict:
    problems: list[str] = []
    log = game.log
    posted = Counter(e["load"] for e in log if e["kind"] == "offer_posted")
    for ld in game.loads.values():
        o = ld.offer
        if o.posted <= game.tick and posted[o.id] != 1:
            problems.append(f"offer {o.id} posted {posted[o.id]} times")
    post_ticks = {e["load"]: e["t"] for e in log if e["kind"] == "offer_posted"}
    for lid, t in post_ticks.items():
        if game.loads[lid].offer.posted != t:
            problems.append(f"offer {lid} posted at {t}, scheduled {game.loads[lid].offer.posted}")
    starts = Counter(e["event_id"] for e in log if e["kind"] == "weather_start")
    for w in game.scenario.weather:
        if w.start <= game.tick and starts[w.event_id] != 1:
            problems.append(f"weather event {w.event_id} started {starts[w.event_id]} times")
    closed = Counter(e["closure_id"] for e in log if e["kind"] == "closure_start")
    for c in game.scenario.closures:
        if c.start <= game.tick and closed[c.closure_id] != 1:
            problems.append(f"closure {c.closure_id} started {closed[c.closure_id]} times")
    claimed = Counter(e["driver"] for e in log if e["kind"] == "adverse_conditions")

    for did, d in sorted(game.drivers.items()):                # every driver's own log: solo, team or at home
        if d.clock.log and d.clock.log[-1][1] != game.tick:
            problems.append(f"{did}'s duty log ends at {d.clock.log[-1][1]}, the clock is at {game.tick}")
        if claimed[did] != len(d.clock.adverse_claims):
            problems.append(f"{did}: {claimed[did]} adverse-conditions events, {len(d.clock.adverse_claims)} claims on the clock")
        for v in check_log(d.clock.log, game.rules, d.clock.adverse_claims)[:5]:
            problems.append(f"{did} ({d.name}): {v}")

    for seat in game.seats:
        summed = Counter()
        for e in log:
            if e["kind"] == "ledger" and e["seat"] == seat:
                summed.update(e["items"])
        for k in ("terminal_deadhead", "terminal_unrepaired", "terminal_in_transit"):
            summed[k] += game.ledger[seat][k]
        for k, v in game.ledger[seat].items():
            if abs(summed.get(k, 0.0) - v) > 0.05:
                problems.append(f"seat {seat} ledger {k}: {v:.2f} booked, {summed.get(k, 0.0):.2f} in the turn ledgers")

    delivered = Counter(e["load"] for e in log if e["kind"] == "delivered")
    for lid, n in delivered.items():
        ld = game.loads[lid]
        if n != 1:
            problems.append(f"{lid} delivered {n} times")
        seats = {e["seat"] for e in log if e["kind"] == "delivered" and e["load"] == lid}
        if seats != {ld.owner}:
            problems.append(f"{lid} owned by {ld.owner} but delivered by {sorted(seats)}")
    return {"ok": not problems, "problems": problems}
