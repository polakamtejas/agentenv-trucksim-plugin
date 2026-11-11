<div align="center">

<a href="https://huggingface.co/spaces/tahougoreng/TruckSimEnv-AgentEnv"><img src="./assets/banner.jpg" alt="TruckSimEnv: the Dallas skyline and its interchanges in 3D on USGS aerial imagery with OpenStreetMap's buildings, from the trailer of the board's runs" width="100%"></a>

<h1>TruckSimEnv v1.1: a trucking carrier through a real week</h1>

<h3>Run a small Texas carrier's dispatch desk for one real week of 2019-2025, its storms and closed roads included</h3>

<p>An AgentEnv environment built from public freight, fuel, weather and hours-of-service records, graded with no judge
by the week's profit, against a greedy operator and a CP-SAT re-planner playing the same week.</p>

<a href="https://huggingface.co/spaces/tahougoreng/TruckSimEnv-AgentEnv"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Space-Replays%20in%203D-FFD21E?style=for-the-badge&labelColor=1a1a1a" alt="Replays in 3D" height="32"></a>
<a href="https://huggingface.co/datasets/tahougoreng/TruckSimEnv-AgentEnv"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-Weeks%20%26%20runs-4F46E5?style=for-the-badge&labelColor=1a1a1a" alt="Dataset" height="32"></a>
<a href="https://github.com/scaleapi/agentenv-framework"><img src="https://img.shields.io/badge/framework-AgentEnv-3B82F6?style=for-the-badge&labelColor=1a1a1a" alt="AgentEnv" height="32"></a>
<a href="https://github.com/polakamtejas/agentenv-trucksim-plugin"><img src="https://img.shields.io/badge/GitHub-agentenv--trucksim%20plugin-181717?style=for-the-badge&logo=github&labelColor=1a1a1a" alt="The agentenv-trucksim plugin on GitHub" height="32"></a>

<sub>The banner is a frame of the board's trailer: Dallas on USGS The National Map's imagery (public domain) with
OpenStreetMap's buildings and roads (© OpenStreetMap contributors, ODbL) through OpenFreeMap.</sub>

</div>

---

