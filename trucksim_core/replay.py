"""A run as one replay document, the viewer's only input (DESIGN.md, Visualization: the viewer is a pure function of
the scenario and the event log). Built from a finished Game, or from the files a run keeps (save_match_files'
`events` and `result`), plus, optionally, the agents' trajectories (trucksim-llm's per-turn record)."""

import json
from pathlib import Path

from .data import DataPack
from .game import Game

FORMAT = "trucksim-replay/1"
EVENT_KINDS = {  # what the viewer's feed and counters use; snapshots and ledgers travel separately
    "offer_posted", "offer_expired", "offer_withdrawn", "claim_won", "claim_lost", "dispatch", "reposition",
    "pickup_start", "picked", "deliver_start", "delivered", "missed_pickup", "late_for_pickup", "load_cancelled",
    "released", "breakdown", "fault_warning", "repair_ordered", "repaired", "at_shop", "maintenance_booked",
    "maintenance_start", "maintenance_done", "rest", "weather_start", "weather_end", "terminal", "week_over", "turn",
    "closure_start", "closure_end", "held", "detour", "turned_back", "adverse_conditions",
    "crew_change", "declined", "temp_hired", "temp_left",
}


def board(pack: DataPack) -> dict:
    return {"cities": [{"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "freight": c.freight_node, "padd": c.padd}
                       for c in sorted(pack.cities.values(), key=lambda c: c.id)],
            "edges": [{"a": e.a, "b": e.b, "route": e.route, "miles": e.miles, "geometry": [list(p) for p in e.geometry]}
                      for e in pack.edges]}


def from_parts(pack: DataPack, events: list[dict], result: dict, trucks: list[dict], duty: dict,
               trajectories: dict | None = None) -> dict:
    snapshots = [[e["t"], e["trucks"]] for e in events if e["kind"] == "snapshot"]
    ledger = [{"seat": e["seat"], "turn": e["turn"], "t": e["t"], "items": e["items"]} for e in events if e["kind"] == "ledger"]
    return {"format": FORMAT, "week": {"monday": result["monday"], "seed": result["seed"], "turn_ticks": 24, "week_ticks": 672},
            "board": board(pack), "seats": result["seats"], "trucks": trucks, "result": result,
            "events": [e for e in events if e["kind"] in EVENT_KINDS], "snapshots": snapshots, "ledger": ledger,
            "duty": duty, "drivers": result.get("drivers", []), "agents": trajectories or {}}


def from_game(pack: DataPack, game: Game, trajectories: dict | None = None) -> dict:
    trucks = [{"id": t.spec.id, "seat": t.spec.seat, "equipment": t.spec.equipment, "class": t.spec.vclass, "owner": t.spec.owner,
               "driver": " & ".join(d.name for d in t.crew) or "(no driver)", "hub": t.spec.hub}
              for t in sorted(game.trucks.values(), key=lambda t: t.spec.id)]
    result = game.result()
    result = {**result, "drivers": drivers_of(game)}
    return from_parts(pack, game.log, result, trucks, game.duty_logs(), trajectories)


def drivers_of(game: Game) -> list[dict]:
    return [{"id": d.id, "name": d.name, "seat": d.seat, "kind": d.kind, "home": d.home}
            for d in sorted(game.drivers.values(), key=lambda d: d.id)]


def from_files(pack: DataPack, events_jsonl: Path, result_json: Path, trajectories: dict | None = None) -> dict:
    events = [json.loads(x) for x in Path(events_jsonl).read_text().splitlines() if x.strip()]
    result = json.loads(Path(result_json).read_text())
    missing = [k for k in ("trucks", "duty_logs") if k not in result]
    if missing:
        raise ValueError(f"{result_json} has no {missing}: it was written before replays kept them")
    return from_parts(pack, events, result, result["trucks"], result["duty_logs"], trajectories)
