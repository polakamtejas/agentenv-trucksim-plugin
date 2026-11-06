---
title: TruckSimEnv v1.1
emoji: 🚚
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
pinned: true
license: other
license_name: odbl-us-public-domain-apache-2.0
license_link: https://github.com/{{GITHUB}}/blob/{{VERSION}}/NOTICE
short_description: Run a Texas trucking carrier through a real week
tags:
- agentenv
- rl-environment
- agents
- multi-agent
- trucking
- logistics
- dispatch
- weather
datasets:
- {{HF_REPO}}
---

# TruckSimEnv v1.1: a trucking carrier through a real week

Every run on [the board]({{HF_LINK}}) replays here. A model runs one small carrier's dispatch desk on the Texas
Triangle for one real week of 2019-2025, against two re-planner rivals on the same load board: it takes loads,
dispatches ten units and twelve drivers under the hours-of-service rules, and handles breakdowns, storms and closed
roads.

- **Overview** ranks the models on the board; **Explorer** lists every run.
- A run's page plays the week turn by turn on the map, with each truck's loads, rests and breakdowns, the storms and
  closures, the agent's tool calls and its own notes, and a fleet chart of the week's money. **Compare with** draws a
  second model's run of the same week over it.
- **▶ 3D playthrough** plays the run on USGS aerial imagery with OpenStreetMap's buildings. `trailer.html` is a
  one-minute trailer cut from the runs.

The tasks, the grading, the tables and how to run a week yourself are in the [dataset]({{HF_LINK}}); the code is on
[GitHub](https://github.com/{{GITHUB}}). The pages draw OpenFreeMap's tiles (© OpenMapTiles, © OpenStreetMap
contributors, ODbL), Mapzen's terrain on AWS Open Data and USGS The National Map's imagery at view time; the trucks are
Kenney's Car Kit (CC0); deck.gl (MIT) and MapLibre GL JS (BSD-3-Clause) are vendored with their licences.
