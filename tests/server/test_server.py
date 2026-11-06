"""The env server in process: lobby -> game -> seat-scoped tools -> turns -> the data the verifier reads."""

import asyncio
import json
from pathlib import Path

import pytest
from agentenv_game import GameError, SlotRequest
from agentenv_game.routing import PLAYER

from agentenv_trucksim.server import TruckSimEnv
from trucksim_core.params import WORLD

EVAL = Path(__file__).resolve().parents[2] / "data" / "references" / f"{WORLD}-eval.jsonl"


def as_seat(seat):
    """Make the next calls come from a seat's address, as /players/<seat>/mcp does."""
    return PLAYER.set(seat)


def lobby(settings: dict, agents=("A",)) -> TruckSimEnv:
    env = TruckSimEnv()
    env.new_lobby(settings)
    for seat in "ABC":
        kind = "agent" if seat in agents else "ai"
        env.fill_slot(SlotRequest(lobby_id=env.lobby.lobby_id, player_id=seat, player_kind=kind,
                                  player_name=f"player-{seat}" if kind == "agent" else None))
    asyncio.run(env.close_lobby())
    return env


def test_one_agent_plays_a_week_through_the_tools_and_the_data_is_graded():
    env = lobby({"monday": "2024-06-03", "seed": 1, "naive_usd": -1500.0, "anchor_usd": 1500.0})
    as_seat("A")
    s = json.loads(env.situation())
    assert s["seat"] == "A" and s["hub"] == "DAL" and s["turn"] == 0
    for _ in range(28):
        nxt = json.loads(asyncio.run(env.end_turn()))
    assert nxt["done"]
    data = env.get()[0].data
    assert data["done"] and data["audit"]["ok"], data["audit"]
    assert set(data["rewards"]) == {"A"} and 0.0 <= data["rewards"]["A"] <= 1.0
    assert data["moves"]["A"] == {"made": 28, "missed": 0}


def test_tools_act_for_the_calling_seat_only():
    env = lobby({"monday": "2024-06-03", "seed": 1})
    as_seat("A")
    with pytest.raises(ValueError, match="not one of seat A's trucks"):
        env.get_truck("B01")
    as_seat("B")                                  # B is the game's AI: no agent plays there
    with pytest.raises(GameError, match="no player slot 'B'"):
        env.situation()


def test_autoplay_with_the_anchor_config_scores_one_and_greedy_scores_point_two():
    row = next(json.loads(x) for x in EVAL.read_text().splitlines() if json.loads(x)["anchor_config"] == "rolling-a")
    settings = {"monday": row["monday"], "seed": row["seed"], "naive_usd": row["naive_usd"], "anchor_usd": row["anchor_usd"]}
    agents = (row["seat"],)
    best = lobby(settings, agents).autoplay(row["seat"], "rolling-a")
    assert best["reward"] == pytest.approx(1.0, abs=1e-4) and best["profit_usd"] == pytest.approx(row["anchor_usd"], abs=0.01)
    naive = lobby(settings, agents).autoplay(row["seat"], "greedy")
    assert naive["reward"] == pytest.approx(0.2, abs=1e-4) and naive["profit_usd"] == pytest.approx(row["naive_usd"], abs=0.01)


def test_an_idle_seat_has_its_turn_ended_for_it():
    env = lobby({"monday": "2024-06-03", "seed": 1, "idle_turn_seconds": 0.05}, agents=("A", "B"))
    as_seat("A")
    asyncio.run(asyncio.sleep(0.1))
    after = json.loads(asyncio.run(env.end_turn()))      # B never calls: A's wait ends B's turn
    assert after["turn"] == 1
    assert env.missed == {"A": 0, "B": 1} and env.made == {"A": 1, "B": 0}


def test_references_need_exactly_one_agent_seat():
    with pytest.raises(Exception, match="exactly one agent seat"):
        lobby({"monday": "2024-06-03", "seed": 1, "naive_usd": 0.0, "anchor_usd": 100.0}, agents=("A", "B"))


def test_settings_are_checked():
    env = TruckSimEnv()
    with pytest.raises(Exception, match="not a Monday"):
        env.new_lobby({"monday": "2024-06-04"})
    with pytest.raises(Exception, match="both naive_usd and anchor_usd"):
        env.new_lobby({"naive_usd": 1.0})


def test_the_driver_tools_work_through_the_env():
    env = lobby({"monday": "2024-06-03", "seed": 1})
    as_seat("A")
    drivers = json.loads(env.list_drivers())["drivers"]
    assert len(drivers) == 12 and {d["driver"][:2] for d in drivers} == {"A-"}
    spare = next(d for d in drivers if d["where"].startswith("off duty at home"))
    crew = json.loads(env.assign_drivers("A02", [spare["driver"]]))["crew"]
    assert [c["driver"] for c in crew] == [spare["driver"]]
    temp = json.loads(env.hire_temp_driver(2))
    assert temp["driver"] == "A-T1" and "h on duty" in temp["rate"]
    with pytest.raises(ValueError, match="only its owner"):
        env.assign_drivers("A09", [spare["driver"]])
