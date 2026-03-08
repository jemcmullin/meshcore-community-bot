#!/usr/bin/env python3
"""Community wrapper around MeshCore Bot web viewer.

Adds a /community page and /api/community/metrics endpoint at runtime,
without modifying meshcore-bot submodule files.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

from flask import jsonify, render_template_string

ROOT = Path(__file__).resolve().parent.parent
SUBMODULE_PATH = ROOT / "meshcore-bot"
if str(SUBMODULE_PATH) not in sys.path:
    sys.path.insert(0, str(SUBMODULE_PATH))

from modules.web_viewer.app import BotDataViewer  # noqa: E402


COMMUNITY_PAGE_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Community Metrics</title>
  <style>
    :root { --bg:#f5f7f2; --ink:#1f2a1f; --card:#ffffff; --muted:#4c5b4c; --line:#d6ddd2; --a:#1f6f5f; }
    body { margin:0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; background:var(--bg); color:var(--ink); }
    .wrap { max-width: 980px; margin: 24px auto; padding: 0 16px; }
    h1 { margin: 0 0 12px; }
    .meta { color: var(--muted); margin-bottom: 16px; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); gap: 12px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px; }
    table { width:100%; border-collapse: collapse; }
    th, td { text-align:left; padding:6px; border-bottom:1px solid var(--line); font-size:14px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#eaf2ea; border:1px solid var(--line); margin-right:6px; margin-bottom:6px; font-size:12px; }
    nav { background:var(--card); border-bottom:1px solid var(--line); padding:0 16px; display:flex; gap:16px; align-items:center; }
    nav a { display:inline-block; padding:12px 8px; color:var(--a); text-decoration:none; font-size:14px; border-bottom:2px solid transparent; }
    nav a.active { border-bottom-color:var(--a); font-weight:600; }
    nav a:hover { color:var(--ink); }
  </style>
</head>
<body>
  <nav>
    <a href="/">Dashboard</a>
    <a href="/community" class="active">Community</a>
  </nav>
  <div class=\"wrap\">
    <h1>Community Metrics</h1>
    <div class=\"meta\" id=\"meta\">Loading...</div>
    <div class=\"grid\">
      <section class=\"card\">
        <h3>Network</h3>
        <div id=\"network\"></div>
      </section>
      <section class=\"card\">
        <h3>Coordination (Last 60m)</h3>
        <div id=\"coord\"></div>
      </section>
      <section class=\"card\" style=\"grid-column: 1/-1;\">
        <h3>Top Repeaters</h3>
        <table>
          <thead><tr><th>Node</th><th>Fan-in</th><th>Seen</th><th>Avg depth</th><th title="log1p(fan_in)/log1p(max_fan_in)">Reach</th><th title="(avg_depth-1)/(max_depth-1)">Depth frac</th><th title="reach × depth_frac">Score</th><th>Coverage</th></tr></thead>
          <tbody id="repeaters"></tbody>
        </table>
        <p id="repeaters-caption" style="font-size:12px;color:var(--muted);margin:6px 0 0;"></p>
      </section>
      <section class=\"card\" style=\"grid-column: 1/-1;\">
        <h3>Recent Bid Events</h3>
        <table>
          <thead><tr><th>Time</th><th>Stage</th><th>Score</th><th>Details</th></tr></thead>
          <tbody id=\"events\"></tbody>
        </table>
      </section>
    </div>
  </div>
<script>
async function refresh() {
  try {
    const res = await fetch('/api/community/metrics');
    if (!res.ok) {
      const text = await res.text();
      document.getElementById('meta').textContent = `Error ${res.status}: ${text.slice(0, 300)}`;
      return;
    }
    const data = await res.json();
    if (data.error) {
      document.getElementById('meta').textContent = `API error: ${data.error}`;
      return;
    }

    document.getElementById('meta').textContent = `Updated ${new Date(data.timestamp * 1000).toLocaleTimeString()} | DB: ${data.db_path}`;
    document.getElementById('network').innerHTML = `
      <div><b>Total known nodes:</b> ${data.network.total_nodes}</div>
      <div><b>Last hour bid events:</b> ${data.coordination.event_count}</div>
    `;

    document.getElementById('coord').innerHTML = Object.entries(data.coordination.stage_counts)
      .map(([k, v]) => `<span class=\"pill\">${k}: ${v}</span>`).join('') || '<span class=\"pill\">No events</span>';

    const reps = data.top_repeaters;
    document.getElementById('repeaters').innerHTML = reps.map(r => `
      <tr>
        <td class="mono">${r.node}</td>
        <td>${r.fan_in}</td>
        <td>${r.obs}</td>
        <td>${r.avg_depth}</td>
        <td>${r.reach.toFixed(3)}</td>
        <td>${r.depth_frac.toFixed(3)}</td>
        <td>${r.score.toFixed(3)}</td>
        <td>${r.coverage_pct.toFixed(0)}%</td>
      </tr>
    `).join('') || '<tr><td colspan="8">No repeater data</td></tr>';
    if (reps.length && reps[0]._max_fan_in !== undefined) {
      document.getElementById('repeaters-caption').textContent =
        `max fan-in: ${reps[0]._max_fan_in} · max depth: ${reps[0]._max_depth} · score = reach × depth_frac`;
    }

    document.getElementById('events').innerHTML = data.coordination.recent_events.map(e => `
      <tr>
        <td>${new Date(e.timestamp * 1000).toLocaleTimeString()}</td>
        <td class=\"mono\">${e.stage}</td>
        <td>${e.score === null ? 'n/a' : e.score.toFixed(3)}</td>
        <td class=\"mono\">${e.summary}</td>
      </tr>
    `).join('') || '<tr><td colspan=\"4\">No recent coordination events</td></tr>';
  } catch (err) {
    document.getElementById('meta').textContent = `Load failed: ${err}`;
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


SCORE_RE = re.compile(r"\\bscore=([0-9]*\\.?[0-9]+)")


def _safe_float(val):
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_score(summary: str):
    if not summary:
        return None
    m = SCORE_RE.search(summary)
    if not m:
        return None
    return _safe_float(m.group(1))


def install_community_routes(viewer: BotDataViewer) -> None:
    """Attach /community page + JSON metrics API to existing viewer app."""

    @viewer.app.after_request
    def inject_community_nav(response):
        """Append a Community nav item to existing viewer pages.

        Injected client-side to avoid template changes in the submodule.
        """
        try:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                return response

            body = response.get_data(as_text=True)
            if "href=\"/community\"" in body:
                return response

            nav_script = """
