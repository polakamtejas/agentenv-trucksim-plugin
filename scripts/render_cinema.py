"""Watch or record a run's 3D playthrough (viewer/cinema.html), or the trailer cut from the board (viewer/trailer.html).

  serve           serve a site folder (written by collect_runs.py) on 127.0.0.1: open /cinema.html?run=<id> to watch
                  a run, /trailer.html for the trailer
  stills          save PNGs of chosen moments (seconds into the playthrough), to check a look without a whole render
  render          record the playthrough to an mp4: Chrome (through Playwright) opens cinema.html?run=<id>&record=1, the
                  page draws each frame when told to (window.cinema.seek) and waits for its map tiles, and the frames go
                  to ffmpeg. So the video doesn't depend on how fast a frame draws: the same run always gives the same frames.
  trailer-stills  stills of the trailer
  trailer         record the trailer the same way (trailer.html?record=1; it reads the board-v11 runs from site.json)

Usage (from the repo root):
  .venv/bin/python scripts/render_cinema.py serve  results/site/board-v11 [--port 8793]
  .venv/bin/python scripts/render_cinema.py stills results/site/board-v11 <run id> 12 40 95 [--dir results/videos/stills]
  .venv/bin/python scripts/render_cinema.py render results/site/board-v11 <run id> [--out results/videos/<run id>.mp4]
                                                   [--fps 30] [--from 0] [--to <seconds>]
  .venv/bin/python scripts/render_cinema.py trailer-stills results/site/board-v11 3 10 22 [--dir results/videos/stills]
  .venv/bin/python scripts/render_cinema.py trailer results/site/board-v11 [--out results/videos/trucksim-trailer.mp4]
Needs: the `video` extra (playwright), Google Chrome, ffmpeg. The pages fetch their map tiles from OpenFreeMap (and the
trailer its aerial imagery from USGS), so the machine needs the internet."""

import argparse
import functools
import http.server
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1920, 1080
CHROME_ARGS = ["--ignore-gpu-blocklist", "--enable-gpu-rasterization", "--use-angle=metal"]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve(site: Path, port: int, page: str = "cinema.html") -> http.server.ThreadingHTTPServer:
    if not (site / page).exists():
        sys.exit(f"{site} has no {page}: rebuild it with scripts/collect_runs.py (or replay_page.write_cinema)")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(QuietHandler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def open_player(pw, port: int, page_query: str):
    """Open a page in record mode (e.g. "cinema.html?run=<id>") and wait for its window.cinema."""
    browser = pw.chromium.launch(channel="chrome", headless=True, args=CHROME_ARGS)
    page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
    page.on("console", lambda m: print(f"[page {m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: print(f"[page error] {e}"))
    page.on("response", lambda r: print(f"[http {r.status}] {r.url}") if r.status >= 400 else None)
    page.goto(f"http://127.0.0.1:{port}/{page_query}{'&' if '?' in page_query else '?'}record=1")
    page.wait_for_function("window.cinema && (window.cinema.ready || window.cinema.error)", timeout=180_000)
    error = page.evaluate("window.cinema.error || null")
    if error:
        sys.exit(f"the player failed: {error}")
    gl = page.evaluate("""() => { const g = document.createElement('canvas').getContext('webgl2');
        const x = g && g.getExtension('WEBGL_debug_renderer_info'); return x ? g.getParameter(x.UNMASKED_RENDERER_WEBGL) : 'unknown'; }""")
    print(f"WebGL renderer: {gl}; playthrough {page.evaluate('window.cinema.duration'):.1f} s")
    return browser, page


def stills(site: Path, page_query: str, name: str, moments: list[float], out_dir: Path) -> None:
    from playwright.sync_api import sync_playwright
    httpd = serve(site, 0, page_query.split("?")[0])
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser, page = open_player(pw, httpd.server_address[1], page_query)
        for t in moments:
            page.evaluate("t => cinema.seek(t)", t)
            path = out_dir / f"{name}-{t:07.2f}.png"
            page.screenshot(path=str(path))
            print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
        browser.close()
    httpd.shutdown()


def render(site: Path, page_query: str, out: Path, fps: int, start: float, end: float | None) -> None:
    from playwright.sync_api import sync_playwright
    httpd = serve(site, 0, page_query.split("?")[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser, page = open_player(pw, httpd.server_address[1], page_query)
        end = page.evaluate("window.cinema.duration") if end is None else end
        frames = int((end - start) * fps)
        ffmpeg = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
                                   "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                                  stdin=subprocess.PIPE)
        began = time.time()
        for i in range(frames):
            page.evaluate("t => cinema.seek(t)", start + i / fps)
            ffmpeg.stdin.write(page.screenshot(type="jpeg", quality=93))
            if i % (fps * 10) == 0 and i:
                rate = i / (time.time() - began)
                print(f"frame {i}/{frames} ({(start + i / fps):.0f} s of {end:.0f}) · {rate:.1f} frames/s · {(frames - i) / rate / 60:.1f} min left", flush=True)
        ffmpeg.stdin.close()
        if ffmpeg.wait():
            sys.exit("ffmpeg failed")
        browser.close()
    httpd.shutdown()
    print(f"wrote {out} ({frames} frames, {out.stat().st_size / 1e6:.1f} MB) in {(time.time() - began) / 60:.1f} min")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("site", type=Path); s.add_argument("--port", type=int, default=8793)
    st = sub.add_parser("stills"); st.add_argument("site", type=Path); st.add_argument("run"); st.add_argument("moments", type=float, nargs="+")
    st.add_argument("--dir", type=Path, default=ROOT / "results" / "videos" / "stills")
    r = sub.add_parser("render"); r.add_argument("site", type=Path); r.add_argument("run"); r.add_argument("--out", type=Path)
    r.add_argument("--fps", type=int, default=30); r.add_argument("--from", dest="start", type=float, default=0.0); r.add_argument("--to", dest="end", type=float)
    ts = sub.add_parser("trailer-stills"); ts.add_argument("site", type=Path); ts.add_argument("moments", type=float, nargs="+")
    ts.add_argument("--dir", type=Path, default=ROOT / "results" / "videos" / "stills")
    tr = sub.add_parser("trailer"); tr.add_argument("site", type=Path); tr.add_argument("--out", type=Path, default=ROOT / "results" / "videos" / "trucksim-trailer.mp4")
    tr.add_argument("--fps", type=int, default=30); tr.add_argument("--from", dest="start", type=float, default=0.0); tr.add_argument("--to", dest="end", type=float)
    a = ap.parse_args()
    if a.cmd == "serve":
        httpd = serve(a.site, a.port)
        print(f"serving {a.site} at http://127.0.0.1:{httpd.server_address[1]}/cinema.html (Ctrl-C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            httpd.shutdown()
    elif a.cmd == "stills":
        stills(a.site, f"cinema.html?run={quote(a.run)}", a.run, a.moments, a.dir)
    elif a.cmd == "render":
        render(a.site, f"cinema.html?run={quote(a.run)}", a.out or ROOT / "results" / "videos" / f"{a.run}.mp4", a.fps, a.start, a.end)
    elif a.cmd == "trailer-stills":
        stills(a.site, "trailer.html", "trailer", a.moments, a.dir)
    else:
        render(a.site, "trailer.html", a.out, a.fps, a.start, a.end)


if __name__ == "__main__":
    main()
