# TruckSimEnv results: pilot-v11

Reward: 0.2 at the greedy operator, 1.0 at the re-planner (the anchor), above 1.0 for beating it (a ruler, not a ceiling), per week in the model's seat against re-planner rivals. 95% CI: bootstrap over weeks.

| Model | Weeks | Mean reward (95% CI) | Mean profit | Loads/week | Missed turns | Tool calls/week | Input tokens/week | Output tokens/week | Minutes/week |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-opus-5-5 | 5 of 5 | **0.769** (0.685 to 0.853) | 437 | 47.2 | 0 | 314 | 3,221,417 | 86,727 | 16.6 |
| anthropic/claude-haiku-5-5 | 5 of 5 | **0.633** (0.337 to 0.893) | -366 | 41.4 | 0 | 354 | 3,799,328 | 209,249 | 19.1 |

| Task | Kind | Seat | Naive | Anchor | anthropic/claude-haiku-5-5 | anthropic/claude-opus-5-5 |
|---|---|---|---:|---:|---:|---:|
| ts-v11-2019-10-07-A-s16 | ordinary | A | -1,568 | 6,977 | 0.383 (383) | 0.628 (3,007) |
| ts-v11-2020-10-05-B-s20 | ordinary | B | -2,830 | 3,655 | 0.154 (-3,204) | 0.863 (2,545) |
| ts-v11-2021-01-04-C-s21 | ordinary | C | -8,911 | 4,286 | 1.015 (4,530) | 0.792 (850) |
| ts-v11-2021-02-08-B-s2 | storm | B | -8,957 | 1,341 | 0.792 (-1,335) | 0.689 (-2,659) |
| ts-v11-2021-02-15-A-s1 | storm | A | -10,287 | 92 | 0.823 (-2,206) | 0.873 (-1,557) |
