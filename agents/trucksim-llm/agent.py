"""trucksim-llm: an operator for TruckSimEnv as an A2A agent. One model plays one seat for the whole week, through
agent-env's model endpoint (LITELLM_BASE_URL, LITELLM_API_KEY, LITELLM_EXTRA_HEADERS) and the OpenAI chat-completions
API, which the proxy serves for every model, so Claude, GPT and open models run on the same code.

Each game turn is a fresh conversation: the rules, the operator's own notes from the turn before, and the turn's
situation. The model acts with the seat's tools until it calls end_turn(notes); the harness then ends the env's turn
(which waits for the other seats), keeps the notes and starts the next turn. So the context stays one turn long,
however long the week is. A turn that runs past max_turns model calls is ended by the harness, with the notes it had.

Every call marks prompt-cache breakpoints (_cache_marked): within a turn each call re-sends the conversation so far, so
all but the newest messages are read from the cache, and the tools and rules are cached across the week."""

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from agentenv_protocol.a2a_agent import (
    MCP_CONFIG_V1,
    TRAJECTORY_V1,
    AgentConfig,
    AgentEnvAgent,
    AgentIdentity,
    NativeTrajectory,
    TaskRequest,
    TaskResult,
    TextPart,
    Usage,
    a2a_agent,
)

log = logging.getLogger("trucksim-llm")
TRAJECTORY_FORMAT = "trucksim-llm/turns"
TOOL_OUTPUT_KEEP = 6_000          # characters of each tool output kept in a turn's `calls`, the viewer's copy (the
                                  # model sees all of it, and the turn's `messages` keep all of it)

RULES = """You are the operator (dispatcher) of a small trucking carrier: one of three seats on a shared load board in
the Texas Triangle, for one real week. You decide which loads to take and which truck and driver does what; you never
drive. Your drivers follow the federal hours-of-service rules, which the env enforces and plans for you.

Your fleet: tractors with 53' dry vans (26 pallets, 45,000 lb), a tractor with a 53' reefer (it can also haul dry
freight, but not both at once), two 26' box trucks (12 pallets, 10,000 lb, no sleeper: a rest away from home is a
motel), a cargo van (3 pallets, 3,500 lb), and two owner-operators' tractors leased on to you. Each costs differently
to run (get_market).

Your drivers (list_drivers): company drivers paid by the mile (two of them run one tractor as a team, one is a spare
at the hub), local drivers paid by the hour for the box trucks and the van, and the two owner-operators, who drive only
their own trucks, are paid a share of each load, pay their own fuel and repairs, and turn down a trip that doesn't pay
them enough per mile or wouldn't get them home by Saturday noon (plan_trip shows their answer). A unit runs only with
a driver: at the hub you can put a rested driver on a tractor whose driver is out of hours (assign_drivers), or call
a temp (hire_temp_driver).

Your goal: the highest profit by the end of the week, after the end-of-week charges.

How the week runs: 28 turns of 6 hours. While a turn is open the clock is stopped; you act with the tools, then call
end_turn(notes). When every seat has ended the turn, the world runs 6 hours (trucks drive, load, unload, rest, break
down, weather moves in, new offers post) and your next turn starts with what happened. Write in `notes` whatever you
want to remember next turn: you will not see this turn's conversation again.

Money. Revenue, booked at delivery: the linehaul, the fuel surcharge per loaded mile, and detention pay when a dock
keeps your truck past its free time. Costs: fuel for every mile (empty miles too) at this week's diesel price, your
drivers' pay (company drivers by the mile, local drivers and temps by the on-duty hour), owner-operators' settlements,
wear per mile, a fixed cost per unit per day that runs whether it moves or not, the reefer unit's fuel while it
carries reefer freight, motels, repairs, tows, maintenance, and penalties for late deliveries, missed pickups and
released loads. At the end of the week, each truck away from its hub is charged the trip home, a load on board is
credited for the share of its miles driven, and an unrepaired truck is charged its repair.

Loads. accept_load claims an offer at its posted rate; claims settle when every seat has ended the turn, and if a rival
claims the same load the seat with the truck closer to the pickup gets it. Loads come full (18-26 pallets), partial
(6-14) or small (1-3; some expedited, with tight appointments and a premium); each lists the units it fits. dispatch
gives trucks their next loads, one after another, or several at once with `stops` (partials from one shipper often
share a trailer: pick up both, then deliver both); it is refused with the reason if a pickup window would be missed
or the unit overfilled. plan_trip shows a truck's timeline, required rests and estimated profit for any loads or
stops without changing anything.

Risks. Docks sometimes keep trucks for hours (detention). Trucks break down: decide with handle_breakdown, or the truck
sits; a fault-code warning precedes some failures, and maintenance at a shop resets wear. Weather slows roads and
sometimes closes them (see get_forecast and situation's closures_now; forecasts can be wrong, reopenings are estimates);
drivers detour or wait. Shippers sometimes cancel. Rivals compete for the same loads.

Work efficiently: a few well-chosen calls a turn. When nothing needs doing, end the turn."""

