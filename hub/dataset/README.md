---
license: other
license_name: odbl-us-public-domain-apache-2.0
license_link: https://github.com/{{GITHUB}}/blob/{{VERSION}}/NOTICE
pretty_name: 'TruckSimEnv v1.1: a trucking carrier through a real week'
language:
- en
task_categories:
- reinforcement-learning
- text-generation
size_categories:
- 1K<n<10K
tags:
- agentenv
- rl-environment
- agents
- multi-agent
- trucking
- logistics
- dispatch
- scheduling
- operations-research
- hours-of-service
- mcp
- real-world-data
- weather
- simulation
agentenv:
  default: trucksim
  bundles:
    trucksim:
      plugins:
      - agentenv-trucksim @ git+https://github.com/{{GITHUB}}@{{VERSION}}
      setup: agent-env trucksim setup
    trucksim-train:
      plugins:
      - agentenv-trucksim @ git+https://github.com/{{GITHUB}}@{{VERSION}}
      setup: agent-env trucksim setup
configs:
- config_name: v11_tasks
  default: true
  data_files:
  - split: eval
    path: tasks/v11-eval.parquet
  - split: train
    path: tasks/v11-train.parquet
- config_name: v11_board
  data_files:
  - split: eval
    path: results/v11-board.parquet
- config_name: v11_episodes
  data_files:
  - split: eval
    path: episodes/v11-eval.parquet
- config_name: v11_turns
  data_files:
  - split: eval
    path: turns/v11-eval.parquet
- config_name: v11_candidates
  data_files:
  - split: eval
    path: references/v11-candidates.parquet
---

<div align="center">

<a href="https://huggingface.co/spaces/{{HF_SPACE}}"><img src="https://huggingface.co/datasets/{{HF_REPO}}/resolve/{{VERSION}}/assets/trucksim-hero.webp" alt="A TruckSimEnv run in 3D: the agent's trucks on the Texas Triangle's highways on USGS aerial imagery, with the week's storms and closed roads" width="100%"></a>

<h1>TruckSimEnv v1.1: a trucking carrier through a real week</h1>

<h3>Run a small Texas carrier's dispatch desk for one real week of 2019-2025, its storms and closed roads included</h3>

<p>An AgentEnv environment built from public freight, fuel, weather and hours-of-service records, graded with no judge
by the week's profit, against a greedy operator and a CP-SAT re-planner playing the same week.</p>

<a href="https://huggingface.co/spaces/{{HF_SPACE}}"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Space-Replays%20in%203D-FFD21E?style=for-the-badge&labelColor=1a1a1a" alt="Replays in 3D" height="32"></a>
<a href="https://github.com/{{GITHUB}}"><img src="https://img.shields.io/badge/GitHub-{{GITHUB_BADGE}}-181717?style=for-the-badge&logo=github&labelColor=1a1a1a" alt="Code on GitHub" height="32"></a>
<a href="https://www.agentenvframework.com"><img src="https://img.shields.io/badge/framework-AgentEnv-6f42c1?style=for-the-badge&labelColor=1a1a1a" alt="AgentEnv Framework" height="32"></a>

</div>

---

## The task

Three carriers share one load board for one week, Monday 00:00 to Monday 00:00 Central time, on the Texas Triangle and
its neighbors: 24 cities and 38 highways and back roads. Each carrier has a hub (A Dallas, B Houston, C San Antonio),
ten units (four company tractors with dry vans and one with a reefer, two 26' box trucks, a cargo van, and two
owner-operators' tractors leased on) and twelve drivers (company drivers paid by the mile, two of them a team and one
a spare at the hub, local drivers paid by the hour, and the two owner-operators, who are paid a share of each load and
turn down a trip that doesn't pay them enough or wouldn't get them home by Saturday noon).

The agent is one carrier's operator. It never drives: it takes loads off the board, plans and dispatches trips,
moves drivers between units at the hub, calls temps, decides what to do about breakdowns, and routes around closed
roads. The env's driver model drives, loads and rests under the federal hours-of-service rules (49 CFR 395.3, with the
adverse-driving-conditions exception of 395.1(b)(1)). The other two carriers are played by the re-planner.

