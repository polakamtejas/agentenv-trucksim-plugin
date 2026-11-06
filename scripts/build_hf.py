"""Build TruckSimEnv's Hugging Face dataset folder (PLAN.md M11): the tables beside agentenv-hf's own, the eval card
and the data pack. agentenv-hf (`agent-env hf publish`) adds each bundle's `<bundle>_tasks` and `<bundle>_episodes`,
`raw/` and `bundles/` on top, keeping this card's text.

| path | config | what |
|---|---|---|
| tasks/v11-eval.parquet, tasks/v11-train.parquet | v11_tasks | one row per task: its week, seed and seat, its references, its prompts |
| references/v11-candidates.parquet | v11_candidates | the candidate weeks the eval was picked from, with their references |
| results/v11-board.parquet | v11_board | one row per model on the board |
| episodes/v11-eval.parquet | v11_episodes | one row per run: reward, profit and its ledger, loads, missed turns, usage |
| turns/v11-eval.parquet | v11_turns | one row per turn of a run: the turn's whole conversation as chat messages |
| results/eval-v1.1.json | | the eval card, in the FineEnvs catalog's format |
| data/*.json, NOTICE, LICENSE | | the data pack the sim reads, and its sources and licences |

The board is the runs of one collect_runs.py result (results/NAME.json), every one of them: a run that wasn't scored
stops the build, so the board can't silently drop a week.

Usage: .venv/bin/python scripts/build_hf.py RESULTS_NAME OUT_DIR --github OWNER/NAME --hf OWNER/NAME --space OWNER/NAME
         --version vX.Y.Z
  The card is hub/dataset/README.md with its {{PLACEHOLDERS}} filled and the board written between its markers."""

import argparse
import ast
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import pyarrow as pa
    from agentenv_hf.messages import to_messages
    from agentenv_hf.tables import JSON, MESSAGE, parquet
except ImportError as e:
    raise SystemExit(f"{e}: build_hf.py writes its tables with agentenv-hf's schema; add the plugin first:\n"
                     "  .venv/bin/agent-env plugin add 'agentenv-hf @ git+https://github.com/earakely-scale/agentenv-hf-plugin@v0.3.0'")

import collect_runs  # noqa: E402
from trucksim_core.params import WORLD  # noqa: E402
from trucksim_core.references import reward as score_of  # noqa: E402

TAG = WORLD.replace(".", "")          # "v1.1" -> "v11", as in the task ids
REFS = ROOT / "data" / "references"
BUNDLES = ROOT / "src" / "agentenv_trucksim" / "bundles"
AGENT = ROOT / "agents" / "trucksim-llm" / "agent.py"
CARD = ROOT / "hub" / "dataset" / "README.md"
SERVER = ROOT / "src" / "agentenv_trucksim" / "server.py"
AUTHORS = [{"name": "Tejas Polakam", "github": "polakamtejas", "affiliation": "Scale AI"}]