END_TURN = {"type": "function", "function": {
    "name": "end_turn",
    "description": "End your turn. The world then runs 6 hours. Put in notes everything you want to remember next "
                   "turn (plans, which truck is meant for what, what to watch); you won't see this conversation again.",
    "parameters": {"type": "object", "properties": {"notes": {"type": "string", "description": "Your notes for next turn."}},
                   "required": ["notes"]}}}


class TruckSimConfig(AgentConfig):
    model: str = "anthropic/claude-haiku-5-5"
    system_prompt: str = ""                 # appended to RULES: the task's own instructions
    max_turns: int = 20                     # model calls per game turn (prompt_agent's max_turns)
    max_game_turns: int = 40                # a week has 28; a safety stop
    max_tokens: int = 8_000
    effort: str | None = None               # passed to the model as reasoning_effort when set (prompt_agent's effort)
    end_turn_timeout_seconds: int = 1_800   # end_turn waits for the other seats
    timeout_seconds: int = 7_200


CACHE_MARK = {"type": "ephemeral"}  # Anthropic's 5-minute prompt cache; LiteLLM passes cache_control through to Claude


def _cache_marked(messages: list) -> list:
    """The copy of a turn's conversation sent to the model, with up to three prompt-cache breakpoints:
    - the system prompt: the tools and the rules come before it, the same on every call of the week, so they are
      written to the cache once and read on every call after;
    - the last message: the next call reads everything up to it and pays full price only for what's new;
    - the message before the last assistant reply, the previous call's last message: the cache entry that call wrote,
      marked again so this call finds it however many tool results the reply added (Anthropic looks back only 20
      blocks from a breakpoint for an earlier entry).
    A cache read is billed at a tenth of the input price, a cache write at 1.25 times. The turn's own `messages`, the
    transcript, stay unmarked."""
    last_reply = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=None)
    marks = {0, len(messages) - 1} | ({last_reply - 1} if last_reply is not None else set())
    out = list(messages)
    for i in marks:
        if not isinstance(out[i]["content"], str):
            raise TypeError(f"message {i} ({out[i]['role']}) has no text content to mark for the prompt cache")
        out[i] = {**out[i], "content": [{"type": "text", "text": out[i]["content"], "cache_control": CACHE_MARK}]}
    return out


def _cache_usage(usage) -> tuple[int, int]:
    """(cache-read, cache-write) input tokens of one call, as LiteLLM reports Claude's: prompt_tokens counts every
    input token, these included."""
    return (getattr(usage, "cache_read_input_tokens", None) or 0,
            getattr(usage, "cache_creation_input_tokens", None) or 0)


def _text(result) -> str:
    out = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    return f"ERROR: {out}" if result.isError else out


async def _complete(client: AsyncOpenAI, cfg: TruckSimConfig, messages: list, tools: list):
    """One model call, retried on rate limits, 5xx and dropped connections (3 tries, backing off)."""
    kwargs = {"model": cfg.model, "messages": _cache_marked(messages), "tools": tools, "max_tokens": cfg.max_tokens}
    if cfg.effort:
        kwargs["reasoning_effort"] = cfg.effort
    for attempt in range(3):
        try:
            return await client.chat.completions.create(**kwargs)
        except (RateLimitError, APIConnectionError) as e:
            err = e
        except APIStatusError as e:
            if e.status_code < 500:
                raise
            err = e
        await asyncio.sleep(5 * (attempt + 1))
    raise err


