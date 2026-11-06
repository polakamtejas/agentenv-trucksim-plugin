"""Grade a TruckSimEnv week: the agent seat's reward against the task's references (0.2 at the greedy operator, 1.0
at the re-planner, and above 1.0 for beating it: the references are a ruler, not a ceiling), from the env's own data. A week that isn't over, failed its validity audit, or carries no
references raises, so the run is not scored rather than scored 0. A seat whose turns the env had to end for it more
than MAX_MISSED_SHARE of the time scores 0."""

from agentenv_protocol import client

MAX_MISSED_SHARE = 0.10


async def verify(mcp_url: str) -> list[dict]:
    week = (await client.get_data(mcp_url.removesuffix("/mcp"))).parts[0].data
    if not week.get("done"):
        raise RuntimeError(f"the week isn't over (turn {week.get('turn')}): finish_match must run before grading")
    if not week["audit"]["ok"]:
        raise RuntimeError(f"the week failed its validity audit: {week['audit']['problems'][:5]}")
    if not week["rewards"]:
        raise RuntimeError("the lobby's settings carry no references (naive_usd, anchor_usd): nothing to grade against")
    (seat, reward), = week["rewards"].items()
    refs, me, moves = week["references"], week["seats"][seat], week["moves"][seat]
    turns = moves["made"] + moves["missed"]
    missed_share = moves["missed"] / turns if turns else 1.0
    played = missed_share <= MAX_MISSED_SHARE
    score = reward if played else 0.0
    return [
        {"criterion": "profit against the references: 0.2 at the greedy operator, 1.0 at the re-planner, more above it",
         "result": score >= 1.0, "score": score, "weight": 1,
         "evidence": f"seat {seat} ({me['hub']}) profit {me['profit_usd']:.2f} USD; naive {refs['naive_usd']:.2f}, "
                     f"anchor {refs['anchor_usd']:.2f}; {me['delivered']} loads delivered"},
        {"criterion": f"the agent ended its own turns (at most {MAX_MISSED_SHARE:.0%} ended for it)",
         "result": played, "score": 1.0 if played else 0.0, "weight": 0,
         "evidence": f"{moves['made']} ended by the agent, {moves['missed']} by the env"},
    ]
