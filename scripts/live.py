"""Watch TruckSimEnv games while they run: a local dashboard of every game agent-env is running, each with a link to
the full viewer following it live.

    .venv/bin/python scripts/live.py            # then open http://127.0.0.1:8790

It finds the games in agent-env's @local document store (read-only): each running task instance, its model and its
env's address. Each game's summary comes from the env's /state.json. The live viewer needs the env's /replay.json,
which env images built before 2026-10-20 23:30 UTC don't have; their games show the summary only, and say so."""

import json
import mimetypes
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "agentenv_trucksim" / "viewer" / "app.html"
SITES = ROOT / "results" / "site"
DB = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "agent-env" / "document_store" / "local.db"
HOST, PORT = "127.0.0.1", 8790
RECENT = 40   # also list this many most recent finished TruckSim runs

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>TruckSimEnv live</title><style>
body{margin:0;background:#f9f9f7;color:#0b0b0b;font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:18px 22px}
h1{font-size:19px;margin:0 0 4px}.sub{color:#52514e;margin-bottom:14px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.c{background:#fcfcfb;border-radius:10px;box-shadow:0 0 0 1px rgba(11,11,11,.1);padding:12px 14px}.c h2{font-size:14px;margin:0 0 2px}
.m{color:#52514e;font-size:12px}.bar{height:6px;background:#e1e0d9;border-radius:3px;margin:8px 0}.bar i{display:block;height:6px;border-radius:3px;background:#52514e}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin-top:6px}td{padding:3px 4px;border-bottom:1px solid #e1e0d9}
.me td{font-weight:650}a{color:#2a78d6}.done{opacity:.75}.sw{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px}
</style></head><body><h1>TruckSimEnv · live</h1><div class="sub" id="sub">loading</div><div class="sub" id="sites"></div><div class="g" id="g"></div><script>
const C={A:"#2a78d6",B:"#eb6834",C:"#1baf7a"};const $=v=>(v<0?"−":"")+"$"+Math.abs(Math.round(v)).toLocaleString("en-US");
async function tick(){const r=await (await fetch("games.json")).json();
document.getElementById("sub").textContent=`${r.games.filter(x=>x.status==="running").length} running · refreshed ${new Date().toLocaleTimeString()}`;
document.getElementById("g").innerHTML=r.games.map(x=>{const s=x.state;if(!s||!s.seats)return `<div class="c"><h2>${x.task}</h2><div class="m">${x.model} · ${x.status}${x.note?" · "+x.note:""}</div></div>`;
const me=(s.model_seats||[])[0],rw=s.rewards&&me?s.rewards[me]:null;
return `<div class="c ${x.status==="running"?"":"done"}"><h2>${x.task}</h2><div class="m">${x.model} · seat ${me} · ${x.status} · ${s.clock}, turn ${Math.min(28,s.turn+1)} of 28</div>
<div class="bar"><i style="width:${Math.min(100,s.turn/28*100)}%"></i></div>
<table>${Object.entries(s.seats).map(([k,v])=>`<tr class="${k===me?"me":""}"><td><i class="sw" style="background:${C[k]}"></i>${k} ${v.hub}${k===me?" (model)":""}</td><td>${$(v.profit_usd)}</td><td>${v.delivered} loads</td></tr>`).join("")}</table>
<div class="m" style="margin-top:6px">${rw!=null?`reward so far ${rw.toFixed(3)} (naive ${$(s.references.naive_usd)}, anchor ${$(s.references.anchor_usd)})`:""}</div>
<div class="m">${x.live?`<a href="game/${x.port}/viewer">open the live viewer</a>`:(x.note||"")}</div></div>`}).join("")||"<div class='m'>No TruckSim runs found.</div>";
document.getElementById("sites").innerHTML=r.sites.length?"Replays: "+r.sites.map(s=>`<a href="site/${s}/">${s}</a>`).join(" · "):"";}
tick();setInterval(tick,4000);</script></body></html>"""


def games() -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("select doc from docs_task_instances where json_extract(doc, '$.task_id') like '%trucksim/ts-%' "
                       "or json_extract(doc, '$.task_id') like '%trucksim/showmatch%' "
                       "order by json_extract(doc, '$.created_at_utc') desc limit ?", (RECENT,)).fetchall()
    out = []
    for (raw,) in rows:
        d = json.loads(raw)
        ctx = d.get("context") or {}
        envs = [e for e in ctx.get("deployed_envs", []) if e.get("env_id") == "trucksim"]
        port = urlparse(envs[0]["environment_card_url"]).port if envs else None
        game = {"task": d["instance_id"].rsplit("/", 1)[-1], "status": d["status"], "model": ctx.get("agent_model") or "?",
                "port": port, "state": None, "live": False, "note": None}
        if d["status"] != "running":
            game["note"] = "finished: the env is gone; replay it from its match files (scripts/collect_runs.py)"
            v = (ctx.get("metadata") or {}).get("verifications", {}).get("trucksim")
            if v:
                game["note"] = f"finished, reward {v['score']:.3f}"
        elif port:
            try:
                game["state"] = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state.json", timeout=3).read())
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/replay.json", timeout=5).read(1)
                    game["live"] = True
                except urllib.error.HTTPError:
                    game["note"] = "this env image predates live replays: summary only"
            except (urllib.error.URLError, TimeoutError):
                game["note"] = "the env isn't answering"
        out.append(game)
    out.sort(key=lambda g: (g["status"] != "running", g["task"]))
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if path == "/games.json":
            return self._send(200, json.dumps({"games": games(), "sites": sorted(p.name for p in SITES.glob("*") if p.is_dir())}).encode(), "application/json")
        if path == "/app.html":
            return self._send(200, APP.read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/site/"):                    # results/site/<name>/..., built by scripts/collect_runs.py
            rel = path.removeprefix("/site/")
            target = (SITES / rel).resolve()
            if not str(target).startswith(str(SITES.resolve())):
                return self._send(403, b"outside results/site", "text/plain")
            if target.is_dir():
                if not path.endswith("/"):
                    self.send_response(301); self.send_header("Location", path + "/"); self.end_headers(); return
                target = target / "index.html"
            if not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = "text/html; charset=utf-8" if target.suffix == ".html" else (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            return self._send(200, target.read_bytes(), ctype)
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "game" and parts[1].isdigit():
            port = int(parts[1])
            if parts[2] == "viewer":
                self.send_response(302)
                self.send_header("Location", f"/app.html#/live/{port}")
                self.end_headers()
                return
            if parts[2] == "replay.json":
                try:
                    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/replay.json", timeout=10).read()
                    return self._send(200, body, "application/json")
                except urllib.error.HTTPError as e:
                    return self._send(e.code, e.read(), "application/json")
                except urllib.error.URLError as e:
                    return self._send(502, json.dumps({"error": f"the env on port {port} isn't answering: {e}"}).encode(), "application/json")
        return self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    if not DB.exists():
        sys.exit(f"no agent-env @local store at {DB}")
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        sys.exit(f"can't listen on {HOST}:{PORT}: {e} (is another live.py running?)")
    print(f"TruckSimEnv live: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
