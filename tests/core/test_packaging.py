"""The data pack ships whole: the env image (Dockerfile) and the wheel (pyproject force-include) carry every file
in data/ that the sim reads. A new data file left out of either fails the deploy, not a test, unless this checks it."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = sorted(p.name for p in (ROOT / "data").glob("*.json"))


def test_the_image_copies_every_data_file():
    copied = set(re.findall(r"data/([\w.-]+\.json)", (ROOT / "Dockerfile").read_text()))
    assert set(PACK) <= copied, sorted(set(PACK) - copied)


def test_the_wheel_carries_every_data_file():
    included = set(re.findall(r'"data/([\w.-]+\.json)"', (ROOT / "pyproject.toml").read_text()))
    assert set(PACK) <= included, sorted(set(PACK) - included)


def test_a_site_carries_the_3d_player(tmp_path):
    """write_site puts the 3D playthrough and the trailer next to the runs: the pages, the run model they share, their
    libraries, every seat's trucks, the road shapes."""
    from agentenv_trucksim.replay_page import write_site
    write_site(tmp_path, [], {})
    for path in ["cinema.html", "trailer.html", "runmodel.mjs", "road_shapes.json", "vendor/deck.gl-9.4.0.min.js", "vendor/deck.gl-core-shim.mjs",
                 "vendor/deck.gl-maplibre-9.4.0/index.js", "vendor/maplibre-gl-6.14.0/maplibre-gl.mjs",
                 "vendor/maplibre-gl-6.14.0/maplibre-gl-worker.mjs"]:
        assert (tmp_path / path).is_file(), path
    for seat in "ABC":
        for model in ("semi", "reefer", "box", "van"):
            assert (tmp_path / "models" / f"{model}-{seat}.glb").read_bytes()[:4] == b"glTF"


def test_setup_builds_from_the_commit_a_git_install_came_from(tmp_path, monkeypatch):
    """`agent-env trucksim setup` with no checkout around (a `uv tool install ... @ git+...@vX`, as the dataset card
    has it) builds from the repo at the commit it was installed from: Docker fetches that commit itself."""
    import click
    import pytest
    from agentenv_trucksim import cli

    monkeypatch.chdir(tmp_path)                                            # no checkout in the cwd
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "site-packages" / "agentenv_trucksim" / "cli.py"))  # nor around the package
    monkeypatch.setattr(cli, "_installed_commit", lambda: "f331079")
    root = cli._checkout(None)
    assert root == "https://github.com/polakamtejas/agentenv-trucksim-plugin.git#f331079"
    assert cli._context(root) == root
    assert cli._context(root, "agents/trucksim-llm") == f"{root}:agents/trucksim-llm"

    monkeypatch.setattr(cli, "_installed_commit", lambda: None)            # a wheel or sdist: nothing to fetch
    with pytest.raises(click.UsageError, match="pass --source"):
        cli._checkout(None)
    with pytest.raises(click.UsageError, match="is not a checkout"):
        cli._checkout(tmp_path)


def test_setup_builds_from_a_checkout_first():
    from agentenv_trucksim import cli

    assert cli._checkout(ROOT) == str(ROOT)
    assert cli._context(str(ROOT), "agents/trucksim-llm") == str(ROOT / "agents" / "trucksim-llm")
