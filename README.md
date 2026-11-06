# TruckSimEnv on AgentEnv

A trucking operations environment for the [AgentEnv Framework](https://www.agentenvframework.com). Three seats, each a
small carrier with a hub (A Dallas, B Houston, C San Antonio), work one shared load board for one real week of
2019-2025 across Texas and its neighbors (24 cities, 38 highways and back roads). An agent is the operator: it takes
loads, dispatches trucks and drivers, handles breakdowns and closed roads. It never drives; the env's driver model
follows the real hours-of-service rules (49 CFR 395.3, with the adverse-conditions exception of 395.1(b)(1)).

The world (v1.1, DESIGN.md):
- **Real data, a static snapshot:** each week's diesel (EIA) and storms (NOAA Storm Events, placed on the stretch of
  road they hit), freight flows (FAF5; the Rio Grande Valley's border crossings from BTS), road closures the NOAA
  narratives report (read by hand), costs from ATRI, FHWA and BLS.
- **Each company:** 10 units (dry-van and reefer tractors, two box trucks, a cargo van, two owner-operators'
  tractors) and 12 drivers (company drivers paid by the mile, a team, a spare, local drivers, the owner-operators,
  who can turn a trip down). Loads come full, partial or small; partials can share a trailer.

Scoring: 0.2 at a greedy operator, 1.0 at a CP-SAT re-planner, above 1.0 for beating it, in the agent's seat against
re-planner rivals.

- [PLAN.md](PLAN.md): milestones and their status · [DESIGN.md](DESIGN.md): the design · [NOTICE](NOTICE): data sources

## Quickstart

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and a model endpoint (a LiteLLM proxy) for model runs.

```bash
git clone https://github.com/polakamtejas/agentenv-trucksim-plugin && cd agentenv-trucksim-plugin
uv venv --python 3.12 && uv pip install agentenv-framework -e ".[dev]"
mkdir -p .agentenv && touch .agentenv/config.toml          # local stores; add [model] for model runs
.venv/bin/agent-env trucksim setup                          # builds and registers the env and the operator agent
.venv/bin/agent-env run trucksim --task smoke-anchor        # no model: grades 1.0 (smoke-naive grades 0.2)
```

Play a week with a model (the 15 eval tasks are `ts-v11-<monday>-<seat>-s<seed>`; `showmatch` seats three models;
the train split's 1,044 tasks, every other Monday of 2019-2025 from each seat, are the bundle `trucksim-train`):

```toml
# .agentenv/config.toml
[model]
base_url = "https://<your-litellm-proxy>/v1"
api_key  = "env:LITELLM_API_KEY"
```

```bash
.venv/bin/agent-env run trucksim --task ts-v11-2020-10-05-B-s20 --model anthropic/claude-haiku-5-5
.venv/bin/python scripts/collect_runs.py mine run.log       # results/mine.md and the runs app (results/site/mine/)
```

On macOS, wrap long jobs (`make_references.py`, the tests) in `caffeinate -i`: a background job can otherwise run at a
fraction of a CPU.

Replay a week without a model, played by a reference policy:

```bash
.venv/bin/agent-env trucksim replay sim 2021-02-15 2 --seat B --policy rolling-a -o uri.html --open
```

Watch a run in 3D: every site `collect_runs.py` writes has a 3D playthrough of each model run (the run page's
**▶ 3D playthrough**). The week plays on a real map with Kenney's trucks, the storms and closures that hit the roads,
a camera that follows the agent's trucks, and the agent's turns (its tool calls and its own notes) on screen. It can be
recorded to an mp4 (1080p, 30 fps), frame by frame, so the same run gives the same video:

```bash
uv pip install -e ".[video]"                               # Playwright and Pillow; uses your Google Chrome and ffmpeg
.venv/bin/python scripts/render_cinema.py serve results/site/pilot-v11                 # then open /cinema.html?run=<id>
.venv/bin/python scripts/render_cinema.py render results/site/pilot-v11 <run id>       # results/videos/<run id>.mp4
```

The trailer (`trailer.html`, about a minute) is cut from the pilot's runs the same way. Each chapter is one real
moment of one run, found in the runs rather than picked by hand: a loaded truck into a downtown at dusk, Hurricane
Delta's rain on a road, trucks held at a closure in Winter Storm Uri, the agent's first turn that dispatches, a
breakdown, Opus's best week in dollars, and the pilot's scores. It is drawn on USGS aerial imagery with
OpenStreetMap's buildings, and every number and claim on it is read from the runs (a chapter that cannot find its
moment, or a claim that stops holding, stops the trailer with an error). It needs a pilot site with the weeks of Delta
(2020-10-05) and Uri (2021-02-15):

```bash
.venv/bin/python scripts/render_cinema.py trailer-stills results/site/pilot-v11 3 10 22   # PNGs of moments, to check a look
.venv/bin/python scripts/render_cinema.py trailer results/site/pilot-v11                  # results/videos/trucksim-trailer.mp4
```

The pages draw OpenFreeMap's tiles, Mapzen's terrain and USGS's imagery at view time, so they need the internet.

## Layout

| Path | What |
|---|---|
| `trucksim_core/` | the sim: scenario, driver model and hours of service, turns, ledger, audit, references (no agent-env imports) |
| `src/agentenv_trucksim/` | the env server (agentenv-game-env), the CLI, the bundles (`trucksim` the eval, `trucksim-train`), the viewers (`viewer/app.html` 2D, `viewer/cinema.html` 3D, `viewer/trailer.html` the trailer, sharing `viewer/runmodel.mjs`; vendored deck.gl and MapLibre, the trucks from `scripts/build_models.py`) |
| `agents/trucksim-llm/` | the operator agent: one model, one seat, a fresh conversation per turn |
| `data/` | the static data pack (`scripts/build_data.py`) and the references by world (`scripts/make_references.py` the eval's, `scripts/make_train.py` the train split's; `v1-*` is the v1 pilot's history) |
| `tests/` | core, references (with a golden check) and server tests |
| `hub/` | the Hugging Face dataset and Space cards (`scripts/build_hf.py`, `scripts/build_space.py`) and the FineEnvs manifest |

## Licence

The code is Apache-2.0 ([LICENSE](LICENSE)). The data pack keeps its sources' terms; [NOTICE](NOTICE) lists them.
