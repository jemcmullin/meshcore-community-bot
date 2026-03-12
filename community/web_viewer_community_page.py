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

# Ensure ROOT is in sys.path for Docker and package resolution
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))
COMMUNITY_PATH = ROOT / "community"
if str(COMMUNITY_PATH) not in sys.path:
    sys.path.insert(0, str(COMMUNITY_PATH))

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
        <h3>Bot Performance (Last 24hr)</h3>
        <div id=\"coord\"></div>
      </section>
      <section class=\"card\">
        <h3>Direct Messages (Last 24hr)</h3>
        <div id=\"dm-stats\"></div>
      </section>
      <section class=\"card\" style=\"grid-column: 1/-1;\">
        <h3>Top Repeaters <span style="font-size:12px;color:var(--muted);">(this bot's bid scoring perspective)</span></h3>
        <table>
          <thead><tr>
            <th>Top</th>
            <th>Name</th>
            <th title="Active &lt;24h · Recent 24–48h · Stale &gt;48h">Status</th>
            <th title="Stored outbound hops from this bot to relay">Hops</th>
            <th title="Unique source nodes routing through this relay">Links</th>
            <th title="Links as % of total known nodes">Coverage</th>
            <th title="Time since relay last seen in mesh traffic">Last</th>
            <th title="Estimated delivery score. Hover row for component breakdown.">Score</th>
          </tr></thead>
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

    const coord = data.coordination;
    const sc = coord.stage_counts;
    const total = sc.bid || 0;
    const won = sc.assigned_us || 0;
    const lost = sc.assigned_other || 0;
    const fallback = sc.fallback || 0;
    const winRate = total > 0 ? ((won / total) * 100).toFixed(0) : 0;
    const fallbackRate = total > 0 ? ((fallback / total) * 100).toFixed(0) : 0;
    const avgScore = coord.avg_score !== null && coord.avg_score !== undefined ? coord.avg_score.toFixed(3) : 'n/a';
    
    if (total === 0) {
      document.getElementById('coord').innerHTML = '<div style=\"color:var(--muted)\">No coordination events in last hour</div>';
    } else {
      document.getElementById('coord').innerHTML = `
        <div><b>Bids:</b> ${total} (won ${won}, lost ${lost})</div>
        <div><b>Win rate:</b> ${winRate}%</div>
        <div><b>Avg score:</b> ${avgScore}</div>
        <div><b>Fallback:</b> ${fallback} (${fallbackRate}%)</div>
      `;
    }

    // DM Statistics
    const dm = data.dm_stats || {};
    const totalDMs = dm.total_dms || 0;
    const dmsDelivered = dm.dms_with_response || 0;
    const deliveryRate = totalDMs > 0 ? ((dmsDelivered / totalDMs) * 100).toFixed(0) : 0;
    
    if (totalDMs === 0) {
      document.getElementById('dm-stats').innerHTML = '<div style=\"color:var(--muted)\">No DMs sent in last hour</div>';
    } else {
      let dmHtml = `
        <div><b>DMs sent:</b> ${totalDMs}</div>
        <div><b>Delivery confirmed:</b> ${dmsDelivered} (${deliveryRate}%)</div>
      `;
      
      // Show top 3 users with best delivery rate
      if (dm.top_users && dm.top_users.length > 0) {
        dmHtml += '<div style=\"margin-top:8px;font-size:12px;color:var(--muted)\"><b>Top delivery:</b></div>';
        dm.top_users.forEach(u => {
          const statusColor = u.rate >= 80 ? '#2d8a4e' : u.rate >= 50 ? '#b07d1a' : '#888';
          dmHtml += `<div style=\"font-size:11px\"><span style=\"color:${statusColor};font-weight:bold\">${u.rate}%</span> ${u.user} (${u.delivered}/${u.sent})</div>`;
        });
      }
      
      // Show bottom 3 users with worst delivery rate
      if (dm.bottom_users && dm.bottom_users.length > 0) {
        dmHtml += '<div style=\"margin-top:6px;font-size:12px;color:var(--muted)\"><b>Needs attention:</b></div>';
        dm.bottom_users.forEach(u => {
          const statusColor = u.rate >= 80 ? '#2d8a4e' : u.rate >= 50 ? '#b07d1a' : '#c44';
          dmHtml += `<div style=\"font-size:11px\"><span style=\"color:${statusColor};font-weight:bold\">${u.rate}%</span> ${u.user} (${u.delivered}/${u.sent})</div>`;
        });
      }
      
      document.getElementById('dm-stats').innerHTML = dmHtml;
    }

    const reps = data.top_repeaters;
    document.getElementById('repeaters').innerHTML = reps.map(r => {
      const ah = r.age_hours;
      const statusColor = ah < 24 ? '#2d8a4e' : ah < 48 ? '#b07d1a' : '#888';
      const statusLabel = ah < 24 ? 'Active' : ah < 48 ? 'Recent' : 'Stale';
      const oh = r.out_hops;
      const pathLabel = oh === null || oh === undefined
        ? '?' : oh === 0 ? 'direct' : `${oh} hop${oh > 1 ? 's' : ''}`;
      const lastSeen = ah === null || ah === undefined ? '?'
        : ah < 1 ? '<1h ago' : ah < 24 ? `${Math.floor(ah)}h ago` : `${Math.floor(ah/24)}d ago`;
      const tip = `infra=${r.infra.toFixed(2)} hop=${r.hop_score.toFixed(2)} path_bonus=${r.path_bonus.toFixed(2)} fresh=${r.freshness.toFixed(2)}`;
      const name = r.name ? r.name : '';
      return `
      <tr title="${tip}">
        <td class="mono">${r.node}</td>
        <td>${name}</td>
        <td style="color:${statusColor};font-weight:bold">${statusLabel}</td>
        <td>${pathLabel}</td>
        <td>${r.fan_in}</td>
        <td>${r.coverage_pct.toFixed(0)}%</td>
        <td>${lastSeen}</td>
        <td>${r.significance.toFixed(2)}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="8">No repeater data</td></tr>';
    document.getElementById('repeaters-caption').textContent =
      'Status: Active <24h · Recent 24–48h · Stale >48h  ·  Score: hover row for component breakdown';

    document.getElementById('events').innerHTML = data.coordination.recent_events.map(e => `
      <tr>
        <!-- Timestamp is already local time from the database, so display as-is without timezone conversion -->
        <td>${new Date(e.timestamp * 1000).toLocaleTimeString('en-US', { hour12: true })}</td>
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
    import re
    from community.config import ScoringConfig
    scoring_cfg = ScoringConfig()
    
    now = time.time()
    # Calculate local timezone offset in seconds
    # now_dt = datetime.datetime.now()
    # now_utc = datetime.datetime.utcnow()
    # tz_offset_sec = int((now_dt - now_utc).total_seconds())
    top_repeaters = []
    stage_counts = {"bid": 0, "assigned_us": 0, "assigned_other": 0, "fallback": 0}
    recent_events = []
    event_count = 0
    total_nodes = 0
    dm_stats = {
        "total_dms": 0,
        "dms_with_response": 0,
        "top_users": [],
        "bottom_users": []
    }

    def _extract_score_from_summary(summary):
      m = re.search(r"\bscore=([0-9]*\.?[0-9]+)", summary)
      score = float(m.group(1)) if m else None
      cleaned_summary = re.sub(r"\bscore=([0-9]*\.?[0-9]+)", "", summary).strip()
      return score, cleaned_summary

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

        # Estimated bid score with path-familiarity weights.
        # Path bonus is message-specific, so repeater rows use 0.0.
        if "mesh_connections" in tables and "complete_contact_tracking" in tables:
          # Calculate local timezone offset in hours
          
          cur.execute(
            f"""
            SELECT COALESCE(mc.to_public_key, mc.to_prefix) AS node,
                 COUNT(DISTINCT mc.from_public_key) AS fan_in,
                 CAST((julianday('now', 'localtime') - julianday(MAX(mc.last_seen))) * 24 AS REAL) AS age_hours,
                 (SELECT MAX(c)
                FROM (SELECT COUNT(DISTINCT from_public_key) AS c
                    FROM mesh_connections
                    GROUP BY to_public_key)) AS max_fan_in,
                 cct.out_hops,
                 cct2.name
            FROM mesh_connections mc
            LEFT JOIN (
              SELECT public_key,
                 MAX(hop_count) AS out_hops
              FROM complete_contact_tracking
              WHERE out_path_len IS NOT NULL
              GROUP BY public_key
            ) AS cct ON (
              (mc.to_public_key IS NOT NULL AND cct.public_key = mc.to_public_key)
              OR (mc.to_public_key IS NULL AND cct.public_key LIKE mc.to_prefix || '%')
            )
            LEFT JOIN (
              SELECT public_key, MAX(name) AS name
              FROM complete_contact_tracking
              WHERE name IS NOT NULL AND name != ''
              GROUP BY public_key
            ) AS cct2 ON (
              (mc.to_public_key IS NOT NULL AND cct2.public_key = mc.to_public_key)
              OR (mc.to_public_key IS NULL AND cct2.public_key LIKE mc.to_prefix || '%')
            )
            GROUP BY node
            ORDER BY fan_in DESC
            LIMIT 50
            """
          )
          rows = cur.fetchall()
          max_fan_in = max(int(rows[0].get("max_fan_in", 1)), 1) if rows else 1
          log_max_fan = math.log1p(max_fan_in)
          for r in rows:
            fan_in = int(r.get("fan_in", 0))
            out_hops = r.get("out_hops", None)
            age_hours = float(r.get("age_hours", 999))
            infra = math.log1p(fan_in) / log_max_fan
            hop_score = 0.25 if out_hops is None else (1.0 / (1 + out_hops))
            path_bonus = 0.0
            freshness = math.exp(-age_hours / 24.0)
            significance = (
                infra * scoring_cfg.infrastructure_weight +
                hop_score * scoring_cfg.hop_weight +
                path_bonus * scoring_cfg.path_bonus_weight +
                freshness * scoring_cfg.freshness_weight
            )

            top_repeaters.append(
              {
                "node": r.get("node", "").upper().replace("!", "")[:4],
                "name": r.get("name", None),
                "fan_in": fan_in,
                "age_hours": round(age_hours, 1),
                "out_hops": int(out_hops) if out_hops is not None else None,
                "hop_score": round(hop_score, 3),
                "infra": round(infra, 3),
                "path_bonus": round(path_bonus, 3),
                "freshness": round(freshness, 3),
                "significance": round(significance, 3),
                "coverage_pct": (fan_in / total_nodes) * 100.0,
              }
            )
          # Re-sort by significance (SQL ordered by fan_in; significance order differs)
          top_repeaters.sort(key=lambda x: x["significance"], reverse=True)
          top_repeaters = top_repeaters[:10]
          if top_repeaters:
            top_repeaters[0]["_max_fan_in"] = max_fan_in

        # Last 24 hrs of coordination snapshots injected by community layer
        if "packet_stream" in tables:
          # Cutoff for last 24 hours; tz_offset not needed since DB timestamps are local time
          cutoff = now - (24 * 60 * 60)
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
            # Remove score and any stage marker (stage=word)
            summary_without_stage = re.sub(r"\bstage=\w+\b", "", summary).strip()
            event_score, summary_without_score = _extract_score_from_summary(summary_without_stage)
            recent_events.append(
              {
                "timestamp": float(r["timestamp"]), # - tz_offset_sec,
                "stage": stage,
                "score": event_score,
                "summary": summary_without_score,
              }
            )

        # DM statistics (last 24 hrs) - track sent DMs and ACK delivery confirmation
        if "packet_stream" in tables:
            # Cutoff for last 24 hours; tz_offset not needed since DB timestamps are local time
            cutoff = now - (24 * 60 * 60)
            
            # Query 'command' entries for DM transmissions with ACK tracking
            cur.execute(
                """
                SELECT timestamp, data
                FROM packet_stream
                WHERE type = 'command' AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 1000
                """,
                (cutoff,),
            )
            
            user_stats = {}  # Track per-user DM stats
            
            for r in cur.fetchall():
                try:
                    payload = json.loads(r["data"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

                # Look for DM commands with command_id pattern "dm_*"
                command_id = payload.get("command_id", "")
                if not command_id or not command_id.startswith("dm_"):
                    continue
                
                # Extract recipient from command_id or user field
                recipient = payload.get("user", "Unknown")
                
                # This is a DM transmission
                dm_stats["total_dms"] += 1
                
                # Track per-user stats
                if recipient not in user_stats:
                    user_stats[recipient] = {"sent": 0, "delivered": 0}
                user_stats[recipient]["sent"] += 1
                
                # Check if ACK was received ('success' field indicates ACK received)
                success = payload.get("success", False)
                if success:
                    dm_stats["dms_with_response"] += 1
                    user_stats[recipient]["delivered"] += 1
            
            # Calculate success rates and get top/bottom users
            user_rates = []
            for user, stats in user_stats.items():
                if stats["sent"] >= 2:  # Only include users with at least 2 DMs
                    rate = (stats["delivered"] / stats["sent"]) * 100
                    user_rates.append({
                        "user": user,
                        "sent": stats["sent"],
                        "delivered": stats["delivered"],
                        "rate": round(rate, 0)
                    })
            
            # Sort by rate (descending)
            user_rates.sort(key=lambda x: x["rate"], reverse=True)
            
            # Get top 3 and bottom 3
            dm_stats["top_users"] = user_rates[:3] if len(user_rates) >= 3 else user_rates
            dm_stats["bottom_users"] = user_rates[-3:][::-1] if len(user_rates) >= 3 else []

    finally:
        conn.close()

    # Calculate average score for bid events
    scores = [e["score"] for e in recent_events if e["score"] is not None and e["stage"] == "bid"]
    avg_score = sum(scores) / len(scores) if scores else None

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
                "avg_score": avg_score,
                "recent_events": recent_events[:50],
            },
            "dm_stats": dm_stats,
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