@a2a_agent(
    identity=AgentIdentity(name="trucksim-llm",
                           description="An operator for TruckSimEnv: one model runs one seat's carrier for the week, "
                                       "a fresh conversation each turn, carrying its own notes forward.",
                           version="0.1.0"),
    config=TruckSimConfig,
    extensions=(MCP_CONFIG_V1, TRAJECTORY_V1),
)
class TruckSimAgent(AgentEnvAgent):
    async def run(self, request: TaskRequest[TruckSimConfig]) -> TaskResult:
        cfg = request.config
        servers = list(request.mcp_servers.values())
        if len(servers) != 1:
            return TaskResult.failure("mcp", f"expected the seat's one MCP server, got {len(servers)}")
        headers = json.loads(os.environ.get("LITELLM_EXTRA_HEADERS") or "{}")
        client = AsyncOpenAI(base_url=os.environ["LITELLM_BASE_URL"], api_key=os.environ["LITELLM_API_KEY"],
                             default_headers=headers or None, timeout=300)
        task_text = "\n".join(p.text for p in request.parts if isinstance(p, TextPart))
        system = RULES + (f"\n\n{cfg.system_prompt}" if cfg.system_prompt else "")
        # input_tokens counts every input token; cache_read_tokens and cache_write_tokens are the parts of it read from
        # and written to the prompt cache
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "tool_calls": 0, "model_calls": 0}
        turns, notes, started = [], "(none yet: this is your first turn)", time.monotonic()

        async with AsyncExitStack() as stack:
            read, write, _ = await stack.enter_async_context(streamablehttp_client(
                servers[0]["url"], headers=servers[0].get("headers"), timeout=60,
                sse_read_timeout=cfg.end_turn_timeout_seconds + 60))
            mcp = await stack.enter_async_context(ClientSession(read, write))
            await mcp.initialize()
            env_tools = [t for t in (await mcp.list_tools()).tools if t.name != "end_turn"]
            tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                       "parameters": t.inputSchema}} for t in env_tools] + [END_TURN]

            async def call(name: str, args: dict, timeout: int = 120) -> str:
                return _text(await mcp.call_tool(name, args, read_timeout_seconds=timedelta(seconds=timeout)))

            situation = await call("situation", {})
            for _ in range(cfg.max_game_turns):
                state = json.loads(situation)
                if state.get("done"):
                    break
                messages = [{"role": "system", "content": system},
                            {"role": "user", "content": f"{task_text}\n\nTurn {state['turn'] + 1} of {state['turns_total']}, "
                                                        f"{state['clock']}.\n\nYour notes from last turn:\n{notes}\n\n"
                                                        f"Situation (JSON):\n{situation}"}]
                record = {"turn": state["turn"], "clock": state["clock"], "calls": [], "ended_by": "harness"}
                for _ in range(cfg.max_turns):
                    try:
                        resp = await _complete(client, cfg, messages, tools)
                    except Exception as e:  # noqa: BLE001 - recorded, and the turn ends with the notes it had
                        record["model_error"] = f"{type(e).__name__}: {e}"[:500]
                        log.warning("model call failed on turn %s: %s", state["turn"], record["model_error"])
                        break
                    usage["model_calls"] += 1
                    if resp.usage:
                        usage["input_tokens"] += resp.usage.prompt_tokens or 0
                        usage["output_tokens"] += resp.usage.completion_tokens or 0
                        read, write = _cache_usage(resp.usage)
                        usage["cache_read_tokens"] += read
                        usage["cache_write_tokens"] += write
                    msg = resp.choices[0].message
                    messages.append(msg.model_dump(exclude_none=True))
                    if msg.content:
                        record["calls"].append({"say": msg.content[:TOOL_OUTPUT_KEEP]})
                    if not msg.tool_calls:
                        messages.append({"role": "user", "content": "Act with the tools, and end the turn with end_turn(notes)."})
                        continue
                    ended = False
                    for tc in msg.tool_calls:
                        usage["tool_calls"] += 1
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args, out = {}, "ERROR: the arguments were not valid JSON"
                        else:
                            if tc.function.name == "end_turn":
                                notes, ended, out = str(args.get("notes") or ""), True, "ok: your turn will end"
                            else:
                                out = await call(tc.function.name, args)
                        record["calls"].append({"tool": tc.function.name, "args": args, "out": out[:TOOL_OUTPUT_KEEP]})
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
                    if ended:
                        record["ended_by"] = "agent"
                        break
                record["notes"] = notes
                # the turn's whole conversation as sent and received (the system prompt, the situation, every call and
                # its full output): each turn is a fresh conversation, so this is the transcript to train on
                record["messages"] = messages
                turns.append(record)
                situation = await call("end_turn", {}, timeout=cfg.end_turn_timeout_seconds)

        final = json.loads(situation)
        summary = (f"Week over for seat {final.get('seat')}: profit {final.get('profit_usd')} USD after "
                   f"{len(turns)} turns ({sum(t['ended_by'] == 'agent' for t in turns)} ended by the model); "
                   f"{usage['model_calls']} model calls, {usage['input_tokens']} input tokens ({usage['cache_read_tokens']} read "
                   f"from the prompt cache, {usage['cache_write_tokens']} written to it) and {usage['output_tokens']} output.")
        return TaskResult.success(
            parts=(TextPart(text=summary),),
            usage=Usage(tool_call_count=usage["tool_calls"], input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        total_tokens=usage["input_tokens"] + usage["output_tokens"],
                        provider_details={"model": cfg.model, "model_calls": usage["model_calls"],
                                          "cache_read_tokens": usage["cache_read_tokens"],
                                          "cache_write_tokens": usage["cache_write_tokens"],
                                          "seconds": round(time.monotonic() - started, 1)}),
            native_trajectory=NativeTrajectory(format=TRAJECTORY_FORMAT, payload={
                "model": cfg.model, "seat": final.get("seat"), "profit_usd": final.get("profit_usd"), "usage": usage,
                "seconds": round(time.monotonic() - started, 1), "turns": turns}))


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    TruckSimAgent().serve()
