"""TruckSimEnv's MCP server: the sim core (trucksim_core) as a game env (agentenv-game-env).

The lobby has three player slots, A, B and C, one per seat (each seat a carrier with a hub). Each slot is an agent or
the game's AI, which plays the rival policy (people need a play page, which v1 doesn't have). An agent plays at its slot's address, /players/<seat>/mcp,
so every tool acts for that seat only.

A turn is 6 virtual hours. While it is open the clock is stopped and every seat acts; `end_turn` waits until every
agent has ended the turn (the game's AI seats play at once), then the world runs and the call returns the
next turn's situation. A seat that makes no call for `idle_turn_seconds` while others wait has its turn ended for it
and the turn counted as missed.

    python -m agentenv_trucksim.server     # serves on MCP_PORT (default 18765)"""

import asyncio
import datetime as dt
import json
import tempfile
import time
from pathlib import Path
from typing import Literal

from agentenv_game import (
    AgentEnvGameEnv,
    Counter,
    GameError,
    Lobby,
    MatchFile,
    MatchReport,
    Moves,
    PlayerKind,
    PlayerSlot,
    PlayerSlotLimits,
    Score,
    check_player_slot,
    create_game,
    match_files,
    match_report,
    play_out,
    player_slot_limits,
    spectator_card,
)
from agentenv_protocol import DataPart, environment_card, extension, get_data, tool
from agentenv_protocol.types import EnvironmentCapabilities, EnvironmentCard, EnvironmentInterface
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import HTMLResponse, JSONResponse

from trucksim_core import replay
from trucksim_core.audit import audit
from trucksim_core.data import DataPack
from trucksim_core.game import ActionError, Game
from trucksim_core.planner import CONFIGS, policy
from trucksim_core.policies import greedy
from trucksim_core.references import SEATS, reward
from trucksim_core.scenario import build_scenario

