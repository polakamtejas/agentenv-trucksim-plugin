# TruckSimEnv v1: the pinned plan

We work through it **one milestone at a time**. The design is in [DESIGN.md](DESIGN.md) (locked
2026-10-20). The framework facts were checked against agentenv-framework **v0.9.1336**. Precedents:
PortSimEnv (`earakely-scale/agentenv-portsim-plugin` v0.5.5) for the sim-core/plugin split, the
references and the HF dataset; OpenCiv3 (`earakely-scale/agentenv-openciv-plugin`) for multi-seat
matches.

## Status

| M | Milestone | Generic step | Size | Status |
|---|---|---|---|---|
| M0 | Setup: framework installed, `hello` + `echo-mcp` pass, model configured | 0 | S | **done** 2026-10-20: 0.9.1336 in `.venv`; `hello`, `echo-mcp` and `scripts/check_model.py` pass |
| M1 | Design lock | 1 | - | **done** (DESIGN.md) |
| M2 | Data pack: static snapshot + manifest | - | M | **done** 2026-10-20: `scripts/build_data.py`, deterministic, 6 files + manifest |
| M3 | Sim core `trucksim_core` (pure Python, no agent-env) | - | **L** | **done** 2026-10-20: 12 tests green; a 3-seat greedy week runs in 0.3 s and passes the audit |
| M4 | References, rival carriers, seed qualification | - | M | **done** 2026-10-20: rolling-a/b anchors, rivals rolling-a, 15 eval tasks, 16 tests |
| M5 | Env server with seats (MCP tools, data plane, per-seat endpoints, turn barrier) | 3 | M | **done** 2026-10-20: on agentenv-game-env (lobby, /players/<seat>/mcp); in-process + HTTP + container checks |
| M6 | Verifier + no-model smoke tasks | 4 | S | **done** 2026-10-20: `agent-env run` smoke-anchor grades 1.0, smoke-naive 0.2 (Docker, `server` provider) |
| M7 | Solver agent + task generator | 5 | M | **done** 2026-10-20: trucksim-llm (OpenAI-format via LiteLLM, a fresh conversation per turn); Haiku 5.5 scored 0.70 on ts-2020-10-05-B-s20 |
| M8 | Viewer: V0 charts → V1 board replay → V2 compare | - | M | **done** 2026-10-21: TruckSim runs (`viewer/app.html`) in PortSim's style: Overview, Explorer, run pages with a fleet chart, ghost runs and live games; built by `scripts/collect_runs.py` and served by `scripts/live.py` |
| M9 | Calibration sweep + validity audit | 6 | M | **v1 pilot done** 2026-10-20 (`results/pilot.md`, uncapped: Opus 0.926, Haiku 0.708); kept as v1 history. The full sweep waits for v1.1 (M16) |
| M10 | Plugin packaging, CLI, CI | 7 | S | **done** 2026-10-20: entry points, `agent-env trucksim setup/replay`, CI green, NOTICE; fresh git install checked. No LICENSE yet, by your choice (decide before publishing) |
| M11 | Publish to HF (only with your go-ahead: it is public) | 8 | M | **in progress** 2026-11-06: go given (your name, Apache-2.0, a train split and the extra tables); the board's sweep and the pushes are left, see M11 |
| M12 | v1.1 board: more cities and back roads; storms on the roads they cross | - | M | **done** 2026-10-21: 24 cities (14 freight, McAllen = 78% of zone 489's Mexico truck trade), 38 roads, storms on county stretches (13,768 records); v1.1 references (35/40 qualify) and tasks `ts-v11-*`; smoke 1.0/0.2 in Docker |
| M13 | v1.1 weather road closures (NOAA narratives + modeled), detours, the adverse-conditions hours exception | - | M | **done** 2026-10-21: 59 real closures (127 narratives read), modeled ones (mean 1.7/week, Uri 38), detours/waits/turn-backs, 395.1(b)(1) in clock and checker; references rebuilt (Uri: naive -$6,631, anchor -$531); smoke 1.0/0.2 in Docker |
| M14 | v1.1 fleet mix (tractors, box trucks, a van), load sizes, partials sharing a trailer, volume | - | L | **done** 2026-10-21: 8 company units/seat (VEHICLES classes, VM-1 mpg, BLS 53-3033), full/partial/small loads with fits, stops (each id twice), shipper batches (re-planner shares 8-14x/week), small/partial prices calibrated; truck forks for plan_trip (4x faster); 40/40 weeks qualify (anchor +$0.3-4.4k, naive -$2-15k); smoke 1.0/0.2 |
| M15 | v1.1 driver marketplace: roster, pay models, teams, slip-seating, owner-operators who can refuse, temps | - | L | **done** 2026-10-23: 10 units + 12 drivers/seat (own hours clocks, per-driver audit), teams (23.5 h of 24 driving), slip-seat at the hub, owner-operators (68% + FSC, $0.60/mi floor, home by Sat noon), temps; ATRI 2023 per-mile costs; $60/unit/day overhead (ATRI Table 21 margins); CP-SAT ties broken by a fixed hash so references match on macOS and Linux |
| M16 | v1.1 tools, references, tasks, viewer and docs; the pilot re-run on your go | - | M | **done** 2026-10-23 (pilot started on the user's go): driver tools, drivers table + per-driver logs, v1.1 Overview, README; 40/40 weeks qualify (anchor -$3.2k..+$7.0k, naive -$1.5k..-$11.1k); 24 model calls per turn. **v1.1 pilot**, re-run 2026-11-03 on the NOAA time fix (`results/pilot-v11.md`): Opus 5.5 0.769 (0.69-0.85), +$437/wk, 47 loads; Haiku 5.5 0.633 (0.34-0.89), -$366/wk, 41 loads. Opus leads the ordinary weeks 0.76 to 0.52 (Haiku beat the optimizer on Jan 4 2021, 1.015, and fell to 0.15 on Oct 5 2020); the storm weeks split, one each (0.78 vs 0.81). ~3.2-3.8M input tokens and ~17-19 min per model-week. The first run (before the fix: Opus 0.786, Haiku 0.704, Haiku winning both storm weeks) is history in `results/pilot-v11-pre-tzfix.md`. One run per model-week, so a week's score moves a lot between runs. The full 15-week sweep is your call |
| M17 | A 3D playthrough of a run (the moon-agent video's style), on open-source parts, recordable to an mp4 | - | M | **done** 2026-11-02: `viewer/cinema.html` (MapLibre 6 + deck.gl 9.4, OpenFreeMap tiles, Mapzen terrain), Kenney's Car Kit trucks per seat + an assembled semi (`scripts/build_models.py`), OSRM's full road shapes (`data/road_shapes.json`), a director (a shot per turn from what happens in it), the agent's feed and notes, books, race, weather; `scripts/render_cinema.py` (serve / stills / render, frame by frame) |
| M18 | A one-minute trailer in the style of GridSimEnv's launch video, cut from the pilot's real runs | - | S | **done** 2026-11-02: `viewer/trailer.html` (11 chapters, each one real moment of one run: a reefer into Dallas, the board, I-35 into Austin, Hurricane Delta on I-55, Uri's closed I-45, the agent's desk, a tire blown on I-35, the week Opus beat the optimizer, the pilot's storm flip), USGS aerial imagery + OpenStreetMap buildings, every number read from the runs; `viewer/runmodel.mjs` shared with cinema.html; `render_cinema.py trailer|trailer-stills`; road shapes to ~3 m. Found: NOAA times are 12 h early in the sim (build_data `_local_to_utc` dropped the offset's sign). **Fixed** 2026-11-03 (b208421, tests in `tests/core/test_build_data.py`): weather, closures, references and tasks rebuilt (the eval set keeps its 15 weeks; the two Feb 2021 weeks swap seeds and seats: `ts-v11-2021-02-15-A-s1`, `ts-v11-2021-02-08-B-s2`), pilot re-run, trailer and Uri playthrough re-cut from the new runs. The trailer now finds every moment in the runs (no hand-picked ticks) and its pilot line follows the scores; the 3D viewers hold a held truck still until its closure reopens (the hourly snapshots made it creep through the closure) |

Order: M0 → M2 → M3 → M4 → M5 → M6 → M7 → M9 → M10, with M8 running alongside from M3 on. Then v1.1
(decided 2026-10-21, DESIGN.md "v1.1"): M12 → M13 → M14 → M15 → M16, one at a time; M17 (the 3D playthrough, 2026-10-27); M11 (HF) after it.

## Repo layout

```
agentenv-trucksim-plugin/
├── trucksim_core/            # the sim: world, streams, HOS executor, turns, P&L, event log. No agent-env imports.
├── src/agentenv_trucksim/    # server.py (MCP env), cli.py (agent-env trucksim ...), bundles/, viewer/
├── agents/trucksim-llm/      # the solver harness as an A2A agent
├── data/                     # the static snapshot + manifest.json (every source and licence)
├── scripts/                  # build_data.py, references.py, make_tasks.py
├── Dockerfile                # env image: core + server + data
└── tests/
```

Why the core is a separate package: the env server, the reference solver, the rival carriers and the
viewer all need the same sim, and it can be tested with no Docker or model. This is the one piece of
extra structure we take on, and it is forced (PortSim does the same with FineEnvs' `berth-core`).

## Milestones

### M0. Setup

- `uv venv`, `uv pip install agentenv-framework`, `mkdir -p .agentenv && touch .agentenv/config.toml`.
- `agent-env run hello`, then `agent-env run echo-mcp` (Docker; the first run builds the gateway + state DB).
- `[model]` in `.agentenv/config.toml` (base_url + `api_key = "env:..."`).
- **Done when:** both bundles pass, and a model call through the configured endpoint works.

### M2. Data pack

- **Road graph:** ~15 cities, edges with miles and polylines. FHWA NHPN (public domain) or
  OSM/OSRM (ODbL, needs attribution); settle this here.
- **Diesel:** EIA weekly retail on-highway diesel, PADD 2 + 3, a few years. A task's week takes a
  historical slice of it, the way PortSim's weeks take a real weather week.
- **Freight flows:** FAF5 O-D tonnage between the zones on the board → lane offer intensities and
  headhaul/backhaul imbalance.
- **Costs:** per-mile components (fuel economy, driver pay, repair, insurance, tires, fixed $/day).
  Check ATRI's terms; otherwise cite published totals.
- **Rules:** HOS constants with their 49 CFR 395 citations.
- `scripts/build_data.py` writes `data/` deterministically, plus `data/manifest.json` (source,
  URL, licence and retrieval date for each file).
- **Done when:** a fresh run of the script reproduces `data/` byte for byte, and every number in
  `data/` traces to a manifest entry.

### M3. Sim core (the biggest piece)

- **World model:** City, Edge, Hub, Seat, Truck, Driver, Load, Offer.
- **Scenario from a seed:** pre-drawn streams per entity (offers, rates, delays, weather, failure
  mileages, fault-code warnings, detention, cancellations).
- **Forecasts:** a noisy view of each stream's truth, with set error levels.
- **HOS trip executor (the driver model):** given an assignment, it builds the timeline of driving,
  breaks and resets, or rejects the assignment with the rule it breaks.
- **Turn engine:** collect every seat's actions → resolve conflicts (higher bid → closer truck →
  seeded coin) → run the sim to the next turn → each seat's brief.
- **Ledger:** P&L per seat, plus terminal value (wear per mile, deadhead home, loads in progress
  pro rata).
- **Event log** (JSONL) and an **audit:** every scheduled event fired once, no rule broken.
- **Tests:**
  - HOS golden cases.
  - **CRN invariance:** two different action sequences on one seed see identical exogenous streams,
    and a truck fails at the same mileage.
  - Determinism: the same actions give a byte-identical log.
  - Conflicts.
  - The ledger balances.
- **Done when:** a scripted 3-seat week runs offline end to end, writes an event log and passes the
  audit, and the tests are green.

### M4. References, rivals and seeds

- **Naive:** a greedy dispatcher (nearest load with the best $/mile, no chaining).
- **Rolling:** an OR-Tools CP-SAT re-planner each turn, on what's announced plus the forecasts. A few
  solver configurations, as in PortSim.
- **Hindsight:** the same solver with the truth, an upper bound for context.
- **Rival carriers:** a fixed policy for the non-model seats (likely rolling with set parameters).
- **Reward:** 0.2 at naive, 1.0 at the anchor, in PortSim's curve.
- **Qualifying seeds:** rolling beats naive by a set margin in at least 3 of 4 configurations, and
  the week stays feasible.
- **Done when:** a references table over the candidate seeds, about 15 qualifying eval weeks, and a
  separate train pool.

### M5. Env server with seats

- `server.py` on `AgentEnvEnvironment`, wrapping the core. Tools are listed in DESIGN.md
  (observe / plan / act / `end_turn`).
- **Extensions:**
  - `urn:trucksim:load/v1`: seed, seats, rivals;
  - `urn:trucksim:submit-actions/v1`: for smoke tests;
  - `urn:trucksim:end-week/v1`.
- `@get_data`: per-seat reward, P&L, audit and a pointer to the event log.
- **Built on agentenv-game-env** (Edgar's, used by OpenCiv3 and Warcraft III): its lobby, player slots and
  `/players/<seat>/mcp` addresses give per-seat endpoints; agentenv-game-env pinned at `e9316f8`. Turn barrier:
  - each agent gets its own seat's MCP endpoint;
  - `end_turn` blocks until all seats have ended (30-minute tool timeout);
  - an idle seat is ended after 5 minutes, and a seat ended for more than 10% of turns scores 0.
- **Image build:** the image needs the core + data, more than one bundle folder holds, so it is
  built and registered by `agent-env trucksim setup`, as PortSim does.
- **Done when:**
  - an in-process MCP test plays a 3-seat week with the naive policy and matches the core's result;
  - the card, `data/get` and `data/reset` work over HTTP.

### M6. Verifier + smoke tasks

- `verify.py` reads `data/get`, returns the model seat's reward, and raises when the audit failed or
  no week is loaded.
- **Smoke tasks (no model):**
  - rolling's actions submitted through the extension → 1.0;
  - naive's → about 0.2;
  - an illegal action → rejected with its reason.
- **Done when:** `agent-env run <bundle> --task smoke` passes through the gateway in Docker.

### M7. Solver agent + tasks

- `agents/trucksim-llm`: a harness loop like `portsim-llm`. The model comes through
  `LITELLM_BASE_URL`, with Anthropic and OpenAI APIs, a turn cap and a cost cap.
- `scripts/make_tasks.py` writes `tasks/*.json`:
  - leaderboard tasks: one model seat plus rivals, with every hub rotation per seed;
  - showmatch tasks: all seats models.
- **Done when:** one model plays one week end to end, with its trajectory and score recorded.

### M8. Viewer

- Built only on the scenario + event log. Start from `design/viewer-mock.html` and switch it to
  owner colors.
- V0 charts → V1 board replay (real polylines; trucks animated; loads, diesel, weather; synced tool
  calls) → V2 compare (ghost fleets, profit race).
- `agent-env trucksim view <run>`.
- **Done when:** any recorded run replays, and two models compare on one seed.

### M9. Calibration sweep

- 3-4 models × 15 eval weeks, one seat rotation each.
- Check that the leaderboard spreads, rolling scores 1.0 and naive about 0.2.
- Independently recompute every env answer, the way PortSim did over six rounds, and fix what
  differs.
- **Done when:** a results board (markdown + parquet) with no recomputation mismatch.

### M10. Plugin packaging

- `pyproject.toml` entry points: `agent_env.bundles` (`trucksim`) and `agent_env.cli_plugins`
  (`trucksim`).
- CI: core tests, the server test, the smoke bundle.
- LICENSE + NOTICE + data licences.
- **Done when:** `agent-env plugin add '<pkg> @ git+...'` then `agent-env trucksim setup` and
  `agent-env run trucksim --task smoke` work from a clean machine.

### M11. Publish to HF

Modeled on AirOpsEnv and PortSimEnv (`earakely-scale/*-AgentEnv`, a dataset and a static replay Space) and on the
FineEnvs catalog (a PR to `adithya-s-k/FineEnvs` adding `07-simulation-environments/<env>/`: `project.yaml`, a README,
`results/eval*.json`; link-only is allowed, AgentEnv-only is fine).

Settled 2026-11-06 (your go: "make and push a new repo under my name", "the same license", "a train split and all
the extra"):
- **Licence:** Apache-2.0 for the code (LICENSE, pyproject), NOTICE for the data; the cards say `license: other`.
  Copyright 2026 Scale AI, as Edgar's repos (your call, 2026-11-06).
- **The task prompt's fleet fixed:** v1.1's tasks said "four trucks ... three dry vans and a reefer" (v1's fleet)
  while the rules said ten units; the pilot ran with it. make_tasks.py now checks the sentence against params.py.
- **The agent records each turn's whole conversation** (`turns[i].messages`), the transcript to train on; the old
  trajectory kept only calls with outputs cut at 6,000 characters and no system prompt or situation. The viewer's
  copy leaves them out. Needs `agent-env trucksim setup` (rebuild the agent image) before the sweep.
- **Train split:** `scripts/make_train.py` -> `data/references/v1.1-train.jsonl` and the bundle `trucksim-train`
  (every non-eval Monday 2019-2025 x 3 seats, seeds from 1001, qualifying weeks only). Done 2026-11-06: 1,044 of
  1,047 qualify (21 on storm weeks; seats A 349, B 347, C 348), 12 workers, ~95 min. Eight of them recomputed
  serially (two storm weeks, the two nearest the $1,000 line, four at random) came out identical in every field, so
  CP-SAT's 5 s limit didn't bite under the parallel load.
- **Tables and card:** `scripts/build_hf.py` (v11_tasks eval+train, v11_board, v11_episodes, v11_turns,
  v11_candidates, the eval card, the data pack; card template `hub/dataset/README.md`, hero `hub/dataset/assets/`),
  `scripts/build_space.py` (the static Space from a collect_runs site; card `hub/space/README.md`), FineEnvs manifest
  `hub/fineenvs/trucksimenv/project.yaml`.

Left, in order:
1. agentenv-hf: installed 2026-11-06 (`agent-env plugin add --yes 'agentenv-hf @ git+https://github.com/earakely-scale/
   agentenv-hf-plugin@v0.3.0'`; it moves huggingface_hub to 1.33, the tests pass). The HF account and a write token:
   the account is `tahougoreng` (2026-11-10): dataset `tahougoreng/TruckSimEnv-AgentEnv` and the Space under it; a fine-grained token with repo.write is in ~/.zshrc's `HF_TOKEN` line (it overrides `hf auth login`), checked with `hf auth whoami`.
2. The board (your go 2026-11-09: Opus 5.5, Sonnet 5.5, Haiku 5.5, with prompt caching): all 15 eval weeks per
   model on the fixed prompt and the new agent; one run a model-week; `scripts/run_board.sh MODEL ...` (the models
   one after another, four weeks at a time: the Docker VM has 4 CPUs, as in the pilot). `hf publish --instance` with
   exactly the board's runs (`--runs latest` would keep one run a task).
   - **Prompt caching** (2026-11-09, agent v12): every call marks Anthropic cache breakpoints (`cache_control`, which
     LiteLLM passes through) on the system prompt, the last message and the previous call's last message, on the
     copy it sends; the transcript stays unmarked (`tests/test_agent.py`). The usage records `cache_read_tokens` and
     `cache_write_tokens`, parts of `input_tokens`; the episodes table carries them. Smoke 1.0/0.2 on the new image.
   - **Live check 2026-11-10** (new LiteLLM key; the old one was revoked): on Haiku, Sonnet and Opus 5.5 a call
     re-sending the previous call's prompt read all 10,865 of its tokens from the cache. The proxy's `prompt_tokens`
     leaves the cached tokens out, so the agent (v13) adds them back: `input_tokens` is every input token, as in the
     pilot. The system-prompt breakpoint alone didn't cache in the check (2 tools + the rules: likely under the
     models' minimum length); within a turn, where nearly all the input is, it does.
3. Build: `collect_runs.py`, `build_hf.py`, `build_space.py`; re-render the trailer and the hero from the board.
4. Dry run: `agent-env hf publish trucksim --out DIR`; read the transcripts and the key scan.
5. GitHub: a fresh repo, no history (your call 2026-11-06): `polakamtejas/agentenv-trucksim-plugin`, one commit of
   the tree; the history stays in the private `polakamtejas/agentenv-trucksim-plugin-dev`. Going public is your
   command (`gh repo edit ... --visibility public`; the classifier blocks mine). Tag v0.1.0 after the board (the card
   pins the plugin at that tag).
6. Push: our card and tables, then `hf publish trucksim-train` and `hf publish trucksim --instance ... --requires
   ... --setup 'agent-env trucksim setup' --tag v0.1.0`; the Space.
7. FineEnvs: the README and `results/eval-v1.1.json` next to project.yaml; PR (maybe discussion #36 first).

## Choices settled along the way (default → where it's settled)

| Choice | Default | Where |
|---|---|---|
| Seats per match | 3 (three owner colors validate) | M3 |
| Trucks per seat | 4 | M3 |
| Turn length | 6 h (28 turns a week) | M3 |
| What a seat sees of rivals | hubs and the market only, not rivals' trucks | M3 |
| Forecast error levels | set in `params.py`; the reference planner doesn't read forecasts yet, so the 'following beats ignoring' check moves to M9 | M9 |
| Road geometry source | **settled:** OSM via the OSRM demo server (ODbL, attributed; only `roads.json` is ODbL). FHWA's shapefiles would need topology work to route on, for no fidelity gain | M2 |
| Cost figures | **settled:** FHWA VM-1 (6.68 mpg) and BLS OEWS ($28.71/h) are fetched; ATRI's report is behind a form, so repair, tires, lease, insurance and detention terms are marked `assumption` in `costs.json` | M2 |
| Barrier timeouts | OpenCiv3's (5 min idle, 10% rule, 30 min tool timeout) | M5 |
| GitHub repo | **decided:** `polakamtejas/agentenv-trucksim-plugin`, fresh with no history (2026-11-06); the history is in private `polakamtejas/agentenv-trucksim-plugin-dev` | made, public on your command |
| HF org | **settled** 2026-11-06: your own HF account ("a new repo under my name") | M11 |

## Framework notes (from the generic plan)

- Bundle layout: `envs/<name>/`, `agents/<name>/`, `artifacts/<name>/verify.py`, `tasks/*.json`.
  `agent-env run` builds a bundle env from its Dockerfile, with that folder alone as the build context.
- A task step with no `depends_on` waits for every step before it.
- The framework ships no solver agent: its built-in bundles are only `hello` and `echo-mcp`.
- `[agents] default_a2a_agent_id` defaults to `a2a-default`.
- Not verified: whether `agent-env hf run` builds a bundle's `envs/` image without a `--setup` command.
