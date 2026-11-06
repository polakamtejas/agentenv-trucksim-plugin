TruckSimEnv v1.1, "Dispatch week": three seats, each a small carrier with a hub (A Dallas, B Houston, C San Antonio),
play one real week of 2019-2025 on the Texas Triangle. The env is `trucksim`, registered from this repo's Dockerfile.

- The `ts-v11-*` tasks are the 15 eval weeks: the model plays one seat against two re-planner rivals, and
  `trucksim-verifier` scores its profit 0.2 at the greedy operator and 1.0 at the re-planner (more above it).
- `smoke-anchor` and `smoke-naive` need no model: the env's autoplay extension plays the smoke week's seat
  (`ts-v11-2019-01-07-A-s13`, seat A) with the re-planner (rolling-a, that week's anchor) or the greedy operator, and
  `trucksim-verifier` must score 1.0 and 0.2.
- `showmatch` seats three models against each other, graded by the most profit.

The train split is the bundle `trucksim-train`.

Road data (c) OpenStreetMap contributors (ODbL); diesel from EIA, freight flows from FAF5 (FHWA/BTS), weather from NOAA
Storm Events, hours of service from 49 CFR 395.3.
