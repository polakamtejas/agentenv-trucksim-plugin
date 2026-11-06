"""Write TruckSimEnv's bundle tasks from the eval references of the current world (PLAN.md, milestones M7 and M16).

- one leaderboard task per row of data/references/<WORLD>-eval.jsonl: the operator agent plays the row's seat, the other
  two seats are the game's AI (the rival policy), and the lobby carries the row's references, so trucksim-verifier
  scores the week 0.2 at the greedy operator and 1.0 at the re-planner;
- one showmatch: three operators, one per seat, each its own model, graded by grade_match (won by the most profit).

Usage: .venv/bin/python scripts/make_tasks.py   -> src/agentenv_trucksim/bundles/trucksim/tasks/*.json"""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collections import Counter  # noqa: E402

from trucksim_core.params import WORLD, Params  # noqa: E402

TASKS = ROOT / "src" / "agentenv_trucksim" / "bundles" / "trucksim" / "tasks"
EVAL = ROOT / "data" / "references" / f"{WORLD}-eval.jsonl"
HUBS = {"A": "Dallas", "B": "Houston", "C": "San Antonio"}
AGENT = "trucksim-llm"
PROMPT_TIMEOUT_SECONDS = 4 * 3600
CALLS_PER_TURN = 24                 # model calls per game turn: ten units and a roster take more than v1's four trucks
SHOWMATCH_MODELS = {"A": "anthropic/claude-opus-5-5", "B": "openai/gpt-6.1-sol", "C": "anthropic/claude-sonnet-5-5"}


# The fleet as the task prompt states it. It must match params.py (checked below), so the prompt can't drift from the
# game again: v1.1's tasks said "four trucks ... three dry vans and a reefer", v1's fleet, until 2026-11-06.
FLEET = ("ten units (four company tractors with dry vans, one with a reefer, two box trucks, a cargo van and two "
         "owner-operators' tractors leased on to you) and twelve drivers")
FLEET_COUNTS = {("tractor_dry", "company"): 4, ("tractor_reefer", "company"): 1, ("box", "company"): 2,
                ("van", "company"): 1, ("tractor_dry", "owner_operator"): 1, ("tractor_reefer", "owner_operator"): 1}
FLEET_DRIVERS = 12


def check_fleet(p: Params) -> None:
    units, drivers = dict(Counter(p.fleet_per_seat)), len(p.crew_per_seat)
    if units != FLEET_COUNTS or drivers != FLEET_DRIVERS:
        raise SystemExit(f"params.py's fleet ({units}, {drivers} drivers) no longer matches FLEET; rewrite FLEET")


def prompt(seat: str, monday: str) -> str:
    week = dt.date.fromisoformat(monday).strftime("%B %-d, %Y")
    return (f"You run seat {seat}: a small carrier based in {HUBS[seat]}, with {FLEET}, for the week of {week}. Two "
            f"rival carriers, based in the other hubs, work the same load board. Make your carrier as profitable as "
            f"you can by the end of the week.")


def leaderboard_task(row: dict) -> list:
    seat = row["seat"]
    steps = [
        {"id": "deploy", "type": "deploy_env", "env_id": "trucksim", "ttl_seconds": PROMPT_TIMEOUT_SECONDS + 1800},
        {"id": "operator", "type": "deploy_agent", "a2a_agent_id": AGENT, "agent_name": "operator", "env_ids": [],
         "ttl_seconds": PROMPT_TIMEOUT_SECONDS + 1800},
        {"id": "lobby", "type": "open_lobby", "env_id": "trucksim",
         "game_settings": {"monday": row["monday"], "seed": row["seed"], "rival": row["rival"],
                           "naive_usd": row["naive_usd"], "anchor_usd": row["anchor_usd"]}},
    ]
    for s in "ABC":
        slot = {"id": f"seat-{s.lower()}", "type": "add_player_slot", "env_id": "trucksim", "player_id": s}
        slot.update({"player_kind": "agent", "player_name": "operator"} if s == seat else {"player_kind": "ai"})
        steps.append(slot)
    steps += [
        {"id": "close", "type": "close_lobby", "env_id": "trucksim"},
        {"id": "play", "type": "prompt_agent", "agent_name": "operator", "prompt_id": row["task"],
         "prompt": prompt(seat, row["monday"]), "max_turns": CALLS_PER_TURN, "timeout_seconds": PROMPT_TIMEOUT_SECONDS},
        {"id": "finish", "type": "finish_match", "env_id": "trucksim"},
        {"id": "files", "type": "save_match_files", "env_id": "trucksim", "fail_task_on_error": False},
        {"id": "grade", "type": "env_outcome_verifier", "env_id": "trucksim", "file_artifact_id": "trucksim-verifier",
         "verifier_id": "trucksim", "score_aggregator": "weighted_average"},
    ]
    return steps