<script>
document.addEventListener('DOMContentLoaded', function () {
  if (document.querySelector('a[href="/community"]')) return;
  var nav = document.querySelector('#navbarNav .navbar-nav');
  if (!nav) return;
  var li = document.createElement('li');
  li.className = 'nav-item';
  var a = document.createElement('a');
  a.className = 'nav-link';
  a.href = '/community';
  a.textContent = 'Community';
  if (window.location.pathname === '/community') {
  a.classList.add('active');
  }
  li.appendChild(a);
  nav.appendChild(li);
});
</script>
"""
            if "</body>" in body:
                body = body.replace("</body>", nav_script + "\n</body>", 1)
                response.set_data(body)
                response.headers["Content-Length"] = str(len(body.encode("utf-8")))
        except Exception:
            # Never fail page delivery due to nav injection issues.
            return response
        return response

    @viewer.app.route("/community")
    def community_page():
        return render_template_string(COMMUNITY_PAGE_HTML)

    @viewer.app.route("/api/community/metrics")
    def community_metrics():
        try:
            return _community_metrics_impl(viewer)
        except Exception as exc:
            import traceback
            return jsonify({"error": str(exc), "trace": traceback.format_exc()}), 500


def _community_metrics_impl(viewer):
    now = time.time()
    top_repeaters = []
    stage_counts = {"bid": 0, "assigned_us": 0, "assigned_other": 0, "fallback": 0}
    recent_events = []
    event_count = 0
    total_nodes = 0

    conn = sqlite3.connect(viewer.db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in cur.fetchall()}

        # Total distinct sender nodes observed in mesh connections
        if "mesh_connections" in tables:
            cur.execute("SELECT COUNT(DISTINCT from_prefix) AS total_nodes FROM mesh_connections")
            row = cur.fetchone()
            total_nodes = int((row["total_nodes"] if row else 0) or 0)
        total_nodes = max(total_nodes, 1)

        # Top repeaters: score = (log1p(fan_in) / log1p(max_fan_in)) × depth_fraction  [0, 1]
        # fan_in         = distinct origin nodes routing through this relay
        # max_fan_in     = highest fan_in in the network (normalises reach to 0-1)
        # depth_fraction = (avg_hop_position - 1) / (max_depth - 1)
        #                  0 for hop-1 feeders, 1 for deepest relay in network
        # Scale: backbone ~0.65-1.0, distributor ~0.30-0.65, feeder ~0.
        if "mesh_connections" in tables:
            cur.execute(
                """
                SELECT to_prefix,
                       COUNT(DISTINCT from_prefix) AS fan_in,
                       SUM(observation_count) AS obs,
                       AVG(COALESCE(avg_hop_position, 1)) AS avg_depth,
                       (SELECT MAX(d)
                        FROM (SELECT AVG(COALESCE(avg_hop_position, 1)) AS d
                              FROM mesh_connections
                              GROUP BY to_prefix)) AS max_depth,
                       (SELECT MAX(c)
                        FROM (SELECT COUNT(DISTINCT from_prefix) AS c
                              FROM mesh_connections
                              GROUP BY to_prefix)) AS max_fan_in
                FROM mesh_connections
                GROUP BY to_prefix
                ORDER BY fan_in DESC
                LIMIT 50
                """
            )
            rows = cur.fetchall()
            max_depth   = max(float(rows[0]["max_depth"] or 1.0), 1.0) if rows else 1.0
            depth_range = max(max_depth - 1, 0.001)
            max_fan_in  = max(int(rows[0]["max_fan_in"] or 1), 1) if rows else 1
            log_max_fan = math.log1p(max_fan_in)
            for r in rows:
                fan_in     = int(r["fan_in"] or 0)
                obs        = int(r["obs"] or 0)
                avg_depth  = float(r["avg_depth"] or 1)
                depth_frac = max(avg_depth - 1, 0) / depth_range
                reach      = math.log1p(fan_in) / log_max_fan
                score      = reach * depth_frac
                top_repeaters.append(
                    {
                        "node": (r["to_prefix"] or "").upper(),
                        "fan_in": fan_in,
                        "obs": obs,
                        "avg_depth": round(avg_depth, 2),
                        "reach": round(reach, 3),
                        "depth_frac": round(depth_frac, 3),
                        "score": round(score, 3),
                        "coverage_pct": (fan_in / total_nodes) * 100.0,
                    }
                )
            # Re-sort by score (SQL ordered by fan_in; score order differs)
            top_repeaters.sort(key=lambda x: x["score"], reverse=True)
            top_repeaters = top_repeaters[:10]
            if top_repeaters:
                top_repeaters[0]["_max_fan_in"] = max_fan_in
                top_repeaters[0]["_max_depth"] = round(max_depth, 2)

        # Last 60 minutes of coordination snapshots injected by community layer
        if "packet_stream" in tables:
            cutoff = now - (60 * 60)
            cur.execute(
                """
                SELECT timestamp, data
                FROM packet_stream
                WHERE type = 'command' AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 500
                """,
                (cutoff,),
            )
            for r in cur.fetchall():
                try:
                    payload = json.loads(r["data"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

                cmd = (payload.get("command") or "").strip()
                if not cmd.startswith("coord_"):
                    continue

                stage = cmd.replace("coord_", "", 1)
                if stage not in stage_counts:
                    stage_counts[stage] = 0
                stage_counts[stage] += 1
                event_count += 1

                summary = payload.get("response") or ""
                ev_score = _extract_score(summary)
                recent_events.append(
                    {
                        "timestamp": float(r["timestamp"]),
                        "stage": stage,
                        "score": ev_score,
                        "summary": summary,
                    }
                )

    finally:
        conn.close()

    return jsonify(
        {
            "timestamp": now,
            "db_path": viewer.db_path,
            "network": {
                "total_nodes": total_nodes,
            },
            "top_repeaters": top_repeaters,
            "coordination": {
                "event_count": event_count,
                "stage_counts": stage_counts,
                "recent_events": recent_events[:50],
            },
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MeshCore Community Data Viewer")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--config",
        default="config.ini",
        help="Path to configuration file (default: config.ini)",
    )
    args = parser.parse_args()

    viewer = BotDataViewer(config_path=args.config)
    install_community_routes(viewer)
    viewer.logger.info("Community routes installed: /community, /api/community/metrics")
    viewer.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
