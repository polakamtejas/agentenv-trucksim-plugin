"""Build a self-contained replay page. Same as `agent-env trucksim replay`, for a checkout without the plugin installed.

  .venv/bin/python scripts/build_replay.py sim MONDAY SEED [--seat A --policy rolling-a] -o out.html
  .venv/bin/python scripts/build_replay.py files EVENTS.jsonl RESULT.json [--trajectory SEAT=traj.json] -o out.html"""

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "src")]

from agentenv_trucksim.cli import replay  # noqa: E402

if __name__ == "__main__":
    replay(prog_name="build_replay.py")
