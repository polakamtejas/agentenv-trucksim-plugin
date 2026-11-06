"""References and the reward (DESIGN.md, Grading; PLAN.md M4).

A task is one week, one seed and the seat the model plays; the other seats are rival carriers playing RIVAL. Each
reference plays the model's seat against those same rivals:
- naive: the greedy policy (policies.greedy);
- the anchor: the better of the rolling re-planner's configurations (planner.CONFIGS).

reward(profit) = max(0, 0.2 + 0.8 * (profit - naive) / (anchor - naive)): 0.2 at naive and 1.0 at the anchor. The two
are a ruler, not a ceiling (there is no known optimum for this game): beating the anchor scores above 1.0, by the same
measure, and the score falls to 0 a quarter of the gap below naive. A week qualifies when the anchor beats naive by
QUALIFY_GAP_USD, so the ruler's unit is worth playing for."""

import datetime as dt
from dataclasses import dataclass

from .data import DataPack
from .game import Game
from .params import WORLD_TAG, Params
from .planner import CONFIGS, policy
from .policies import greedy
from .scenario import Scenario, build_scenario

SEATS = (("A", "DAL"), ("B", "HOU"), ("C", "SA"))
RIVAL = "rolling-a"
QUALIFY_GAP_USD = 1_000.0


@dataclass(frozen=True)
class Task:
    monday: str
    seed: int
    seat: str

    @property
    def id(self) -> str:
        return f"ts-{WORLD_TAG}-{self.monday}-{self.seat}-s{self.seed}"

    def scenario(self, pack: DataPack, params: Params = Params()) -> Scenario:
        return build_scenario(pack, self.seed, dt.date.fromisoformat(self.monday), list(SEATS), params)


def rival_policy():
    return policy(RIVAL)


def play(pack: DataPack, scenario: Scenario, seat: str, seat_policy) -> Game:
    """A whole week with `seat` played by seat_policy and every other seat by the rivals."""
    rival = rival_policy()
    return Game(pack, scenario, model_seats=set(), npc_policy=lambda g, s: (seat_policy if s == seat else rival)(g, s))


def lazy(game: Game, seat: str) -> None:
    game.end_turn(seat)


def references(pack: DataPack, task: Task, params: Params = Params()) -> dict:
    s = task.scenario(pack, params)
    profits = {"naive": play(pack, s, task.seat, greedy).profit(task.seat)}
    for name in sorted(CONFIGS):
        profits[name] = play(pack, s, task.seat, policy(name)).profit(task.seat)
    profits["do_nothing"] = play(pack, s, task.seat, lazy).profit(task.seat)
    anchor_name = max(sorted(CONFIGS), key=lambda n: profits[n])
    anchor = profits[anchor_name]
    return {"task": task.id, "monday": task.monday, "seed": task.seed, "seat": task.seat, "hub": dict(SEATS)[task.seat],
            "rival": RIVAL, "profits_usd": {k: round(v, 2) for k, v in profits.items()},
            "naive_usd": round(profits["naive"], 2), "anchor_usd": round(anchor, 2), "anchor_config": anchor_name,
            "gap_usd": round(anchor - profits["naive"], 2), "qualifies": anchor - profits["naive"] >= QUALIFY_GAP_USD,
            "weather_events": len(s.weather), "offers": len(s.offers)}


def reward(profit: float, naive: float, anchor: float) -> float:
    if anchor <= naive:
        raise ValueError(f"the anchor ({anchor:.2f}) must beat naive ({naive:.2f}); this week doesn't qualify")
    return max(0.0, 0.2 + 0.8 * (profit - naive) / (anchor - naive))
