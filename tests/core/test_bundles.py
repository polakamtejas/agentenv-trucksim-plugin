"""The two bundles: `trucksim` (the eval weeks, the smoke tasks, the showmatch) and `trucksim-train` (the train split).
They must grade alike, hold no week in common, and each task must carry its own reference row's numbers, or a run is
graded against the wrong ruler without any error."""

import json
from pathlib import Path

from trucksim_core.params import WORLD

ROOT = Path(__file__).resolve().parents[2]
BUNDLES = ROOT / "src" / "agentenv_trucksim" / "bundles"
REFS = ROOT / "data" / "references"


def rows(name: str) -> dict[str, dict]:
    return {r["task"]: r for r in (json.loads(x) for x in (REFS / f"{WORLD}-{name}.jsonl").read_text().splitlines())}


def lobby(task_file: Path) -> dict:
    return next(s for s in json.loads(task_file.read_text()) if s["type"] == "open_lobby")["game_settings"]


def test_both_bundles_grade_with_the_same_verifier():
    path = Path("artifacts") / "trucksim-verifier" / "verify.py"
    assert (BUNDLES / "trucksim" / path).read_bytes() == (BUNDLES / "trucksim-train" / path).read_bytes()


def test_each_bundle_has_a_task_per_reference_row_and_carries_its_numbers():
    for bundle, split in (("trucksim", "eval"), ("trucksim-train", "train")):
        refs = rows(split)
        files = {p.stem: p for p in (BUNDLES / bundle / "tasks").glob("ts-*.json")}
        assert set(files) == set(refs), (bundle, sorted(set(files) ^ set(refs))[:5])
        for task, path in files.items():
            settings, r = lobby(path), refs[task]
            assert (settings["monday"], settings["seed"], settings["naive_usd"], settings["anchor_usd"]) == \
                   (r["monday"], r["seed"], r["naive_usd"], r["anchor_usd"]), task


def test_no_train_week_is_an_eval_week_and_every_train_task_qualifies():
    eval_weeks = {r["monday"] for r in rows("eval").values()}
    train = rows("train").values()
    assert not eval_weeks & {r["monday"] for r in train}
    assert all(r["qualifies"] and r["anchor_usd"] > r["naive_usd"] for r in train)
    assert min(r["seed"] for r in train) > max(r["seed"] for r in rows("candidates").values())
