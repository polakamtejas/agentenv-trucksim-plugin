TruckSimEnv v1.1's train split: every Monday 2019-2025 that isn't an eval week, from each of the three seats, played
and graded exactly as the eval's `ts-v11-*` tasks are (the bundle `trucksim`). Each Monday has one seed, shared by its
three seats, numbered from 1001, so no train world is an eval world. A week whose re-planner doesn't beat the greedy
operator by $1,000 is left out, as for the eval. The tasks and their references are written by scripts/make_train.py.

artifacts/trucksim-verifier is a copy of the eval bundle's verifier, so the two bundles grade alike.
