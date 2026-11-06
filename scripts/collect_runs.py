"""Collect TruckSimEnv runs from agent-env's local stores into a results board and replay pages (PLAN.md, M9).

Reads, read-only, the @local namespace's document store (~/.local/state/agent-env/document_store/local.db, or
$XDG_STATE_HOME/agent-env/...) and the files its records point at. For each run instance: the verifier's grade, the
seat's profit and missed turns, the model, its token usage (from the trucksim-llm trajectory), and the match files,
which become a replay page with the agent's turns.

Usage: .venv/bin/python scripts/collect_runs.py NAME RUN_LOG [RUN_LOG ...]
  RUN_LOG: the output of `agent-env run trucksim --task ... --model ...` (its "instance @local/..." lines)
  -> results/NAME.json, results/NAME.md, and the TruckSim runs app over the runs (git-ignored): results/site/NAME/
     (serve it: scripts/live.py does, at /site/NAME/) and results/site/NAME.html (one self-contained file)"""

import json
import os
import random
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from agentenv_trucksim.replay_page import app_page, from_run_files, site_row, write_site  # noqa: E402
from trucksim_core.references import reward as score_of  # noqa: E402

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "agent-env"
DB = STATE / "document_store" / "local.db"
INSTANCE = re.compile(r"instance (@local/\S+)")
REFS = ROOT / "data" / "references"   # every world's eval references; task ids carry the world, so none collide


def doc(con, table: str, where: str, value) -> dict:
    row = con.execute(f"select doc from {table} where json_extract(doc, '{where}') = ?", (value,)).fetchone()
    if row is None:
        raise KeyError(f"no {table} document with {where} = {value}")
    return json.loads(row[0])


def file_of(con, artifact_id: str, version: int) -> Path:
    row = con.execute("select doc from docs_artifacts where json_extract(doc, '$.id') = ? and json_extract(doc, '$.version') = ?",
                      (artifact_id, version)).fetchone()
    if row is None:
        raise KeyError(f"no artifact {artifact_id} v{version}")
    url = json.loads(row[0])["object_url"]
    if not url.startswith("file://"):
        raise ValueError(f"{artifact_id} is not in a local object store: {url}")
    return Path(url.removeprefix("file://"))


def collect(con, instance_id: str, refs: dict) -> dict:
    inst = doc(con, "docs_task_instances", "$.instance_id", instance_id)
    ctx = inst["context"]
    md = ctx.get("metadata", {})
    task = inst["task_id"].rsplit("/", 1)[-1]
    pr = (ctx.get("prompt_responses") or [{}])[0]
    traj_url = pr.get("agent_trajectory_object_url")
    traj = json.loads(Path(traj_url.removeprefix("file://")).read_text()) if traj_url else {}
    verif = md.get("verifications", {}).get("trucksim", {})
    files = {f["kind"]: file_of(con, f["artifact_id"], f["version"]) for f in md.get("match_files", {}).get("files", [])}
    result = json.loads(files["result"].read_text()) if "result" in files else {}
    seat = refs.get(task, {}).get("seat")
    me = result.get("seats", {}).get(seat, {})
    ref = refs.get(task, {})
    profit = me.get("profit_usd")
    return {"task": task, "instance": instance_id, "status": inst["status"], "error": inst.get("error"),
            "model": pr.get("model") or ctx.get("agent_model"), "seat": seat, "kind": ref.get("kind"),
            # scored with today's formula from the profit; graded_reward is what the verifier recorded at run time
            "reward": score_of(profit, ref["naive_usd"], ref["anchor_usd"]) if profit is not None and ref else None,
            "graded_reward": verif.get("score"), "profit_usd": profit, "delivered": me.get("delivered"),
            "naive_usd": refs.get(task, {}).get("naive_usd"), "anchor_usd": refs.get(task, {}).get("anchor_usd"),
            "missed_turns": result.get("moves", {}).get(seat, {}).get("missed"), "tool_calls": pr.get("tool_call_count"),
            "usage": traj.get("usage"), "seconds": traj.get("seconds"),
            "created": inst.get("created_at_utc"), "completed": inst.get("completed_at_utc"),
            "_files": {k: str(v) for k, v in files.items()}, "_trajectory": traj}


