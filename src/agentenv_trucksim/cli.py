"""`agent-env trucksim`: build and register the env and the operator agent, and build replay pages."""

import subprocess
import webbrowser
from pathlib import Path

import click

ENV_ID = "trucksim"
ENV_IMAGE = "agentenv-trucksim-env"
AGENT_ID = "trucksim-llm"
AGENT_IMAGE = "agentenv-trucksim-llm"
DEFAULT_MODEL = "anthropic/claude-haiku-5-5"


def _checkout(source: Path | None) -> Path:
    """The repo checkout to build from: --source, else the one an editable install runs from, else the cwd."""
    for root in [source] if source else [Path(__file__).resolve().parents[2], Path.cwd()]:
        if root and (root / "Dockerfile").is_file() and (root / "trucksim_core").is_dir() and (root / "agents" / AGENT_ID).is_dir():
            return root
    raise click.UsageError("no checkout of agentenv-trucksim-plugin found: clone it and pass --source "
                           "(the images are built from the repo's Dockerfiles)")


def _docker_platform() -> str:
    out = subprocess.run(["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], capture_output=True, text=True)
    if out.returncode:
        raise click.ClickException(f"docker is not running: {out.stderr.strip()}")
    return out.stdout.strip()


def _build(tag: str, context: Path, dockerfile: Path, platform: str) -> None:
    click.echo(f"Building {tag} for {platform}")
    if subprocess.run(["docker", "build", "--platform", platform, "-t", tag, "-f", str(dockerfile), str(context)]).returncode:
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
    _build(ENV_IMAGE, root, root / "Dockerfile", platform_)
    art = DockerImageArtifact.put(id=ENV_IMAGE, image_name=ENV_IMAGE, description="TruckSimEnv env: the sim core as a game env")
    env = MCPServerEnv.put(id=ENV_ID, docker_image_artifact=art, environment_name=ENV_ID, env_provider_type="server")
    click.echo(f"Registered env {env.id!r} version {env.version}")
    _build(AGENT_IMAGE, root / "agents" / AGENT_ID, root / "agents" / AGENT_ID / "Dockerfile", platform_)
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
