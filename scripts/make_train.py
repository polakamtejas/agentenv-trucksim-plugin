"""Write TruckSimEnv's train split: every Monday of the train pool, from each of the three seats (PLAN.md M11).

The pool is every Monday 2019-2025 that isn't an eval week (data/references/<WORLD>-pool.json, from
make_references.py). Each Monday gets one seed, shared by its three seats, so a Monday's three tasks are one world seen
from each hub. Seeds start at TRAIN_SEED0, far above the eval candidates' 1-40, so no train world is an eval world.

The references are computed exactly as the eval's are (references.py). A task whose anchor doesn't beat naive by
QUALIFY_GAP_USD can't be graded (the ruler would be too short, as for the eval), so it is left out of the split; its
numbers stay in the cache. `kind` is "storm" for the Mondays make_references.py ranks as storm weeks (the
STORM_WEEKS most severe of 2019-2025), else "ordinary"; `severity` is beside it for finer cuts.

The cache is appended to as each task finishes, so a stopped run resumes where it stopped.

Usage: .venv/bin/python scripts/make_train.py [--workers N]
  -> data/references/<WORLD>-train-all.jsonl (every computed task), <WORLD>-train.jsonl (the qualifying ones),
     src/agentenv_trucksim/bundles/trucksim-train/tasks/*.json (one task per qualifying row)"""

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from make_references import STORM_WEEKS, severity_by_week  # noqa: E402
from make_tasks import check_fleet, leaderboard_task  # noqa: E402
from trucksim_core.data import DataPack  # noqa: E402
from trucksim_core.params import WORLD, Params  # noqa: E402
from trucksim_core.references import Task, references  # noqa: E402

TRAIN_SEED0 = 1001
REFS = ROOT / "data" / "references"
TASKS = ROOT / "src" / "agentenv_trucksim" / "bundles" / "trucksim-train" / "tasks"

_pack: DataPack | None = None


def _init() -> None:
    global _pack
    _pack = DataPack()


def _compute(monday: str, seed: int, seat: str) -> dict:
    return references(_pack, Task(monday, seed=seed, seat=seat), Params())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, required=True, help="processes; each solves with one CP-SAT worker")
    args = ap.parse_args()
    check_fleet(Params())

    pool = json.loads((REFS / f"{WORLD}-pool.json").read_text())["mondays"]
    eval_weeks = {json.loads(x)["monday"] for x in (REFS / f"{WORLD}-eval.jsonl").read_text().splitlines()}
    if eval_weeks & set(pool):
        raise SystemExit(f"the pool holds eval weeks: {sorted(eval_weeks & set(pool))}")
    pack, p = DataPack(), Params()
    sev = severity_by_week(pack, p)
    weeks = [w for w in pack.diesel_weeks if "2019-01-07" <= w <= "2025-12-22"]
    storms = set(sorted(weeks, key=lambda w: (-sev[w], w))[:STORM_WEEKS])   # make_references.py's ranking

    wanted = [(monday, TRAIN_SEED0 + i, seat) for i, monday in enumerate(pool) for seat in "ABC"]
    cache = REFS / f"{WORLD}-train-all.jsonl"
    done = {}
    if cache.exists():
        for line in cache.read_text().splitlines():
            row = json.loads(line)
            done[row["task"]] = row
    todo = [w for w in wanted if Task(*w).id not in done]
    print(f"{len(wanted)} train tasks: {len(done)} cached, {len(todo)} to compute with {args.workers} workers", flush=True)

    t0 = time.time()
    with cache.open("a") as out, ProcessPoolExecutor(args.workers, initializer=_init) as ex:
        futures = [ex.submit(_compute, *w) for w in todo]
        for n, f in enumerate(as_completed(futures), 1):
            row = f.result()
            row.update(kind="storm" if row["monday"] in storms else "ordinary", severity=sev[row["monday"]])
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            done[row["task"]] = row
            if n % 25 == 0 or n == len(todo):
                left = (time.time() - t0) / n * (len(todo) - n)
                print(f"  {n}/{len(todo)}  {row['task']}  naive {row['naive_usd']:9.0f}  anchor {row['anchor_usd']:9.0f}  "
                      f"~{left / 60:.0f} min left", flush=True)

    rows = [done[Task(*w).id] for w in wanted]
    train = [r for r in rows if r["qualifies"]]
    (REFS / f"{WORLD}-train.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in train))
    TASKS.mkdir(parents=True, exist_ok=True)
    for old in TASKS.glob("ts-*.json"):
        old.unlink()
    for r in train:
        (TASKS / f"{r['task']}.json").write_text(json.dumps(leaderboard_task(r), indent=1) + "\n")
    seats = {s: sum(r["seat"] == s for r in train) for s in "ABC"}
    print(f"{len(train)}/{len(rows)} train tasks qualify ({sum(r['kind'] == 'storm' for r in train)} on storm weeks, "
          f"seats {seats}); wrote them to {TASKS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