The week runs in 28 turns of 6 hours. While a turn is open the clock is stopped; every carrier acts, then ends its
turn, and the world runs 6 hours: trucks drive, wait at docks, break down, storms move in and close roads, and new
offers post. Two carriers claiming one load: the one with the truck closer to the pickup gets it. Each turn is a fresh
conversation: the agent sees the rules, the situation and the notes it left itself last turn.

| | |
|---|---|
| Tools ({{TOOLS_N}}, MCP) | {{TOOLS}} |
| Per turn | up to 24 model calls; a turn that runs out is ended for the agent, with the notes it had |
| Per week | 28 turns; a seat whose turns the env had to end more than 10% of the time scores 0 |

## The weeks

Every week is a real one: that week's diesel price (EIA), the storms NOAA's Storm Events database recorded, placed on
the stretch of road they hit, the road closures NOAA's narratives report (read by hand) and closures modeled from a
storm's type and severity, and the freight flows of FAF5 behind
the load board. The seed draws the offers (each with its hidden detention at the docks), the
mileage at which each truck's parts fail, the forecasts' errors and the closures a storm causes beyond the reported
ones, each from its own stream (common random numbers), so two models on one task are dealt the same week and differ
only by what they chose.

- **eval ({{EVAL_TASKS}} tasks):** {{EVAL_STORMS}} storm weeks (the most severe of 2019-2025 on the board's roads,
  Winter Storm Uri among them) and {{EVAL_ORDINARY}} ordinary weeks a season apart, each with its own seed and a seat
  in rotation, five tasks a seat. They were picked from {{CANDIDATES}} candidates before any model ran
  (`v11_candidates`): the first that qualify, a third of them storm weeks.
- **train ({{TRAIN_TASKS}} tasks):** every other Monday of 2019-2025, from each of the three seats, one seed per Monday
  shared by its three seats, seeds from 1001 so no train world is an eval world. Graded exactly as the eval.

A week qualifies when the re-planner beats the greedy operator by at least $1,000, so the ruler below is worth
playing for.

## Grading

The week's profit P in the agent's seat, after the end-of-week charges (each truck away from its hub is charged the
trip home, a load on board is credited for the share of its miles driven, an unrepaired truck is charged its repair),
is placed on a ruler made of two references played on the same week, against the same rivals:

- **naive** (0.2): a greedy operator: each idle truck takes the feasible load with the best revenue per mile, empty
  miles included, one load at a time, and each breakdown gets the cheaper repair;
- **anchor** (1.0): the better of two configurations of a rolling CP-SAT re-planner, which re-plans every turn.

reward = max(0, 0.2 + 0.8 (P − naive) / (anchor − naive)). The anchor is a ruler, not a ceiling: there is no known
optimum for this game, and a model that beats the re-planner scores above 1.0, by the same measure. A run is scored
only when the week finishes and passes the env's audit (every move legal, every hour of service within the rules).
No judge.

## Results

<!-- board -->
<!-- /board -->

## Watch a run