def showmatch(monday: str, seed: int) -> list:
    steps = [{"id": "deploy", "type": "deploy_env", "env_id": "trucksim", "ttl_seconds": PROMPT_TIMEOUT_SECONDS + 1800}]
    for s in "ABC":
        steps.append({"id": f"operator-{s.lower()}", "type": "deploy_agent", "a2a_agent_id": AGENT,
                      "agent_name": f"operator-{s.lower()}", "env_ids": [], "ttl_seconds": PROMPT_TIMEOUT_SECONDS + 1800,
                      "depends_on": [{"task_step_id": "deploy"}]})
    steps.append({"id": "lobby", "type": "open_lobby", "env_id": "trucksim", "game_settings": {"monday": monday, "seed": seed},
                  "depends_on": [{"task_step_id": "deploy"}]})
    for s in "ABC":
        steps.append({"id": f"seat-{s.lower()}", "type": "add_player_slot", "env_id": "trucksim", "player_id": s,
                      "player_kind": "agent", "player_name": f"operator-{s.lower()}",
                      "depends_on": [{"task_step_id": "lobby"}, {"task_step_id": f"operator-{s.lower()}"}]})
    steps.append({"id": "close", "type": "close_lobby", "env_id": "trucksim",
                  "depends_on": [{"task_step_id": f"seat-{s.lower()}"} for s in "ABC"]})
    for s in "ABC":   # all three play at once: end_turn waits for the slowest
        steps.append({"id": f"play-{s.lower()}", "type": "prompt_agent", "agent_name": f"operator-{s.lower()}",
                      "prompt_id": f"showmatch-{s}", "prompt": prompt(s, monday), "model": SHOWMATCH_MODELS[s],
                      "max_turns": CALLS_PER_TURN, "timeout_seconds": PROMPT_TIMEOUT_SECONDS,
                      "depends_on": [{"task_step_id": "close"}]})
    plays = [{"task_step_id": f"play-{s.lower()}"} for s in "ABC"]
    steps += [
        {"id": "finish", "type": "finish_match", "env_id": "trucksim", "depends_on": plays},
        {"id": "files", "type": "save_match_files", "env_id": "trucksim", "fail_task_on_error": False,
         "depends_on": [{"task_step_id": "finish"}]},
        {"id": "grade", "type": "grade_match", "env_id": "trucksim", "weights": {"profit": 1}, "max_missed": 0.1,
         "depends_on": [{"task_step_id": "finish"}]},
    ]
    return steps


def smoke(row: dict, policy: str) -> list:
    """No model: the game's own policy plays the row's seat through the autoplay extension. rolling-a grades 1.0 when
    it is the row's anchor, and greedy grades 0.2 (it is naive), so these check the whole pipe end to end."""
    seat = row["seat"]
    return [
        {"id": "deploy", "type": "deploy_env", "env_id": "trucksim"},
        {"id": "lobby", "type": "open_lobby", "env_id": "trucksim",
         "game_settings": {"monday": row["monday"], "seed": row["seed"], "naive_usd": row["naive_usd"], "anchor_usd": row["anchor_usd"]}},
        *[{"id": f"seat-{s.lower()}", "type": "add_player_slot", "env_id": "trucksim", "player_id": s, "player_kind": "ai"}
          if s != seat else
          {"id": f"seat-{s.lower()}", "type": "add_player_slot", "env_id": "trucksim", "player_id": s, "player_kind": "agent",
           "player_name": "autoplay", "register": False} for s in "ABC"],
        {"id": "close", "type": "close_lobby", "env_id": "trucksim"},
        {"id": "play", "type": "apply_server_config", "env_id": "trucksim", "timeout_seconds": 600,
         "directives": [{"service": "trucksim", "uri": "urn:trucksim:autoplay/v1", "args": {"seat": seat, "policy": policy}}]},
        {"id": "finish", "type": "finish_match", "env_id": "trucksim"},
        {"id": "grade", "type": "env_outcome_verifier", "env_id": "trucksim", "file_artifact_id": "trucksim-verifier",
         "verifier_id": "trucksim", "score_aggregator": "weighted_average"},
    ]


def main() -> None:
    check_fleet(Params())
    rows = [json.loads(x) for x in EVAL.read_text().splitlines()]
    for old in TASKS.glob("ts-*.json"):
        old.unlink()
    for row in rows:
        (TASKS / f"{row['task']}.json").write_text(json.dumps(leaderboard_task(row), indent=1) + "\n")
    (TASKS / "showmatch.json").write_text(json.dumps(showmatch("2021-02-15", 2), indent=1) + "\n")
    anchored = next(r for r in rows if r["anchor_config"] == "rolling-a")      # autoplay's rolling-a is then the anchor
    (TASKS / "smoke-anchor.json").write_text(json.dumps(smoke(anchored, "rolling-a"), indent=1) + "\n")
    (TASKS / "smoke-naive.json").write_text(json.dumps(smoke(anchored, "greedy"), indent=1) + "\n")
    print(f"wrote {len(rows)} leaderboard tasks, the showmatch and the smoke tasks ({anchored['task']}) to {TASKS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
