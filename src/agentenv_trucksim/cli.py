"""`agent-env trucksim`: build and register the env and the operator agent, and build replay pages."""

import json
import subprocess
import webbrowser
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import click

ENV_ID = "trucksim"
ENV_IMAGE = "agentenv-trucksim-env"
AGENT_ID = "trucksim-llm"
AGENT_IMAGE = "agentenv-trucksim-llm"
DEFAULT_MODEL = "anthropic/claude-haiku-5-5"
REPO = "https://github.com/polakamtejas/agentenv-trucksim-plugin"


def _installed_commit() -> str | None:
    """The git commit a `pip install git+...` or a uv git source installed this package from (PEP 610's
    direct_url.json), or None for an install that didn't come from git."""
    try:
        direct = json.loads(distribution("agentenv-trucksim").read_text("direct_url.json") or "{}")
    except PackageNotFoundError:
        return None
    return direct.get("vcs_info", {}).get("commit_id")


def _checkout(source: Path | None) -> str:
    """What to build from: --source, else the checkout an editable install runs from, else the cwd, else the commit of
    this repo a git install came from, as a git URL Docker fetches itself as the build context (as PortSim's setup)."""
    for root in [source] if source else [Path(__file__).resolve().parents[2], Path.cwd()]:
        if (root / "Dockerfile").is_file() and (root / "trucksim_core").is_dir() and (root / "agents" / AGENT_ID).is_dir():
            return str(root)
    if source:
        raise click.UsageError(f"{source} is not a checkout of agentenv-trucksim-plugin (no Dockerfile, trucksim_core/ "
                               f"and agents/{AGENT_ID}/)")
    if commit := _installed_commit():
        return f"{REPO}.git#{commit}"
    raise click.UsageError(f"no checkout of agentenv-trucksim-plugin found: clone {REPO} and pass --source "
                           "(the images are built from the repo's Dockerfiles)")


def _context(root: str, subdir: str | None = None) -> str:
    """The build context of ``subdir`` of ``root``: a folder, or for a git URL its ``#commit:subdir`` form."""
    if not subdir:
        return root
    return f"{root}:{subdir}" if "#" in root else str(Path(root) / subdir)


def _docker_platform() -> str:
    out = subprocess.run(["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], capture_output=True, text=True)
    if out.returncode:
        raise click.ClickException(f"docker is not running: {out.stderr.strip()}")
    return out.stdout.strip()


def _build(tag: str, context: str, platform: str) -> None:
    """Build ``tag`` from the Dockerfile at the top of ``context`` (a folder or a git URL)."""
    click.echo(f"Building {tag} for {platform} from {context}")
    if subprocess.run(["docker", "build", "--platform", platform, "-t", tag, context]).returncode:
        raise click.ClickException(f"docker build of {tag} failed")


@click.group()
def trucksim():
    """TruckSimEnv: build and register the env and the operator agent, and build replay pages."""


@trucksim.command()
@click.option("--source", type=click.Path(exists=True, file_okay=False, path_type=Path), help="The repo checkout to build.")
@click.option("--platform", "platform_", help="Build platform. Default: the Docker host's; remote sandboxes need linux/amd64.")
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="The operator agent's default model.")
def setup(source: Path | None, platform_: str | None, model: str):
    """Build the env and agent images and register them: the env `trucksim` (on the `server` provider, as game envs
    are) and the A2A agent `trucksim-llm`."""
    from agent_env.a2a_agent import A2AAgent
    from agent_env.artifact import DockerImageArtifact
    from agent_env.env import MCPServerEnv

    root, platform_ = _checkout(source), platform_ or _docker_platform()
    _build(ENV_IMAGE, _context(root), platform_)
    art = DockerImageArtifact.put(id=ENV_IMAGE, image_name=ENV_IMAGE, description="TruckSimEnv env: the sim core as a game env")
    env = MCPServerEnv.put(id=ENV_ID, docker_image_artifact=art, environment_name=ENV_ID, env_provider_type="server")
    click.echo(f"Registered env {env.id!r} version {env.version}")
    _build(AGENT_IMAGE, _context(root, f"agents/{AGENT_ID}"), platform_)
    art = DockerImageArtifact.put(id=AGENT_IMAGE, image_name=AGENT_IMAGE, description="trucksim-llm: one model operating one seat")
    agent = A2AAgent.put(id=AGENT_ID, docker_image_artifact=art, metadata={"default_model": model})
    click.echo(f"Registered A2A agent {agent.id!r} version {agent.version}")


@trucksim.group()
def replay():
    """Build a self-contained replay page."""


def _write(doc: dict, out: Path, open_it: bool) -> None:
    from .replay_page import page
    out.write_text(page(doc))
    click.echo(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    if open_it:
        webbrowser.open(out.resolve().as_uri())


@replay.command("sim")
@click.argument("monday")
@click.argument("seed", type=int)
@click.option("--seat", type=click.Choice(["A", "B", "C"]), help="The seat --policy plays; every other seat is a rival.")
@click.option("--policy", "seat_policy", default="rolling-a", type=click.Choice(["greedy", "rolling-a", "rolling-b"]))
@click.option("-o", "--out", type=click.Path(path_type=Path), required=True)
@click.option("--open", "open_it", is_flag=True, help="Open the page in a browser.")
def replay_sim(monday, seed, seat, seat_policy, out, open_it):
    """Play a week in process and replay it."""
    from .replay_page import simulated
    _write(simulated(monday, seed, seat, seat_policy), out, open_it)


@replay.command("files")
@click.argument("events", type=click.Path(exists=True, path_type=Path))
@click.argument("result", type=click.Path(exists=True, path_type=Path))
@click.option("--trajectory", multiple=True, help="SEAT=path to a trucksim-llm trajectory JSON (repeatable).")
@click.option("-o", "--out", type=click.Path(path_type=Path), required=True)
@click.option("--open", "open_it", is_flag=True)
def replay_files(events, result, trajectory, out, open_it):
    """Replay a run from the match files it kept (save_match_files: events and result)."""
    from .replay_page import from_run_files
    trajs = {}
    for item in trajectory:
        seat, _, path = item.partition("=")
        if not path:
            raise click.BadParameter(f"--trajectory takes SEAT=path, not {item!r}")
        trajs[seat] = Path(path)
    _write(from_run_files(events, result, trajs), out, open_it)
