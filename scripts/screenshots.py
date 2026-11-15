"""Capture screenshots of the running stack for the report (docs/img/*.png).

Runs inside the Playwright container on the compose network (see
scripts/screenshots.sh). Logs in through nginx exactly like a user, switches
chaos modes and photographs the dashboards while each problem is visible.

Inside the container `localhost:80` is forwarded to nginx, so redirects that
Grafana builds from its root_url (http://localhost/grafana/) keep working.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(os.getenv("OUT_DIR", "/out"))
DASH_DIR = Path(os.getenv("DASH_DIR", "/dash"))
TEXT_DIR = Path(os.getenv("TEXT_DIR", "/text"))
BASE = "http://localhost"
TEMPO = "http://tempo:3200"
CHAOS_WAIT = int(os.getenv("CHAOS_WAIT_S", "150"))
MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"

ADMIN = (os.environ["AUTH_ADMIN_USER"], os.environ["AUTH_ADMIN_PASSWORD"])
VIEWER = (os.environ["AUTH_VIEWER_USER"], os.environ["AUTH_VIEWER_PASSWORD"])


# --------------------------------------------------------------- helpers
def start_forwarder(listen_port: int = 80, target: tuple[str, int] = ("nginx", 80)) -> None:
    """Forward 127.0.0.1:80 -> nginx:80 in a background thread."""

    async def pipe(reader, writer):
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()

    async def handle(client_r, client_w):
        up_r, up_w = await asyncio.open_connection(*target)
        await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))

    async def main():
        server = await asyncio.start_server(handle, "127.0.0.1", listen_port)
        async with server:
            await server.serve_forever()

    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    time.sleep(0.5)


def panel_id(dashboard_file: str, title: str) -> int:
    data = json.loads((DASH_DIR / dashboard_file).read_text())
    for p in data["panels"]:
        if p.get("title") == title:
            return p["id"]
    raise KeyError(title)


def dash_url(uid: str, *, panel: int | None = None, rng: str = "now-30m", extra: str = "") -> str:
    url = f"{BASE}/grafana/d/{uid}?orgId=1&kiosk&theme=light&from={rng}&to=now{extra}"
    if panel is not None:
        url += f"&viewPanel={panel}"
    return url


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def login(page: Page, user: tuple[str, str]) -> None:
    page.goto(f"{BASE}/login?next=/grafana/")
    page.fill("#username", user[0])
    page.fill("#password", user[1])
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/grafana/.*"), timeout=30000)


def shot(page: Page, name: str, url: str, *, height: int = 1000, wait_ms: int = 9000, width: int = 1600) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url)
    page.wait_for_timeout(wait_ms)
    page.screenshot(path=str(OUT / f"{name}.png"))
    log(f"saved {name}.png")


def set_chaos(page: Page, mode: str) -> None:
    resp = page.request.post(f"{BASE}/chaos", data={"mode": mode})
    log(f"chaos -> {mode}: HTTP {resp.status}")


def tempo_search(query: str, since_s: int = 300) -> str | None:
    now = int(time.time())
    q = urllib.parse.urlencode({"q": query, "limit": 20, "start": now - since_s, "end": now})
    with urllib.request.urlopen(f"{TEMPO}/api/search?{q}", timeout=10) as r:
        traces = json.load(r).get("traces", [])
    if not traces:
        return None
    best = max(traces, key=lambda t: t.get("durationMs", 0))
    return best["traceID"].rjust(32, "0")


def explore_url(datasource: str, query: dict, rng: str = "now-30m") -> str:
    panes = {"a": {"datasource": datasource, "queries": [{"refId": "A", **query}],
                   "range": {"from": rng, "to": "now"}}}
    return (f"{BASE}/grafana/explore?schemaVersion=1&orgId=1&theme=light&panes="
            + urllib.parse.quote(json.dumps(panes, separators=(",", ":"))))


def recent_trace(query: str, min_age_s: int = 120, since_s: int = 900) -> tuple[str, int] | None:
    """A trace at least `min_age_s` old (so our own browsing does not show up next to it)."""
    now = int(time.time())
    q = urllib.parse.urlencode({"q": query, "limit": 50, "start": now - since_s, "end": now})
    with urllib.request.urlopen(f"{TEMPO}/api/search?{q}", timeout=10) as r:
        traces = json.load(r).get("traces", [])
    old = [t for t in traces if int(t["startTimeUnixNano"]) // 10**9 < now - min_age_s]
    if not old:
        return None
    t = max(old, key=lambda t: t.get("durationMs", 0))
    return t["traceID"].rjust(32, "0"), int(t["startTimeUnixNano"]) // 10**6


def shot_trace_logs(page: Page, trace_id: str, start_ms: int) -> None:
    """Logs of one trace; the expanded order-api line shows the link to Tempo."""
    lt = "logs-traces.json"
    rng = f"&from={start_ms - 30000}&to={start_ms + 30000}"
    url = (f"{BASE}/grafana/d/logs-traces?orgId=1&kiosk&theme=light{rng}"
           f"&var-search={trace_id}&viewPanel={panel_id(lt, 'Логи')}")
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.goto(url)
    page.wait_for_timeout(7000)
    line = page.get_by_text('"service": "order-api"').first
    if line.count():
        line.click()
        page.wait_for_timeout(2000)
    page.screenshot(path=str(OUT / "fig_logs_trace_link.png"))
    log("saved fig_logs_trace_link.png")


def render_terminal(page: Page, name: str, title: str, text: str) -> None:
    text = re.sub(r"\x1b\[32m(PASS)\x1b\[0m", lambda m: "\x00g" + m.group(1) + "\x01", text)
    text = re.sub(r"\x1b\[31m(FAIL)\x1b\[0m", lambda m: "\x00r" + m.group(1) + "\x01", text)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    body = html.escape(text).replace("\x00g", '<span class="g">').replace("\x00r", '<span class="r">').replace("\x01", "</span>")
    page.set_viewport_size({"width": 1400, "height": 400})
    page.set_content(f"""<html><head><style>
      body {{ margin: 0; background: #fff; }}
      .term {{ margin: 16px; border-radius: 8px; background: #1e1e1e; color: #d4d4d4; width: 1300px;
               font: 15px/1.45 'DejaVu Sans Mono', monospace; overflow: hidden; }}
      .bar {{ background: #3a3a3a; color: #bbb; padding: 6px 12px; font: 13px sans-serif; }}
      pre {{ margin: 0; padding: 14px 16px; white-space: pre-wrap; }}
      .g {{ color: #4ec9b0; font-weight: bold; }} .r {{ color: #f44747; font-weight: bold; }}
    </style></head><body><div class="term"><div class="bar">{html.escape(title)}</div><pre>{body}</pre></div></body></html>""")
    page.locator(".term").screenshot(path=str(OUT / f"{name}.png"))
    log(f"saved {name}.png")


def render_mermaid(page: Page, name: str, source: str, width: int = 1500) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(f"""<html><head><script src="{MERMAID_JS}"></script>
      <style>body {{ margin: 0; background: #fff; font-family: 'DejaVu Sans', sans-serif; }}
      #d {{ display: inline-block; padding: 16px; }}</style></head>
      <body><div id="d" class="mermaid">{html.escape(source)}</div>
      <script>mermaid.initialize({{ startOnLoad: true, theme: 'neutral',
        flowchart: {{ htmlLabels: true, curve: 'basis' }}, er: {{ fontSize: 14 }} }});</script></body></html>""")
    page.wait_for_selector("#d svg", timeout=30000)
    page.wait_for_timeout(500)
    page.locator("#d").screenshot(path=str(OUT / f"{name}.png"))
    log(f"saved {name}.png")


# ------------------------------------------------------------------ main
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    start_forwarder()
    only = set(sys.argv[1:])  # optional subset: diagrams, terminal, ui

    pg = "postgres.json"
    app = "application.json"
    lt = "logs-traces.json"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.5,
                                  locale="uk-UA", timezone_id="Europe/Kyiv")
        page = ctx.new_page()

        # ------------------------------------------------ static renders
        if not only or "diagrams" in only:
            for f in sorted(TEXT_DIR.glob("*.mmd")):
                render_mermaid(page, f.stem, f.read_text())
        if not only or "terminal" in only:
            for f in sorted(TEXT_DIR.glob("*.txt")):
                title, _, text = f.read_text().partition("\n")
                render_terminal(page, f.stem, title, text)
        if only & {"pg", "logs", "security", "locks"}:
            login(page, ADMIN)
        if "logs" in only:   # logs of an older order-api trace
            found = recent_trace('{ resource.service.name = "order-api" && kind = server && name = "GET /products" }')
            if found:
                shot_trace_logs(page, *found)
        if "security" in only:
            set_chaos(page, "brute_force")
            page.wait_for_timeout(110 * 1000)
            shot(page, "fig_dash_security", dash_url("security", rng="now-15m"), height=2300)
            set_chaos(page, "off")
        if "pg" in only:   # re-shoot only the PostgreSQL dashboard in normal mode
            shot(page, "fig_dash_postgres", dash_url("pg-overview", rng="now-10m"), height=1560)
        if "locks" in only:
            set_chaos(page, "lock_contention")
            page.wait_for_timeout(CHAOS_WAIT * 1000)
            shot(page, "fig_dash_postgres_locks", dash_url("pg-overview", rng="now-15m"), height=1560)
            set_chaos(page, "off")
        if only and "ui" not in only:
            browser.close()
            return 0

        # ---------------------------------------------------- access control
        shot(page, "fig_login", f"{BASE}/grafana/d/pg-overview", width=1200, height=760, wait_ms=1500)
        vctx = browser.new_context(viewport={"width": 1200, "height": 500}, device_scale_factor=1.5)
        vpage = vctx.new_page()
        login(vpage, VIEWER)
        shot(vpage, "fig_forbidden", f"{BASE}/prometheus/", width=1200, height=420, wait_ms=1000)
        vctx.close()

        login(page, ADMIN)
        set_chaos(page, "off")

        # ---------------------------------------------------- normal mode
        shot(page, "fig_dash_postgres", dash_url("pg-overview"), height=1560)
        shot(page, "fig_dash_application", dash_url("app-overview"), height=1560)
        shot(page, "fig_service_graph", dash_url("logs-traces", panel=panel_id(lt, "Карта сервісів (service graph)")),
             height=800)

        # ---------------------------------------------------- slow queries
        set_chaos(page, "slow_queries")
        page.wait_for_timeout(CHAOS_WAIT * 1000)
        shot(page, "fig_latency_exemplars",
             dash_url("app-overview", panel=panel_id(app, "Латентність p50 / p95 / p99 (з exemplars)"), rng="now-15m"),
             height=800)
        shot(page, "fig_top_queries",
             dash_url("pg-overview", panel=panel_id(pg, "Топ-10 повільних запитів (середній час виконання, 5 хв)")),
             height=750)
        shot(page, "fig_alerts", f"{BASE}/grafana/alerting/list?view=list&theme=light", height=1000, wait_ms=6000)
        trace_id = tempo_search('{ resource.service.name = "order-api" && kind = server && duration > 500ms }')
        log(f"slow trace: {trace_id}")
        if trace_id:
            shot(page, "fig_trace",
                 dash_url("logs-traces", panel=panel_id(lt, "Трейс за ID"), extra=f"&var-trace_id={trace_id}"),
                 height=900, wait_ms=8000)
        set_chaos(page, "off")

        # ---------------------------------------------------- lock contention
        page.wait_for_timeout(30000)
        set_chaos(page, "lock_contention")
        page.wait_for_timeout(CHAOS_WAIT * 1000)
        shot(page, "fig_dash_postgres_locks", dash_url("pg-overview", rng="now-15m"), height=1560)
        set_chaos(page, "off")

        # ---------------------------------------------------- traffic spike
        page.wait_for_timeout(30000)
        set_chaos(page, "traffic_spike")
        page.wait_for_timeout(CHAOS_WAIT * 1000)
        shot(page, "fig_queue_spike",
             dash_url("app-overview", panel=panel_id(app, "Довжина черги оплат"), rng="now-15m"), height=700)
        set_chaos(page, "off")

        # ---------------------------------------------------- brute force
        page.wait_for_timeout(20000)
        set_chaos(page, "brute_force")
        page.wait_for_timeout(110 * 1000)
        shot(page, "fig_dash_security", dash_url("security", rng="now-15m"), height=2300)
        set_chaos(page, "off")

        found = recent_trace('{ resource.service.name = "order-api" && kind = server && name = "GET /products" }')
        if found:
            shot_trace_logs(page, *found)

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