This folder is link-only: the env, the data pack and the runs live in the
[agentenv-trucksim plugin](https://github.com/polakamtejas/agentenv-trucksim-plugin) and its
[dataset](https://huggingface.co/datasets/tahougoreng/TruckSimEnv-AgentEnv). It holds the manifest, the eval card
([results/eval-v1.1.json](results/eval-v1.1.json)) and the board ([results/board.md](results/board.md)).

## The task

Three carriers share one load board for one week, Monday 00:00 to Monday 00:00 Central time, on the Texas Triangle and
its neighbors: 24 cities and 38 highways and back roads. Each carrier has a hub (Dallas, Houston or San Antonio), ten
units (tractors with dry vans and a reefer, box trucks, a cargo van and two owner-operators' tractors leased on) and
twelve drivers: company drivers paid by the mile, a team, a spare at the hub, local drivers paid by the hour, and the
two owner-operators, who are paid a share of each load and turn down a trip that doesn't pay them enough.

The agent is one carrier's operator. It never drives: it takes loads off the board, plans and dispatches trips, moves
drivers between units, calls temps, decides what to do about breakdowns and routes around closed roads. The env's
driver model drives, loads and rests under the federal hours-of-service rules (49 CFR 395.3, with the
adverse-driving-conditions exception). The other two carriers are played by the re-planner.

The week runs in 28 turns of 6 hours. While a turn is open the clock is stopped; every carrier acts and ends its turn,
then the world runs 6 hours: trucks drive, wait at docks, break down, storms move in and close roads, and new offers
post. Each turn is a fresh conversation of up to 24 model calls over 16 MCP tools; the agent sees the rules, the
situation and the notes it left itself last turn.

## The weeks

Every week is a real one: that week's diesel price (EIA), the storms NOAA's Storm Events database recorded, placed on
the stretch of road they hit, the road closures their narratives report (read by hand) plus closures modeled from a
storm's type and severity, and FAF5's freight flows behind the load board. The seed draws the offers, the docks'
hidden detention, the parts' failures and the forecasts' errors, each from its own stream, so two models on one task
are dealt the same week.

- **eval (15 weeks):** 5 of the most severe storm weeks of 2019-2025 on the board's roads (Winter Storm Uri among
  them) and 10 ordinary weeks a season apart, a seat in rotation, picked from 40 candidates before any model ran.
- **train (1,044 tasks):** every other Monday of 2019-2025 from each of the three seats, graded exactly as the eval.

## Grading

The week's profit P in the agent's seat, after the end-of-week charges, is placed on a ruler of two references played
on the same week against the same rivals: a greedy operator (0.2) and the better of two configurations of a rolling
CP-SAT re-planner (1.0):

reward = max(0, 0.2 + 0.8 (P − naive) / (anchor − naive))

The anchor is a ruler, not a ceiling: there is no known optimum for this game, and a model that beats the re-planner
scores above 1.0. A run is scored only when the week finishes and passes the env's audit (every offer, storm and
closure as drawn, every duty log within the rules, the ledger adding up). No judge.

## The board

On 10 November 2026 Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5 each played the 15 eval weeks once, through the plugin's
`trucksim-llm` agent with prompt caching: 45 runs, every one scored, no missed turns.

| Model | Mean reward (95% CI) | Ordinary weeks | Storm weeks | Mean profit a week |
|---|---|---:|---:|---:|
| Claude Opus 5.5 | **0.967** (0.797 to 1.159) | 0.854 | 1.195 | $1,649 |
| Claude Sonnet 5.5 | **0.760** (0.644 to 0.898) | 0.640 | 1.000 | −$308 |
| Claude Haiku 5.5 | **0.721** (0.578 to 0.889) | 0.586 | 0.992 | −$894 |
| Re-planner (the anchor) | 1.000 | | | $2,642 |
| Greedy operator (naive) | 0.200 | | | −$6,170 |

Opus beats the re-planner on 5 of the 15 weeks. Every model scores higher in the storm weeks than in the ordinary
ones, which says more about the ruler than about the storms: in a storm week the re-planner's lead over the greedy
operator is smaller ($7,536 on average, against $9,450 in an ordinary week), so each dollar a model saves counts for
more. The week of 30 January 2023 costs even the re-planner $7,061.
[results/board.md](results/board.md) has every week; the dataset's `v11_episodes` and `v11_turns` have every run's
ledger and every turn's conversation.

## Start here

| You want to… | Open |
|---|---|
| Watch any run, on a map or in 3D | [tahougoreng/TruckSimEnv-AgentEnv](https://huggingface.co/spaces/tahougoreng/TruckSimEnv-AgentEnv) |
| Get the weeks, the train split and every recorded run | [datasets/tahougoreng/TruckSimEnv-AgentEnv](https://huggingface.co/datasets/tahougoreng/TruckSimEnv-AgentEnv) |
| Read the sim, the references and the grade | the plugin's [DESIGN.md](https://github.com/polakamtejas/agentenv-trucksim-plugin/blob/v0.1.1/DESIGN.md) |
| Run weeks, sweep models or publish to the Hub | [agentenv-trucksim plugin](https://github.com/polakamtejas/agentenv-trucksim-plugin) |

## Run it

You need Docker, [uv](https://docs.astral.sh/uv/) and git:

```bash
uv tool install agentenv-framework \
    --with "agentenv-trucksim @ git+https://github.com/polakamtejas/agentenv-trucksim-plugin@v0.1.1" \
    --with "agentenv-hf @ git+https://github.com/earakely-scale/agentenv-hf-plugin@v0.3.0"
agent-env trucksim setup        # builds the env and agent images from the plugin's commit on GitHub, a few minutes
agent-env hf run tahougoreng/TruckSimEnv-AgentEnv@v0.1.1 --bundle trucksim --task smoke-anchor     # no model: grades 1.0
agent-env hf run tahougoreng/TruckSimEnv-AgentEnv@v0.1.1 --bundle trucksim --task ts-v11-2019-01-07-A-s13 \
    --model anthropic/claude-opus-5-5   # an OpenAI-compatible endpoint, as [model] in .agentenv/config.toml
```

## Data and licence

Road distances and shapes come from OpenStreetMap via the OSRM demo server (© OpenStreetMap contributors, ODbL 1.0);
city points from the U.S. Census Bureau Gazetteer Files; diesel prices from the EIA; freight flows from the Freight
Analysis Framework 5 (FHWA, BTS, Oak Ridge National Laboratory); storms and their narratives from NOAA NCEI's Storm
Events Database; hours of service from 49 CFR 395; costs from FHWA Highway Statistics and BLS OEWS (U.S. Government
works, public domain), and ATRI's published operating-cost totals, cited. None of it is copied here; the plugin's
[NOTICE](https://github.com/polakamtejas/agentenv-trucksim-plugin/blob/v0.1.1/NOTICE) lists each source and its terms,
and the dataset is published under the terms on its card. The plugin's code is Apache-2.0. None of the agencies, data
providers or projects named here is affiliated with this project or endorses it.

Contributed from the agentenv-trucksim plugin (https://github.com/polakamtejas/agentenv-trucksim-plugin) by Tejas
Polakam (Scale AI), built on AgentEnv (https://github.com/scaleapi/agentenv-framework).