TASKS = pa.schema([
    ("task_id", pa.string()), ("split", pa.string()), ("bundle", pa.string()), ("monday", pa.string()),
    ("seed", pa.int64()), ("seat", pa.string()), ("hub", pa.string()), ("kind", pa.string()),
    ("severity", pa.float64()), ("rival", pa.string()), ("naive_usd", pa.float64()), ("anchor_usd", pa.float64()),
    ("anchor_config", pa.string()), ("gap_usd", pa.float64()), ("do_nothing_usd", pa.float64()),
    ("references_usd", JSON), ("weather_events", pa.int64()), ("offers", pa.int64()), ("system_prompt", pa.string()),
    ("prompt", pa.string()), ("game_settings", JSON),
])
CANDIDATES = pa.schema([
    ("task_id", pa.string()), ("monday", pa.string()), ("seed", pa.int64()), ("seat", pa.string()),
    ("kind", pa.string()), ("severity", pa.float64()), ("naive_usd", pa.float64()), ("anchor_usd", pa.float64()),
    ("anchor_config", pa.string()), ("gap_usd", pa.float64()), ("references_usd", JSON), ("qualifies", pa.bool_()),
    ("in_eval", pa.bool_()),
])
EPISODES = pa.schema([
    ("episode_id", pa.string()), ("task_id", pa.string()), ("model", pa.string()), ("seat", pa.string()),
    ("hub", pa.string()), ("kind", pa.string()), ("status", pa.string()), ("reward", pa.float64()),
    ("graded_reward", pa.float64()), ("profit_usd", pa.float64()), ("naive_usd", pa.float64()),
    ("anchor_usd", pa.float64()), ("ledger_usd", JSON), ("delivered", pa.int64()), ("missed_turns", pa.int64()),
    ("turns_ended_by_model", pa.int64()), ("tool_calls", pa.int64()), ("model_calls", pa.int64()),
    ("input_tokens", pa.int64()), ("output_tokens", pa.int64()), ("minutes", pa.float64()),
    ("created_utc", pa.string()), ("completed_utc", pa.string()), ("replay", pa.string()),
])
TURNS = pa.schema([
    ("episode_id", pa.string()), ("task_id", pa.string()), ("model", pa.string()), ("seat", pa.string()),
    ("turn", pa.int64()), ("clock", pa.string()), ("ended_by", pa.string()), ("notes", pa.string()),
    ("calls", JSON), ("messages", pa.list_(MESSAGE)),
])
BOARD = pa.schema([
    ("rank", pa.int64()), ("model", pa.string()), ("weeks", pa.int64()), ("mean_reward", pa.float64()),
    ("ci95_low", pa.float64()), ("ci95_high", pa.float64()), ("ordinary_mean", pa.float64()),
    ("storm_mean", pa.float64()), ("mean_profit_usd", pa.float64()), ("loads_per_week", pa.float64()),
    ("missed_turns", pa.int64()), ("input_tokens_per_week", pa.float64()), ("output_tokens_per_week", pa.float64()),
    ("minutes_per_week", pa.float64()),
])


def rules() -> str:
    """The operator agent's system prompt (RULES in agents/trucksim-llm/agent.py), read without importing the agent."""
    for node in ast.parse(AGENT.read_text()).body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["RULES"]:
            return ast.literal_eval(node.value)
    raise SystemExit(f"no RULES in {AGENT}")


def read_rows(name: str) -> list[dict]:
    return [json.loads(x) for x in (REFS / f"{WORLD}-{name}.jsonl").read_text().splitlines()]


def task_steps(bundle: str, task: str) -> list[dict]:
    return json.loads((BUNDLES / bundle / "tasks" / f"{task}.json").read_text())


def task_row(r: dict, split: str, bundle: str, system: str) -> dict:
    steps = task_steps(bundle, r["task"])
    prompt = next(s["prompt"] for s in steps if s["type"] == "prompt_agent")
    settings = next(s["game_settings"] for s in steps if s["type"] == "open_lobby")
    return {"task_id": r["task"], "split": split, "bundle": bundle, "monday": r["monday"], "seed": r["seed"],
            "seat": r["seat"], "hub": r["hub"], "kind": r["kind"], "severity": r["severity"], "rival": r["rival"],
            "naive_usd": r["naive_usd"], "anchor_usd": r["anchor_usd"], "anchor_config": r["anchor_config"],
            "gap_usd": r["gap_usd"], "do_nothing_usd": r["profits_usd"]["do_nothing"], "references_usd": r["profits_usd"],
            "weather_events": r["weather_events"], "offers": r["offers"], "system_prompt": system, "prompt": prompt,
            "game_settings": settings}


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def tools() -> list[str]:
    """The env's MCP tools, in the order server.py declares them, read without importing the server."""
    names = re.findall(r"@tool\(\)\s+(?:async )?def ([a-z_]+)\(", SERVER.read_text())
    if not names:
        raise SystemExit(f"no @tool() methods found in {SERVER}")
    return names


