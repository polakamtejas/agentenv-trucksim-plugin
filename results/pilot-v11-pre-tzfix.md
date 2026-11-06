# TruckSimEnv results: pilot-v11-pre-tzfix

> History: this pilot ran before the NOAA time fix (b208421), when every storm report reached the sim 12 hours early. Superseded by `pilot-v11.md`.

Reward: 0.2 at the greedy operator, 1.0 at the re-planner (the anchor), above 1.0 for beating it (a ruler, not a ceiling), per week in the model's seat against re-planner rivals. 95% CI: bootstrap over weeks.

| Model | Weeks | Mean reward (95% CI) | Mean profit | Loads/week | Missed turns | Tool calls/week | Input tokens/week | Output tokens/week | Minutes/week |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-opus-5-5 | 5 of 5 | **0.786** (0.657 to 0.932) | 833 | 49.0 | 0 | 317 | 3,329,365 | 90,947 | 18.7 |
| anthropic/claude-haiku-5-5 | 5 of 5 | **0.704** (0.528 to 0.866) | -338 | 40.6 | 0 | 361 | 3,895,234 | 188,182 | 17.7 |

| Task | Kind | Seat | Naive | Anchor | anthropic/claude-haiku-5-5 | anthropic/claude-opus-5-5 |
|---|---|---|---:|---:|---:|---:|
| ts-v11-2019-10-07-A-s16 | ordinary | A | -1,568 | 6,977 | 0.391 (470) | 0.616 (2,876) |
| ts-v11-2020-10-05-B-s20 | ordinary | B | -2,830 | 3,505 | 0.620 (497) | 0.800 (1,922) |
| ts-v11-2021-01-04-C-s21 | ordinary | C | -9,039 | 3,573 | 0.688 (-1,345) | 1.074 (4,742) |
| ts-v11-2021-02-08-A-s1 | storm | A | -7,133 | 429 | 0.917 (-352) | 0.626 (-3,104) |
| ts-v11-2021-02-15-B-s2 | storm | B | -11,108 | 439 | 0.903 (-962) | 0.812 (-2,272) |