def ci95(values: list[float], draws: int = 2000) -> tuple[float, float]:
    """Bootstrap over weeks, with a fixed seed so the board doesn't change between builds."""
    rng = random.Random(0)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    name, logs = sys.argv[1], sys.argv[2:]
    refs = {r["task"]: r for f in sorted(REFS.glob("*-eval.jsonl")) for r in (json.loads(x) for x in f.read_text().splitlines())}
    ids = [i for log in logs for i in INSTANCE.findall(Path(log).read_text())]
    if not ids:
        sys.exit(f"no 'instance @local/...' lines in {logs}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = [collect(con, i, refs) for i in ids]
    out = ROOT / "results"
    (out / "replays" / name).mkdir(parents=True, exist_ok=True)
    for r in rows:
        if not ("events" in r["_files"] and "result" in r["_files"] and r["_trajectory"]):
            r["replay"] = "none: the run kept no match files or trajectory"
            continue
        stem = f"{r['task']}-{r['model'].replace('/', '_')}"
        traj_path = out / "replays" / name / f"{stem}.trajectory.json"
        # the viewer reads each turn's calls; the turns' full conversations (`messages`) stay in the run record
        viewer_traj = {**r["_trajectory"], "turns": [{k: v for k, v in t.items() if k != "messages"}
                                                     for t in r["_trajectory"].get("turns", [])]}
        traj_path.write_text(json.dumps(viewer_traj))
        try:
            doc_ = from_run_files(Path(r["_files"]["events"]), Path(r["_files"]["result"]), {r["seat"]: traj_path})
        except ValueError as e:                       # a run from an env image older than replays
            r["replay"] = f"none: {e}"
            print(f"no replay for {r['task']} ({r['model']}): {e}", file=sys.stderr)
            continue
        r["_doc"] = doc_
    site_rows, site_docs = [], {}                            # the TruckSim runs app over every replayed run
    for r in rows:
        if "_doc" in r:
            summary = {k: r[k] for k in ("task", "kind", "seat", "model", "reward", "graded_reward", "profit_usd", "delivered",
                                         "naive_usd", "anchor_usd", "missed_turns", "tool_calls", "usage", "seconds")}
            summary["hub"] = refs[r["task"]]["hub"]
            row = site_row(r["_doc"], summary)
            site_rows.append(row)
            site_docs[row["id"]] = r["_doc"]
            r["replay"] = f"results/site/{name}/#/run/{row['id']}"
    if site_rows:
        write_site(out / "site" / name, site_rows, site_docs)
        (out / "site" / f"{name}.html").write_text(app_page(site_rows, site_docs))
        print(f"site: results/site/{name}/ (serve it, or open results/site/{name}.html)", file=sys.stderr)

    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    (out / f"{name}.json").write_text(json.dumps(clean, indent=1) + "\n")

    by_model = defaultdict(list)
    for r in clean:
        by_model[r["model"]].append(r)
    lines = [f"# TruckSimEnv results: {name}", "",
             "Reward: 0.2 at the greedy operator, 1.0 at the re-planner (the anchor), above 1.0 for beating it (a ruler, "
             "not a ceiling), per week in the model's seat against re-planner rivals. 95% CI: bootstrap over weeks.", "",
             "| Model | Weeks | Mean reward (95% CI) | Mean profit | Loads/week | Missed turns | Tool calls/week | Input tokens/week | Output tokens/week | Minutes/week |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for model, rs in sorted(by_model.items(), key=lambda kv: -sum(x["reward"] or 0 for x in kv[1]) / len(kv[1])):
        ok = [x for x in rs if x["reward"] is not None]
        rew = [x["reward"] for x in ok]
        lo, hi = ci95(rew) if len(rew) > 1 else (float("nan"), float("nan"))
        mean = lambda key: sum(x[key] or 0 for x in ok) / len(ok) if ok else float("nan")  # noqa: E731
        tok = lambda key: sum((x["usage"] or {}).get(key, 0) for x in ok) / len(ok) if ok else float("nan")  # noqa: E731
        lines.append(f"| {model} | {len(ok)} of {len(rs)} | **{sum(rew) / len(rew):.3f}** ({lo:.3f} to {hi:.3f}) | "
                     f"{mean('profit_usd'):,.0f} | {mean('delivered'):.1f} | {sum(x['missed_turns'] or 0 for x in ok)} | "
                     f"{mean('tool_calls'):.0f} | {tok('input_tokens'):,.0f} | {tok('output_tokens'):,.0f} | {mean('seconds') / 60:.1f} |")
    lines += ["", "| Task | Kind | Seat | Naive | Anchor | " + " | ".join(sorted(by_model)) + " |",
              "|---|---|---|---:|---:|" + "---:|" * len(by_model)]
    for task in sorted({r["task"] for r in clean}):
        cells = []
        for model in sorted(by_model):
            x = next((r for r in clean if r["task"] == task and r["model"] == model), None)
            cells.append("-" if x is None or x["reward"] is None else f"{x['reward']:.3f} ({x['profit_usd']:,.0f})")
        ref = refs[task]
        lines.append(f"| {task} | {ref['kind']} | {ref['seat']} | {ref['naive_usd']:,.0f} | {ref['anchor_usd']:,.0f} | " + " | ".join(cells) + " |")
    failed = [r for r in clean if r["reward"] is None]
    if failed:
        lines += ["", "Not scored: " + ", ".join(f"{r['task']} ({r['model']}): {r['status']} {r['error'] or ''}".strip() for r in failed)]
    (out / f"{name}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
