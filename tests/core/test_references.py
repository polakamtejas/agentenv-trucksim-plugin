"""References and the reward. The golden test recomputes one eval task: any change to the sim that would silently
move the published references fails here, so they get regenerated on purpose (scripts/make_references.py --recompute)."""

import json
from pathlib import Path

import pytest

from trucksim_core.data import DataPack
from trucksim_core.params import WORLD
from trucksim_core.references import Task, references, reward

EVAL = Path(__file__).resolve().parents[2] / "data" / "references" / f"{WORLD}-eval.jsonl"


def test_reward_is_0_2_at_naive_1_at_the_anchor_and_0_a_quarter_gap_below():
    assert reward(-1000, -1000, 1000) == pytest.approx(0.2)
    assert reward(1000, -1000, 1000) == pytest.approx(1.0)
    assert reward(3000, -1000, 1000) == pytest.approx(1.8)     # no ceiling: beating the anchor counts
    assert reward(-1500, -1000, 1000) == pytest.approx(0.0)
    assert reward(-9000, -1000, 1000) == 0.0
    assert reward(0, -1000, 1000) == pytest.approx(0.6)
    with pytest.raises(ValueError, match="must beat naive"):
        reward(0, 500, 500)


def test_eval_set_is_fifteen_qualifying_tasks_balanced_over_seats():
    rows = [json.loads(x) for x in EVAL.read_text().splitlines()]
    assert len(rows) == 15 and all(r["qualifies"] for r in rows)
    assert {s: sum(r["seat"] == s for r in rows) for s in "ABC"} == {"A": 5, "B": 5, "C": 5}
    assert all(r["anchor_usd"] - r["naive_usd"] >= 1000 for r in rows)


def test_golden_references_reproduce():
    row = json.loads(EVAL.read_text().splitlines()[0])
    again = references(DataPack(), Task(row["monday"], row["seed"], row["seat"]))
    assert again["profits_usd"] == row["profits_usd"]
