"""The TruckSim runs app (viewer/app.html) over replay documents (trucksim_core.replay): one run, many runs as a
self-contained file, or a static site that fetches each run as it opens."""

import datetime as dt
import json
import shutil
from importlib.resources import as_file, files
from pathlib import Path

from trucksim_core import replay
from trucksim_core.data import DEFAULT_DATA_DIR, DataPack
from trucksim_core.game import Game
from trucksim_core.planner import policy
from trucksim_core.policies import greedy
from trucksim_core.references import RIVAL, SEATS
from trucksim_core.scenario import build_scenario

SITE_MARKER = '<script id="site-data" type="application/json">null</script>'


def page(doc: dict) -> str:
    """One run as a self-contained TruckSim runs app, opening on that run."""
    row = site_row(doc)
    return app_page([row], {row["id"]: doc})


def simulated(monday: str, seed: int, seat: str | None, seat_policy: str) -> dict:
    """A week played in process: `seat` by `seat_policy` (greedy, rolling-a or rolling-b), every other seat by the rivals."""
    pack = DataPack()
    sc = build_scenario(pack, seed, dt.date.fromisoformat(monday), list(SEATS))
    mine = greedy if seat_policy == "greedy" else policy(seat_policy)
    rival = policy(RIVAL)
    g = Game(pack, sc, model_seats=set(), npc_policy=lambda gm, st: (mine if st == seat else rival)(gm, st))
    doc = replay.from_game(pack, g)
    doc["result"]["rival"] = RIVAL
    if seat:
        doc["seats"][seat]["label"] = f"{seat_policy} (reference policy)"
    return doc


def from_run_files(events: Path, result: Path, trajectories: dict[str, Path]) -> dict:
    return replay.from_files(DataPack(), events, result, {s: json.loads(p.read_text()) for s, p in trajectories.items()})


def _embed(html: str, marker: str, obj) -> str:
    if marker not in html:
        raise ValueError(f"the page has no {marker!r} placeholder")
    return html.replace(marker, marker.replace(">null<", ">" + json.dumps(obj, separators=(",", ":")).replace("</", "<\\/") + "<"))


def run_id(task: str, model: str) -> str:
    return f"{task}--{model.replace('/', '_')}"


def site_row(doc: dict, summary: dict | None = None) -> dict:
    """The app's index row for one run: what its Overview and Explorer tables show."""
    seat = next((s for s, v in doc["seats"].items() if v.get("model")), None)
    me = doc["seats"].get(seat, {}) if seat else {}
    refs = doc["result"].get("references") or {}
    traj = (doc.get("agents") or {}).get(seat) or {}
    row = {"task": f"week-{doc['week']['monday']}", "kind": None, "seat": seat, "hub": me.get("hub"), "model": traj.get("model") or me.get("label") or "",
           "reward": (doc["result"].get("rewards") or {}).get(seat), "profit_usd": me.get("profit_usd"), "delivered": me.get("delivered"),
           "naive_usd": refs.get("naive_usd"), "anchor_usd": refs.get("anchor_usd"), "missed_turns": None, "tool_calls": None,
           "usage": traj.get("usage"), "seconds": traj.get("seconds"), "monday": doc["week"]["monday"], "seed": doc["week"]["seed"]}
    row.update({k: v for k, v in (summary or {}).items() if v is not None})
    row["id"] = run_id(row["task"], row["model"] or "run")
    return row


def app_page(rows: list[dict], docs: dict | None) -> str:
    """The TruckSim runs app with its index inside, and the runs' replays too when `docs` is given (one file)."""
    html = (files("agentenv_trucksim") / "viewer" / "app.html").read_text()
    return _embed(html, SITE_MARKER, {"runs": rows, **({"docs": docs} if docs else {})})


def write_site(out: Path, rows: list[dict], docs: dict) -> None:
    """A static site: index.html (the app), site.json, and runs/<id>.json fetched as each run opens."""
    (out / "runs").mkdir(parents=True, exist_ok=True)
    for rid, doc in docs.items():
        (out / "runs" / f"{rid}.json").write_text(json.dumps(doc, separators=(",", ":")))
    (out / "site.json").write_text(json.dumps({"runs": rows}, separators=(",", ":")))
    (out / "index.html").write_text((files("agentenv_trucksim") / "viewer" / "app.html").read_text())
    write_cinema(out)


# The 3D playthrough (viewer/cinema.html): its libraries, Kenney's trucks and the road shapes, next to the runs it plays.
CINEMA_FILES = ("cinema.html", "trailer.html", "runmodel.mjs", "vendor", "models")


def write_cinema(out: Path) -> None:
    viewer = files("agentenv_trucksim") / "viewer"
    for name in CINEMA_FILES:
        with as_file(viewer / name) as src:
            dst = out / name
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copyfile(src, dst)
    shutil.copyfile(DEFAULT_DATA_DIR / "road_shapes.json", out / "road_shapes.json")