SEAT_IDS = tuple(s for s, _ in SEATS)
POLICIES = {"greedy": greedy, **{name: policy(name) for name in CONFIGS}}
WAIT_POLL_SECONDS = 2.0


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@environment_card(name="trucksim")
class TruckSimEnv(AgentEnvGameEnv):
    class GameSettings(BaseModel):
        model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)
        monday: str = "2024-06-03"
        """The week played, a Monday from 2019 to 2025: its diesel prices and its weather are that week's."""
        seed: int = Field(1, ge=0)
        """Draws the week's load offers and what's hidden in them, and every truck's failures."""
        rival: Literal["greedy", "rolling-a", "rolling-b"] = "rolling-a"
        """The policy the game's AI seats play."""
        naive_usd: float | None = None
        """The reference profit of the greedy operator in the agent's seat (with anchor_usd, or neither)."""
        anchor_usd: float | None = None
        """The reference profit of the re-planner in the agent's seat: reward 1.0 at or above it."""
        idle_turn_seconds: float = Field(300.0, gt=0)
        """A seat that makes no call this long, while others wait for it, has its turn ended for it."""

        @model_validator(mode="after")
        def _check(self):
            day = dt.date.fromisoformat(self.monday)
            if day.weekday() != 0:
                raise ValueError(f"monday {self.monday} is not a Monday")
            if (self.naive_usd is None) != (self.anchor_usd is None):
                raise ValueError("give both naive_usd and anchor_usd, or neither")
            if self.naive_usd is not None and self.anchor_usd <= self.naive_usd:
                raise ValueError("anchor_usd must be above naive_usd")
            return self

    def __init__(self):
        super().__init__()
        self.pack = DataPack()
        self.game: Game | None = None
        self.settings: dict = {}
        self.model_seats: set[str] = set()
        self.made: dict[str, int] = {}
        self.missed: dict[str, int] = {}
        self.last_call: dict[str, float] = {}
        self.turn_changed: asyncio.Condition | None = None

    # ---- the lobby ------------------------------------------------------------------------------------------------
    @player_slot_limits
    def three_seats(self, game_settings: GameSettings, requested: PlayerSlotLimits) -> PlayerSlotLimits:
        if requested.max not in (None, 3) or requested.min not in (None, 3):
            raise ValueError("TruckSimEnv has three seats, A, B and C")
        return PlayerSlotLimits(min=3, max=3, player_kinds=[PlayerKind.AGENT, PlayerKind.AI])

    @check_player_slot
    def a_seat(self, slot: PlayerSlot, lobby: Lobby) -> None:
        if slot.player_id not in SEAT_IDS:
            raise GameError("bad_slot", f"TruckSimEnv's player slots are its seats {list(SEAT_IDS)}, not {slot.player_id!r}")

    @create_game
    async def new_game(self, lobby: Lobby) -> None:
        self.settings = dict(lobby.game_settings)
        self.model_seats = {s.player_id for s in lobby.player_slots if s.player_kind is not PlayerKind.AI}
        if self.settings.get("naive_usd") is not None and len(self.model_seats) != 1:
            raise ValueError("references (naive_usd, anchor_usd) grade exactly one agent seat; this lobby has "
                             f"{sorted(self.model_seats) or 'none'}")
        scenario = build_scenario(self.pack, self.settings["seed"], dt.date.fromisoformat(self.settings["monday"]), list(SEATS))
        self.made = {s: 0 for s in self.model_seats}
        self.missed = {s: 0 for s in self.model_seats}
        self.last_call = {s: time.monotonic() for s in self.model_seats}
        self.turn_changed = asyncio.Condition()
        self.game = Game(self.pack, scenario, self.model_seats, npc_policy=POLICIES[self.settings["rival"]])

    @match_report
    def report(self) -> MatchReport:
        g = self.game
        if g is None:
            return MatchReport(status="started")
        result = g.result()["seats"]
        scores = {}
        for seat in SEAT_IDS:
            row = [Score(name="profit", value=result[seat]["profit_usd"], better="higher", unit="USD")]
            r = self._reward(seat)
            if r is not None:
                row.append(Score(name="reward", value=round(r, 4), better="higher"))
            scores[seat] = row
        outcomes = {}
        if g.done:
            best = max(result[s]["profit_usd"] for s in SEAT_IDS)
            winners = [s for s in SEAT_IDS if result[s]["profit_usd"] == best]
            outcomes = {s: ("drawn" if len(winners) > 1 else "won") if s in winners else "lost" for s in SEAT_IDS}
        return MatchReport(status="finished" if g.done else "started",
                           status_detail=f"week of {g.scenario.monday}, {g.situation(SEAT_IDS[0])['clock']}",
                           progress=[Counter(name="week", unit="turns", value=g.turn, limit=g.p.week_ticks // g.p.turn_ticks)],
                           outcomes=outcomes, scores=scores,
                           moves={s: Moves(made=self.made[s], missed=self.missed[s]) for s in sorted(self.model_seats)})

    @play_out
    async def finish_week(self) -> None:
        """After the agents stop: end their turns, turn after turn, until the week is over."""
        g = self._require_game()
        while not g.done:
            for seat in sorted(self.model_seats):
                if not g.done and seat not in g.ended:
                    self.missed[seat] += 1
                    g.end_turn(seat)
        await self._notify()

    @match_files
    async def kept(self, kinds: list[str] | None) -> list[MatchFile]:
        g = self._require_game()
        wanted = set(kinds or ("events", "result"))
        if unknown := sorted(wanted - {"events", "result"}):
            raise ValueError(f"TruckSimEnv keeps 'events' and 'result', not {unknown}")
        folder = Path(tempfile.mkdtemp(prefix="trucksim-"))
        stem = f"trucksim-{g.scenario.monday}-s{g.scenario.seed}"
        files = []
        if "events" in wanted:
            (folder / "events.jsonl").write_text(g.event_log_jsonl())
            files.append(MatchFile(name=f"{stem}-events.jsonl", kind="events", content_type="application/x-ndjson",
                                   file=folder / "events.jsonl"))
        if "result" in wanted:
            (folder / "result.json").write_text(_json(self._data()))
            files.append(MatchFile(name=f"{stem}-result.json", kind="result", content_type="application/json",
                                   file=folder / "result.json"))
        return files

    @spectator_card
    def spectators(self) -> EnvironmentCard:
        return EnvironmentCard(name="trucksim/spectators",
                               additionalInterfaces=[EnvironmentInterface(url="/spectate", transport="http")],
                               capabilities=EnvironmentCapabilities(operations=[]))

    def create_app(self):
        app = super().create_app()

        @app.custom_route("/spectate", methods=["GET"])
        async def spectate(request):
            return HTMLResponse("<!doctype html><meta charset='utf-8'><title>TruckSimEnv</title>"
                                "<pre id=s>loading</pre><script>async function d(){const r=await fetch('state.json');"
                                "document.getElementById('s').textContent=JSON.stringify(await r.json(),null,1)}"
                                "d();setInterval(d,2000)</script>")

        @app.custom_route("/state.json", methods=["GET"])
        async def state(request):
            return JSONResponse(self._data() if self.game else {"match": None})

        @app.custom_route("/replay.json", methods=["GET"])
        async def live_replay(request):
            """The week so far as a replay document: the viewer follows a running game with it."""
            if self.game is None:
                return JSONResponse({"error": "no game yet: the lobby hasn't closed"}, status_code=404)
            doc = replay.from_game(self.pack, self.game)
            data = self._data()
            doc["result"].update(rival=data["rival"], references=data["references"], rewards=data["rewards"])
            doc["live"] = {"done": self.game.done, "tick": self.game.tick, "turn": self.game.turn}
            return JSONResponse(doc)

        return app

    # ---- the data plane: what the verifier reads ------------------------------------------------------------------
    @get_data
    def get(self) -> list[DataPart]:
        return [DataPart(data=self._data() if self.game else {"done": False, "match": None})]

    def _data(self) -> dict:
        g = self.game
        out = g.result()
        out.update(model_seats=sorted(self.model_seats), rival=self.settings["rival"],
                   references=({"naive_usd": self.settings["naive_usd"], "anchor_usd": self.settings["anchor_usd"]}
                               if self.settings.get("naive_usd") is not None else None),
                   rewards={s: round(r, 4) for s in sorted(self.model_seats) if (r := self._reward(s)) is not None},
                   moves={s: {"made": self.made[s], "missed": self.missed[s]} for s in sorted(self.model_seats)},
                   audit=audit(g) if g.done else None,
                   trucks=[{"id": t.spec.id, "seat": t.spec.seat, "equipment": t.spec.equipment, "class": t.spec.vclass,
                            "owner": t.spec.owner, "driver": " & ".join(d.name for d in t.crew) or "(no driver)", "hub": t.spec.hub}
                           for t in sorted(g.trucks.values(), key=lambda t: t.spec.id)],
                   drivers=replay.drivers_of(g),
                   duty_logs=g.duty_logs())
        return out

    def _reward(self, seat: str) -> float | None:
        if self.game is None or seat not in self.model_seats or self.settings.get("naive_usd") is None:
            return None
        return reward(self.game.profit(seat), self.settings["naive_usd"], self.settings["anchor_usd"])

    # ---- helpers --------------------------------------------------------------------------------------------------
    def _require_game(self) -> Game:
        if self.game is None:
            raise ValueError("there is no game yet: its lobby hasn't closed")
        return self.game

    def _seat(self) -> str:
        g = self._require_game()
        slot = self.player()
        if slot is None:
            raise ValueError("play at your seat's address, /players/<A|B|C>/mcp")
        self.last_call[slot.player_id] = time.monotonic()
        if g.done:
            raise ValueError("the week is over")
        return slot.player_id

    def _call(self, fn, *args):
        try:
            return _json(fn(self._seat(), *args))
        except ActionError as e:
            raise ValueError(str(e)) from None

    async def _notify(self) -> None:
        if self.turn_changed is not None:
            async with self.turn_changed:
                self.turn_changed.notify_all()

    # ---- the operator's tools ---------------------------------------------------------------------------------------
    @tool()
    def situation(self) -> str:
        """Your seat right now: the clock and turn, your profit and ledger, every truck (where, doing what, its driver's
        hours left, fault warnings), your loads, open offers, the storms under way (by road: the stretch they slow and
        how much), the roads closed now (where, which way, why, and when they're expected to reopen), and what happened
        to your seat since your last turn."""
        return self._call(self._require_game().situation)

    @tool()
    def list_loads(self, origin: str | None = None, equipment: Literal["dry", "reefer"] | None = None,
                   size: Literal["full", "partial", "small"] | None = None,
                   fits: Literal["tractor_dry", "tractor_reefer", "box", "van"] | None = None, max_results: int = 40) -> str:
        """The open load offers on the board, optionally from one origin city (e.g. "HOU"), of one equipment or size,
        or that one kind of unit can carry: lane, miles, size (pallets, pounds), which units it fits, pickup window,
        delivery appointment (expedited loads have tight ones), linehaul and fuel surcharge, and when the offer
        expires. At most max_results, the newest first when there are more (the count left out is reported)."""
        return self._call(self._require_game().list_loads, origin, equipment, size, fits, max_results)

    @tool()
    def get_truck(self, truck: str) -> str:
        """One of your trucks and its driver: where it is, its plan, its driver's hours-of-service clocks and today's
        duty log, fault-code warnings, and a breakdown's repair options when it has one."""
        return self._call(self._require_game().get_truck, truck)

    @tool()
    def get_market(self) -> str:
        """This week's diesel by region, the fuel surcharge it pays, the linehaul per mile offered by origin over the
        last 24 hours, and your costs: fuel economy, driver cost per on-duty hour, wear per mile, fixed cost per truck
        and day, detention terms and penalties."""
        return self._call(self._require_game().get_market)

    @tool()
    def get_forecast(self) -> str:
        """The weather forecast for the next 72 hours by road: each road's coming storms, the stretch and how much they
        would slow trucks (forecasts can miss storms, mistime them, or be false alarms), next week's diesel outlook, and
        your trucks' fault-code warnings."""
        return self._call(self._require_game().get_forecast)

    @tool()
    def plan_trip(self, truck: str, load_ids: list[str] | None = None, reposition_to: str | None = None,
                  stops: list[str] | None = None) -> str:
        """What a truck would do with these loads (board loads or yours), without changing anything: each stop's time,
        the driver's required rests, empty and loaded miles, the waits and detours for roads closed now, and the
        estimated revenue, cost and profit. load_ids: one load after another. Or stops, to carry several at once:
        each new load's id twice, its first appearance the pickup and its second the delivery (e.g. ["L-1012",
        "L-1020", "L-1012", "L-1020"]: pick up both, then deliver both), and a load already aboard once. Loads aboard
        together must fit the unit (pallets, weight, one kind of freight). It assumes average stop times, no
        breakdowns, only the weather already under way, and that closed roads reopen when announced."""
        return self._call(self._require_game().plan_trip, truck, load_ids or [], reposition_to, stops)

    @tool()
    def accept_load(self, load_id: str) -> str:
        """Claim a load at its posted rate. Claims settle when every seat has ended the turn; when two seats claim the
        same load, the one with a truck closer to its pickup gets it."""
        return self._call(self._require_game().accept_load, load_id)

    @tool()
    def release_load(self, load_id: str) -> str:
        """Withdraw a claim before it settles (free), or give back a load you own before its pickup (a penalty)."""
        return self._call(self._require_game().release_load, load_id)

    @tool()
    def dispatch(self, assignments: list[dict]) -> str:
        """Give trucks their next loads: [{"truck": "A01", "load_ids": ["L-1012", "L-1020"]}, ...] (one after the
        other; loads aboard are delivered first) or [{"truck": "A01", "stops": [...]}] (as in plan_trip, to carry
        several at once). Several trucks in one call. Each replaces that truck's plan; a stop under way finishes. An
        assignment that would miss a pickup window, or overfill the unit, is refused with the reason. Loads must be
        yours or claimed by you this turn."""
        return self._call(self._require_game().dispatch, assignments)

    @tool()
    def list_drivers(self) -> str:
        """Your drivers: who each is (a company driver paid by the mile, a local driver without a CDL paid by the hour,
        an owner-operator who drives only their own truck and can turn a trip down, or a temp), where they are (on a
        unit, or off duty at home at the hub), and their hours-of-service clocks."""
        return self._call(self._require_game().list_drivers)

    @tool()
    def assign_drivers(self, truck: str, driver_ids: list[str]) -> str:
        """Put a crew on a unit at the hub: one driver, or two on a tractor (a team: one drives while the other sleeps,
        so the truck runs about 20 hours a day). Each must be at home at the hub or on a unit that is there too; a
        tractor needs CDL drivers. Use it to send a tractor back out with a rested driver while its last one rests at
        home (slip-seating). The drivers coming off go home."""
        return self._call(self._require_game().assign_drivers, truck, driver_ids)

    @tool()
    def hire_temp_driver(self, days: int = 1) -> str:
        """Call a CDL temp driver from an agency: at the hub in 12 hours, for whole days, paid hourly on duty with a
        daily minimum (see get_market). At most two at a time."""
        return self._call(self._require_game().hire_temp_driver, days)

    @tool()
    def reposition(self, truck: str, city: str) -> str:
        """Send a truck, empty, to a city (after delivering any load on board)."""
        return self._call(self._require_game().reposition, truck, city)

    @tool()
    def schedule_maintenance(self, truck: str, city: str | None = None) -> str:
        """Service a truck at a shop (a freight city; its hub by default): 4 hours, and its components' wear is reset."""
        return self._call(self._require_game().schedule_maintenance, truck, city)

    @tool()
    def handle_breakdown(self, truck: str, option: Literal["roadside", "tow"]) -> str:
        """Decide a broken-down truck's repair: "roadside" (not for engines) or "tow" to the nearest shop. get_truck
        shows each option's hours and cost. Until you decide, the truck sits where it broke."""
        return self._call(self._require_game().handle_breakdown, truck, option)

    @tool()
    async def end_turn(self) -> str:
        """End your turn. When every seat has ended it, the world runs 6 hours and this returns the next turn's
        situation; until then it waits, which can take minutes."""
        g = self._require_game()
        seat = self._seat()
        turn = g.turn
        try:
            g.end_turn(seat)
        except ActionError as e:
            raise ValueError(str(e)) from None
        self.made[seat] += 1
        if g.turn != turn or g.done:
            await self._notify()
        while g.turn == turn and not g.done:
            self._end_idle_seats(waiting=seat)
            if g.turn != turn or g.done:
                await self._notify()
                break
            async with self.turn_changed:
                try:
                    await asyncio.wait_for(self.turn_changed.wait(), timeout=WAIT_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
        self.last_call[seat] = time.monotonic()
        return _json(g.situation(seat))

    def _end_idle_seats(self, waiting: str) -> None:
        g, limit, now = self.game, self.settings["idle_turn_seconds"], time.monotonic()
        for seat in sorted(self.model_seats):
            if seat != waiting and seat not in g.ended and now - self.last_call[seat] >= limit:
                self.missed[seat] += 1
                g.end_turn(seat)
                if g.done:
                    return

    # ---- for no-model tasks: a reference policy plays a seat -------------------------------------------------------
    @extension("urn:trucksim:autoplay/v1",
               description="Play a seat with a reference policy (greedy, rolling-a or rolling-b) until the week ends, "
                           "for tasks that check the grader without a model.")
    def autoplay(self, seat: str, policy: str) -> dict:
        g = self._require_game()
        if seat not in self.model_seats or len(self.model_seats) != 1:
            raise ValueError(f"autoplay plays the lobby's one agent seat; this lobby's are {sorted(self.model_seats)}")
        if policy not in POLICIES:
            raise ValueError(f"policy is one of {sorted(POLICIES)}")
        while not g.done:
            self.made[seat] += 1
            POLICIES[policy](g, seat)
        return {"done": g.done, "profit_usd": round(g.profit(seat), 2), "reward": self._reward(seat)}


def main() -> None:
    TruckSimEnv().serve()


if __name__ == "__main__":
    main()