def board_md(board: list[dict], baselines: list[dict]) -> str:
    lines = ["| | Model | Weeks | Mean reward (95% CI) | Ordinary weeks | Storm weeks | Mean profit | Loads a week | "
             "Missed turns | Input tokens a week | Output tokens a week | Minutes a week |",
             "|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in board:
        lines.append(f"| {r['rank']} | {r['model']} | {r['weeks']} | **{r['mean_reward']:.3f}** ({r['ci95_low']:.3f} to "
                     f"{r['ci95_high']:.3f}) | {r['ordinary_mean']:.3f} | {r['storm_mean']:.3f} | "
                     f"${r['mean_profit_usd']:,.0f} | {r['loads_per_week']:.1f} | {r['missed_turns']} | "
                     f"{r['input_tokens_per_week']:,.0f} | {r['output_tokens_per_week']:,.0f} | {r['minutes_per_week']:.1f} |")
    for b in baselines:
        lines.append(f"| | {b['name']} | {b['weeks']} | {b['mean']:.3f} | | | ${b['mean_profit_usd']:,.0f} | | | | | |")
    return "\n".join(lines)


def baselines_of(eval_rows: list[dict]) -> list[dict]:
    """The references, as players on the same weeks: each one's reward by the eval's own ruler."""
    out = []
    for name, label in (("anchor", "Re-planner, the better configuration (the anchor)"),
                        ("rolling-b", "Re-planner, configuration b"), ("rolling-a", "Re-planner, configuration a"),
                        ("naive", "Greedy operator (naive)"), ("do_nothing", "Doing nothing")):
        profits = [r["anchor_usd"] if name == "anchor" else r["profits_usd"][name] for r in eval_rows]
        rewards = [score_of(p, r["naive_usd"], r["anchor_usd"]) for p, r in zip(profits, eval_rows)]
        out.append({"name": label, "weeks": len(eval_rows), "mean": mean(rewards), "mean_profit_usd": mean(profits)})
    return out


def fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    left = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", template)))
    if left:
        raise SystemExit(f"the card's placeholders {left} have no value")
    return template


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_name")
    ap.add_argument("out_dir", type=Path)
    for flag in ("--github", "--hf", "--space", "--version"):
        ap.add_argument(flag, required=True)
    args = ap.parse_args()
    name, out = args.results_name, args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    system = rules()
    eval_rows, train_rows, cand_rows = read_rows("eval"), read_rows("train"), read_rows("candidates")
    refs = {r["task"]: r for r in eval_rows}

    def write(path: str, rows: list[dict], schema: pa.Schema) -> None:
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(parquet(rows, schema))
        print(f"  {path}: {len(rows)} rows", file=sys.stderr)

    write(f"tasks/{TAG}-eval.parquet", [task_row(r, "eval", "trucksim", system) for r in eval_rows], TASKS)
    write(f"tasks/{TAG}-train.parquet", [task_row(r, "train", "trucksim-train", system) for r in train_rows], TASKS)
    eval_ids = set(refs)
    write(f"references/{TAG}-candidates.parquet",
          [{**{k: r[k] for k in ("monday", "seed", "seat", "kind", "severity", "naive_usd", "anchor_usd", "anchor_config",
                                 "gap_usd", "qualifies")},
            "task_id": r["task"], "references_usd": r["profits_usd"], "in_eval": r["task"] in eval_ids} for r in cand_rows],
          CANDIDATES)

    board_runs = json.loads((ROOT / "results" / f"{name}.json").read_text())
    unscored = [f"{r['task']} ({r['model']}): {r['status']}" for r in board_runs if r.get("reward") is None]
    if unscored:
        raise SystemExit(f"{name} has runs that weren't scored, so the board would drop their weeks: {unscored}")
    con = sqlite3.connect(f"file:{collect_runs.DB}?mode=ro", uri=True)
    episodes, turns = [], []
    for b in board_runs:
        run = collect_runs.collect(con, b["instance"], refs)
        result = json.loads(Path(run["_files"]["result"]).read_text())
        me, traj, usage = result["seats"][run["seat"]], run["_trajectory"], run["_trajectory"]["usage"]
        episode_id = run["instance"].rsplit("/", 1)[-1]
        episodes.append({
            "episode_id": episode_id, "task_id": run["task"], "model": run["model"], "seat": run["seat"],
            "hub": refs[run["task"]]["hub"], "kind": run["kind"], "status": run["status"], "reward": run["reward"],
            "graded_reward": run["graded_reward"], "profit_usd": run["profit_usd"], "naive_usd": run["naive_usd"],
            "anchor_usd": run["anchor_usd"], "ledger_usd": me["ledger_usd"], "delivered": run["delivered"],
            "missed_turns": run["missed_turns"], "turns_ended_by_model": sum(t["ended_by"] == "agent" for t in traj["turns"]),
            "tool_calls": usage["tool_calls"], "model_calls": usage["model_calls"], "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"], "minutes": round(traj["seconds"] / 60, 2),
            "created_utc": run["created"], "completed_utc": run["completed"],
            "replay": f"{run['task']}--{run['model'].replace('/', '_')}"})
        for t in traj["turns"]:
            turns.append({"episode_id": episode_id, "task_id": run["task"], "model": run["model"], "seat": run["seat"],
                          "turn": t["turn"], "clock": t["clock"], "ended_by": t["ended_by"], "notes": t["notes"],
                          "calls": t["calls"],
                          # recorded by the agent from 2026-11-06; earlier runs kept only the calls
                          "messages": to_messages({"messages": t["messages"]}) if "messages" in t else None})
    write(f"episodes/{TAG}-eval.parquet", episodes, EPISODES)
    write(f"turns/{TAG}-eval.parquet", turns, TURNS)

    board = []
    for model in sorted({e["model"] for e in episodes}):
        es = [e for e in episodes if e["model"] == model]
        lo, hi = collect_runs.ci95([e["reward"] for e in es])
        board.append({"model": model, "weeks": len(es), "mean_reward": mean([e["reward"] for e in es]),
                      "ci95_low": lo, "ci95_high": hi,
                      "ordinary_mean": mean([e["reward"] for e in es if e["kind"] == "ordinary"]),
                      "storm_mean": mean([e["reward"] for e in es if e["kind"] == "storm"]),
                      "mean_profit_usd": mean([e["profit_usd"] for e in es]),
                      "loads_per_week": mean([e["delivered"] for e in es]),
                      "missed_turns": sum(e["missed_turns"] for e in es),
                      "input_tokens_per_week": mean([e["input_tokens"] for e in es]),
                      "output_tokens_per_week": mean([e["output_tokens"] for e in es]),
                      "minutes_per_week": mean([e["minutes"] for e in es])})
    board.sort(key=lambda r: -r["mean_reward"])
    for i, r in enumerate(board, 1):
        r["rank"] = i
    write(f"results/{TAG}-board.parquet", board, BOARD)
    (out / "results" / f"{TAG}-board.json").write_text(json.dumps(board, indent=1) + "\n")

    baselines = baselines_of(eval_rows)
    tool_names = tools()
    storms = sum(r["kind"] == "storm" for r in eval_rows)
    hf_link = f"https://huggingface.co/datasets/{args.hf}"
    card = fill(CARD.read_text(), {
        "GITHUB": args.github, "GITHUB_BADGE": args.github.split("/")[1].replace("-", "--"), "HF_REPO": args.hf,
        "HF_SPACE": args.space, "VERSION": args.version, "TOOLS_N": str(len(tool_names)),
        "TOOLS": ", ".join(f"`{t}`" for t in tool_names), "EVAL_TASKS": str(len(eval_rows)), "EVAL_STORMS": str(storms),
        "EVAL_ORDINARY": str(len(eval_rows) - storms), "CANDIDATES": str(len(cand_rows)),
        "TRAIN_TASKS": f"{len(train_rows):,}", "EXAMPLE_TASK": eval_rows[0]["task"]})
    start, end = "<!-- board -->", "<!-- /board -->"
    if card.count(start) != 1 or card.count(end) != 1:
        raise SystemExit(f"{CARD} needs one {start} ... {end} block")
    before, rest = card.split(start)
    card = f"{before}{start}\n\n{board_md(board, baselines)}\n\n{end}{rest.split(end, 1)[1]}"
    (out / "README.md").write_text(card)

    eval_card = {
        "env": "trucksim", "version": WORLD, "authors": AUTHORS, "framework": "agentenv",
        "tasks": {"split": "eval", "n": len(eval_rows), "unit": "weeks",
                  "source": "one real week of 2019-2025 on the Texas Triangle (24 cities, 38 roads): EIA diesel, NOAA "
                            "Storm Events placed on the roads they hit and the road closures their narratives report "
                            "(read by hand) plus modeled ones, FAF5 freight flows behind the load board, 49 CFR 395 "
                            "hours of service, costs from FHWA, BLS and ATRI's published totals",
                  "selection": f"{storms} of the most severe storm weeks and {len(eval_rows) - storms} ordinary weeks a "
                               f"season apart, the first of {len(cand_rows)} candidates whose re-planner beats the greedy "
                               "operator by $1,000, a seat in rotation, five a seat; fixed before any model ran"},
        "harness": {"agent": "trucksim-llm", "tools": tool_names, "max_turns": 28,
                    "note": "28 game turns of 6 hours, each a fresh conversation of up to 24 model calls"},
        "grading": {"reference": "the better of two configurations of a rolling CP-SAT re-planner, played in the "
                                 "agent's seat on the same week against the same re-planner rivals",
                    "reward": "max(0, 0.2 + 0.8 (P - naive) / (anchor - naive)), P the seat's profit after the "
                              "end-of-week charges, naive a greedy operator; above 1.0 for beating the re-planner",
                    "excused": "nothing: a run is scored only if the week finishes and passes the audit (every offer, "
                               "storm and closure as drawn, every duty log within 49 CFR 395.3, the ledger adding up); "
                               "a seat whose turns the env ended more than 10% of the time scores 0",
                    "judge": False},
        "baselines": [{"name": b["name"], "mean": round(b["mean"], 4)} for b in baselines],
        "models": [{"model": r["model"], "runs": r["weeks"], "mean": round(r["mean_reward"], 4),
                    "ci95": [round(r["ci95_low"], 4), round(r["ci95_high"], 4)], "feasible": r["weeks"],
                    "usd_per_episode": None} for r in board],
        "links": {"runs": f"{hf_link}/viewer/v11_episodes", "replay": f"https://huggingface.co/spaces/{args.space}",
                  "code": f"https://github.com/{args.github}"},
        "date": dt.date.today().isoformat()}
    (out / "results" / f"eval-{WORLD}.json").write_text(json.dumps(eval_card, indent=1) + "\n")

    (out / "data").mkdir(exist_ok=True)
    for f in sorted((ROOT / "data").glob("*.json")):
        shutil.copy2(f, out / "data" / f.name)
    for f in ("NOTICE", "LICENSE"):
        shutil.copy2(ROOT / f, out / f)
    shutil.copytree(CARD.parent / "assets", out / "assets", dirs_exist_ok=True)   # the card's hero
    print(f"wrote {out}: {len(eval_rows)} eval and {len(train_rows)} train tasks, {len(episodes)} runs of "
          f"{len(board)} models", file=sys.stderr)


if __name__ == "__main__":
    main()
