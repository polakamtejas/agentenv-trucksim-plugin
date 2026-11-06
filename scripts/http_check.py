"""M5's over-HTTP check: serve the env, drive its lobby over REST and one seat over MCP at /players/A/mcp.

Usage: .venv/bin/python scripts/http_check.py [BASE_URL]
  without BASE_URL it starts the server itself on MCP_PORT=18799 and stops it at the end"""

import asyncio
import json
import os
import subprocess
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PORT = 18799
BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else f"http://127.0.0.1:{PORT}"


async def main() -> None:
    server = None if len(sys.argv) > 1 else subprocess.Popen(
        [sys.executable, "-m", "agentenv_trucksim.server"], env={**os.environ, "MCP_PORT": str(PORT), "MCP_HOST": "127.0.0.1"})
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
            for _ in range(60):
                try:
                    card = (await http.get("/.well-known/agent-env.json")).json()
                    break
                except httpx.TransportError:
                    time.sleep(0.5)
            exts = [e["uri"] for e in card["capabilities"].get("extensions", [])]
            print("card:", card["name"], "| tools:", len(card["capabilities"]["tools"]), "| extensions:", exts)
            lobby = (await http.post("/agentenv/ext/lobby/open", json={"game_settings": {"monday": "2024-06-03", "seed": 1}})).json()
            for seat, kind in (("A", "agent"), ("B", "ai"), ("C", "ai")):
                r = await http.post("/agentenv/ext/lobby/fill", json={"lobby_id": lobby["lobby_id"], "player_id": seat,
                                                                     "player_kind": kind, "player_name": "check" if kind == "agent" else None})
                r.raise_for_status()
            closed = (await http.post("/agentenv/ext/lobby/close", json={"lobby_id": lobby["lobby_id"]})).json()
            print("lobby:", closed["status"])
            seat_card = (await http.get("/players/A/.well-known/agent-env.json")).json()
            print("seat A card:", seat_card["name"], [i["url"] for i in seat_card.get("additionalInterfaces", [])][:2])
            rpc = (await http.post("/agentenv", json={"jsonrpc": "2.0", "id": 1, "method": "data/get", "params": {}})).json()
            print("data/get:", {k: rpc["result"]["parts"][0]["data"][k] for k in ("done", "turn", "model_seats")})
        async with streamablehttp_client(f"{BASE}/players/A/mcp") as (read, write, _):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()
                tools = sorted(t.name for t in (await mcp.list_tools()).tools)
                print("seat A tools:", tools)
                sit = json.loads((await mcp.call_tool("situation", {})).content[0].text)
                print("situation:", sit["seat"], sit["hub"], sit["clock"], "fleet", [t["truck"] for t in sit["fleet"]])
                for _ in range(3):
                    nxt = json.loads((await mcp.call_tool("end_turn", {})).content[0].text)
                loads = json.loads((await mcp.call_tool("list_loads", {})).content[0].text)
                print("after 3 turns:", nxt["clock"], "open offers", len(loads["loads"]))
                bad = await mcp.call_tool("get_truck", {"truck": "B1"})
                print("other seat's truck refused:", bad.isError, bad.content[0].text[:80])
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(main())
