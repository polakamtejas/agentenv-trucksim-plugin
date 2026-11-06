# TruckSimEnv results: pilot

Reward: 0.2 at the greedy operator, 1.0 at the re-planner (the anchor), above 1.0 for beating it (a ruler, not a ceiling), per week in the model's seat against re-planner rivals. 95% CI: bootstrap over weeks.

| Model | Weeks | Mean reward (95% CI) | Mean profit | Loads/week | Missed turns | Tool calls/week | Input tokens/week | Output tokens/week | Minutes/week |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-opus-5-5 | 5 of 5 | **0.926** (0.493 to 1.360) | 376 | 17.8 | 0 | 183 | 991,137 | 43,869 | 9.0 |
| anthropic/claude-haiku-5-5 | 5 of 5 | **0.708** (0.403 to 0.919) | -715 | 16.2 | 0 | 223 | 1,115,439 | 76,393 | 7.2 |

| Task | Kind | Seat | Naive | Anchor | anthropic/claude-haiku-5-5 | anthropic/claude-opus-5-5 |
|---|---|---|---:|---:|---:|---:|
| ts-2019-10-07-A-s16 | ordinary | A | -1,013 | 2,434 | 0.156 (-1,201) | 0.538 (444) |
| ts-2020-10-05-B-s20 | ordinary | B | -1,669 | 1,368 | 0.820 (684) | 0.539 (-382) |
| ts-2021-02-08-A-s1 | storm | A | -4,510 | -1,263 | 0.676 (-2,578) | 0.425 (-3,598) |
| ts-2021-02-15-B-s2 | storm | B | -3,269 | -600 | 1.015 (-549) | 1.564 (1,281) |
| ts-2023-04-03-C-s30 | ordinary | C | -3,871 | 819 | 0.872 (68) | 1.565 (4,134) |