Every run on the board replays in the [Space](https://huggingface.co/spaces/{{HF_SPACE}}): the week on a map of the
Texas Triangle, turn by turn, with each truck's loads, rests, breakdowns and the storms and closures it met, the
agent's tool calls and its own notes, and a fleet chart of the week's money. **▶ 3D playthrough** plays the run on
USGS aerial imagery with OpenStreetMap's buildings, the camera following the agent's trucks.

## Tables

All parquet. One config per table, a split per split of the tasks.

| Config | What |
|---|---|
| `v11_tasks` (default) | one row per task, `eval` and `train`: its Monday, seed and seat, the week's kind and storm severity, every reference's profit (naive, the re-planner's two configurations, doing nothing) and the anchor, the operator's system prompt and the task prompt, and the lobby's `game_settings` |
| `v11_board` | one row per model: mean reward with its 95% bootstrap interval over the weeks, its ordinary and storm means, mean profit, loads, missed turns, tokens and minutes a week |
| `v11_episodes` | one row per run on the board: reward, profit and its whole ledger (`ledger_usd`: linehaul, fuel surcharge, detention pay, fuel, pay, owner-operators' settlements, repairs, penalties, the end-of-week charges), loads delivered, missed turns, tool and model calls, tokens (and how many of the input tokens were read from and written to the prompt cache) and minutes |
| `v11_turns` | one row per turn of each run: the clock, who ended the turn, the agent's notes, its calls, and the turn's whole conversation as chat `messages` (system prompt, the situation, every tool call and its full output), tool-call arguments as objects, as TRL's `SFTTrainer` reads them |
| `v11_candidates` | the candidate weeks the eval was picked from, with their references and whether each qualified |

And agentenv-hf's, as `agent-env hf publish` writes them: `trucksim_tasks` and `trucksim_episodes`, and
`raw/trucksim.jsonl` with each run's record and native trajectory; `trucksim-train_tasks` for the train bundle.
`bundles/trucksim/` and `bundles/trucksim-train/` are the two bundles `agent-env hf run` plays, the eval's the default.
`data/` is the data pack the sim reads, `results/eval-v1.1.json` the eval card.

```python
from datasets import load_dataset

tasks = load_dataset("{{HF_REPO}}", "v11_tasks", split="train")
turns = load_dataset("{{HF_REPO}}", "v11_turns", split="eval")
turns[0]["messages"]       # one turn's conversation, tool-call arguments as objects
board = load_dataset("{{HF_REPO}}", "v11_board", split="eval").to_pandas()
```

## Run it yourself

You need Docker, [uv](https://docs.astral.sh/uv/) and git. Install the plugin, build the env and the operator agent,
check the wiring with a week no model plays, then play a week straight from this dataset with a model on an
OpenAI-compatible endpoint (a LiteLLM proxy, set as `[model]` in `.agentenv/config.toml`):

```bash
uv tool install agentenv-framework \
    --with "agentenv-trucksim @ git+https://github.com/{{GITHUB}}@{{VERSION}}" \
    --with "agentenv-hf @ git+https://github.com/earakely-scale/agentenv-hf-plugin@v0.3.0"
agent-env trucksim setup        # builds the env and agent images from the plugin's commit on GitHub, a few minutes
agent-env hf run {{HF_REPO}}@{{VERSION}} --bundle trucksim --task smoke-anchor     # no model: grades 1.0
agent-env hf run {{HF_REPO}}@{{VERSION}} --bundle trucksim --task {{EXAMPLE_TASK}} \
    --model anthropic/claude-opus-5-5
```

`smoke-anchor` and `smoke-naive` play the re-planner and the greedy operator in the agent's seat and must grade 1.0
and 0.2. `showmatch` seats three models against each other. A train task runs the same way with `--bundle
trucksim-train`. `hf run` downloads this card and `bundles/` at the tag's commit, checks that the plugin the card's
`agentenv` table names is installed, shows what it will run and asks first.

## Licence and attribution

The code is Apache-2.0 ([LICENSE](LICENSE)). The data pack keeps its sources' terms ([NOTICE](NOTICE)):

- road distances and shapes from OpenStreetMap via the OSRM demo server, © OpenStreetMap contributors, ODbL 1.0
  (`data/roads.json` and `data/road_shapes.json` are derived databases under the ODbL); city points from the U.S.
  Census Bureau Gazetteer Files (public domain);
- diesel prices from the U.S. Energy Information Administration; freight flows from the Freight Analysis Framework 5
  (FHWA, BTS, Oak Ridge National Laboratory); storms and their narratives from NOAA NCEI's Storm Events Database;
  hours of service from 49 CFR 395 (eCFR); costs from FHWA Highway Statistics and BLS OEWS (U.S. Government works,
  public domain), and ATRI's published operating-cost totals, cited; entries marked "assumption" in `data/costs.json`
  have no fetched source.

The viewers draw, at view time, OpenFreeMap's tiles (© OpenMapTiles, © OpenStreetMap contributors, ODbL), Mapzen's
terrain tiles on AWS Open Data and USGS The National Map's imagery (public domain); the trucks are Kenney's Car Kit
(CC0). None of the agencies, data providers or projects named here is affiliated with this project or endorses it.
