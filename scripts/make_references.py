"""Pick TruckSimEnv v1's eval weeks and write their references (PLAN.md, milestone M4).

Candidates are the storm weeks (the weeks whose NOAA events slow the board's roads the most) and a spread of ordinary
weeks, 2019-2025. Each candidate gets a seed and a seat in rotation; its references are computed (references.py), and
the first EVAL_SIZE that qualify are taken, at least STORM_SHARE of them storm weeks. Every other Monday in the data
is the train pool: its references are computed when its tasks are made (M7).

Usage: .venv/bin/python scripts/make_references.py [--recompute]
  -> data/references/<WORLD>-candidates.jsonl (cached), <WORLD>-eval.jsonl, <WORLD>-pool.json (WORLD in params.py)"""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trucksim_core.data import DataPack  # noqa: E402
from trucksim_core.params import WORLD, Params  # noqa: E402
from trucksim_core.references import Task, references  # noqa: E402
from trucksim_core.scenario import week_start_utc  # noqa: E402

EVAL_SIZE = 15
STORM_WEEKS = 12          # candidates from the top of the severity ranking
ORDINARY_EVERY = 13       # and every 13th Monday (about one a quarter)
STORM_SHARE = 1 / 3


def severity_by_week(pack: DataPack, p: Params) -> dict[str, float]:
    """Sum over a week's events of (1 - speed factor) x hours, in the week Monday 00:00 CST to Monday."""
    out = {}
    for monday in pack.diesel_weeks:
        start = week_start_utc(dt.date.fromisoformat(monday))
        end = start + dt.timedelta(days=7)
        total = 0.0
        for ev in pack.weather_events:
            b = dt.datetime.strptime(ev["begin_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
            e = max(dt.datetime.strptime(ev["end_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc),
                    b + dt.timedelta(hours=p.weather_min_event_hours))
            overlap = (min(e, end) - max(b, start)).total_seconds() / 3600
            if overlap > 0:
                total += (1 - p.weather_speed[ev["type"]]) * overlap
        out[monday] = round(total, 1)
    return out


def compute_candidates(pack: DataPack, p: Params, path: Path) -> list[dict]:
    weeks = [w for w in pack.diesel_weeks if "2019-01-07" <= w <= "2025-12-22"]
    sev = severity_by_week(pack, p)
    storms = sorted(weeks, key=lambda w: (-sev[w], w))[:STORM_WEEKS]
    ordinary = [w for w in weeks[::ORDINARY_EVERY] if w not in storms]
    candidates = [(w, "storm") for w in storms] + [(w, "ordinary") for w in ordinary]
    rows = []
    for i, (monday, kind) in enumerate(candidates):
        task = Task(monday, seed=i + 1, seat="ABC"[i % 3])
        ref = references(pack, task, p)
        ref.update(kind=kind, severity=sev[monday])
        rows.append(ref)
        print(f"{task.id:22} {kind:8} sev {sev[monday]:7.1f}  naive {ref['naive_usd']:8.0f}  anchor {ref['anchor_usd']:8.0f} "
              f"({ref['anchor_config']})  gap {ref['gap_usd']:7.0f}  {'QUALIFIES' if ref['qualifies'] else '-'}", flush=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return rows


def select(rows: list[dict]) -> list[dict]:
    """Qualifying candidates in their computed order, STORM_SHARE of them storm weeks, at most a third per seat."""
    want_storms = int(round(EVAL_SIZE * STORM_SHARE))
    per_seat = EVAL_SIZE // 3
    chosen, seats = [], {"A": 0, "B": 0, "C": 0}
    for kind, quota in (("storm", want_storms), ("ordinary", EVAL_SIZE - want_storms)):
        taken = 0
        for r in rows:
            if taken == quota:
                break
            if r["qualifies"] and r["kind"] == kind and seats[r["seat"]] < per_seat:
                chosen.append(r)
                seats[r["seat"]] += 1
                taken += 1
    if len(chosen) < EVAL_SIZE:
        raise SystemExit(f"only {len(chosen)} tasks fit the quotas; widen the candidates")
    return sorted(chosen, key=lambda r: r["task"])


def main() -> None:
    pack, p = DataPack(), Params()
    out = ROOT / "data" / "references"
    out.mkdir(exist_ok=True)
    cand_path = out / f"{WORLD}-candidates.jsonl"
    if cand_path.exists() and "--recompute" not in sys.argv:
        rows = [json.loads(x) for x in cand_path.read_text().splitlines()]
        print(f"read {len(rows)} candidates from {cand_path.relative_to(ROOT)} (--recompute to redo them)")
    else:
        rows = compute_candidates(pack, p, cand_path)
    chosen = select(rows)
    (out / f"{WORLD}-eval.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in chosen))
    eval_weeks = {r["monday"] for r in chosen}
    weeks = [w for w in pack.diesel_weeks if "2019-01-07" <= w <= "2025-12-22"]
    pool = {"note": "train pool: every Monday 2019-2025 not in the eval set; any seed, any seat",
            "mondays": [w for w in weeks if w not in eval_weeks], "candidates_tried": len(rows),
            "candidates_qualified": sum(r["qualifies"] for r in rows)}
    (out / f"{WORLD}-pool.json").write_text(json.dumps(pool, indent=1) + "\n")
    seats = {s: sum(r["seat"] == s for r in chosen) for s in "ABC"}
    print(f"{pool['candidates_qualified']}/{len(rows)} candidates qualify; eval set: {len(chosen)} tasks "
          f"({sum(r['kind'] == 'storm' for r in chosen)} storm weeks, seats {seats}); pool: {len(pool['mondays'])} Mondays")
    for r in chosen:
        print(f"  {r['task']:22} {r['kind']:8} naive {r['naive_usd']:8.0f} anchor {r['anchor_usd']:8.0f} gap {r['gap_usd']:6.0f}")


if __name__ == "__main__":
    main()
