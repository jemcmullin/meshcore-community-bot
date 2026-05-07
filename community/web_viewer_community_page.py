#!/usr/bin/env python3
"""Community wrapper around MeshCore Bot web viewer.

Adds a /community page and /api/community/metrics endpoint at runtime,
without modifying meshcore-bot submodule files.
"""

from __future__ import annotations

import argparse
import json
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
    .wrap { max-width: 1200px; margin: 24px auto; padding: 0 16px; }
    h1 { margin: 0 0 12px; }
    .meta { color: var(--muted); margin-bottom: 16px; }
    .grid { display:grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
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
        <h3>Recent Coordination Events</h3>
        <table>
          <thead><tr><th>Bid</th><th>Coordinated</th><th>Sender</th><th>Hops</th><th>Command</th><th>Action</th><th>Reason</th><th>Details</th></tr></thead>
          <tbody id=\"events\"></tbody>
        </table>
      </section>
      <section class=\"card\" style=\"grid-column: 1/-1;\">
        <h3>Top Repeaters (from this bot)</h3>
        <table>
          <thead><tr>
            <th>Top</th>
            <th>Name</th>
            <th title="Active &lt;24h · Recent 24-48h · Stale &gt;48h">Status</th>
            <th title="Hops to bot from last advert. Est. for messages traversing this relay">Advert<br>Hops</th>
            <th title="Unique source nodes routing through this relay">Links</th>
            <th title="Time since relay last seen in mesh traffic">Last</th>
          </tr></thead>
          <tbody id="repeaters"></tbody>
        </table>
        <p id="repeaters-caption" style="font-size:12px;color:var(--muted);margin:6px 0 0;"></p>
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
      
    `;

    const coord = data.coordination;
    const sc = coord.stage_counts;
    const total = sc.bid || 0;
    const responded = sc.assigned_us || 0;
    const deferred = sc.assigned_other || 0;
    const fallback = sc.fallback_sent || 0;
    const respondedRandom = sc.assigned_us_random || 0;
    const respondedBest = responded - respondedRandom;
    const responseRate = total > 0 ? ((responded / total) * 100).toFixed(0) : 0;
    const fallbackRate = total > 0 ? ((fallback / total) * 100).toFixed(0) : 0;
    if (total === 0) {
      document.getElementById('coord').innerHTML = '<div style=\"color:var(--muted)\">No coordination events in last hour</div>';
    } else {
      document.getElementById('coord').innerHTML = `
        <div><b>Coordinated:</b> ${total} (responded ${responded}, deferred ${deferred})</div>
        <div><b>Response rate:</b> ${responseRate}% <span style="color:var(--muted);font-size:12px">(best: <span style="color:#2d8a4e">${respondedBest}</span> · random: <span style="color:#5a9a6e">${respondedRandom}</span>)</span></div>
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
      
      // Show top users (rate >= 80%)
      const topUsers = (dm.top_users || []).filter(u => u.rate >= 80);
      if (topUsers.length > 0) {
        dmHtml += '<div style=\"margin-top:8px;font-size:12px;color:var(--muted)\"><b>Top delivery:</b></div>';
        topUsers.forEach(u => {
          dmHtml += `<div style=\"font-size:11px\"><span style=\"color:#2d8a4e;font-weight:bold\">${u.rate}%</span> ${u.user} (${u.delivered}/${u.sent})</div>`;
        });
      }
      
      // Show bottom users (rate < 80%)
      const bottomUsers = (dm.bottom_users || []).filter(u => u.rate < 80);
      if (bottomUsers.length > 0) {
        dmHtml += '<div style=\"margin-top:6px;font-size:12px;color:var(--muted)\"><b>Needs attention:</b></div>';
        bottomUsers.forEach(u => {
          const statusColor = u.rate >= 50 ? '#b07d1a' : '#c44';
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
      const name = r.name ? r.name : '';
      const tip = `hop_score=${r.hop_score.toFixed(2)}`;

      return `
      <tr title="${tip}">
        <td class="mono">${r.node}</td>
        <td>${name}</td>
        <td style="color:${statusColor};font-weight:bold">${statusLabel}</td>
        <td>${pathLabel}</td>
        <td>${r.fan_in}</td>
        <td>${lastSeen}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="6">No repeater data</td></tr>';
    document.getElementById('repeaters-caption').textContent =
      'Status: Active <24h · Recent 24-48h · Stale >48h';

    const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleTimeString('en-US', { hour12: true }) : '—';
    document.getElementById('events').innerHTML = data.coordination.recent_events.map(e => {
      const stageColor = e.stage === 'assigned_us' ? (e.is_random ? '#5a9a6e' : '#2d8a4e')
        : e.stage === 'assigned_other' ? '#888'
        : e.stage === 'fallback_sent' ? '#b07d1a' : '#4c5b4c';
      const stageText = e.stage === 'assigned_us' ? (e.is_random ? 'responded (random)' : 'responded (best)')
        : e.stage === 'assigned_other' ? 'deferred'
        : e.stage === 'fallback_sent' ? 'fallback' : e.stage;
      const stageLabel = `<span style="color:${stageColor};font-weight:bold">${stageText}</span>`;
      const bidTime = fmtTime(e.bid_timestamp || (e.stage === 'bid' ? e.timestamp : null));
      const coordTime = e.stage !== 'bid' ? fmtTime(e.timestamp) : '—';
      let detail = '';
      if (e.winner) detail += `<span>handler: <b>${e.winner}</b></span> `;
      if (e.score)  detail += `<span>score: ${e.score}</span> `;
      if (e.delay)  detail += `<span style="color:var(--muted)">+${e.delay}</span>`;
      return `
      <tr>
        <td>${bidTime}</td>
        <td>${coordTime}</td>
        <td class="mono">${e.sender || '—'}</td>
        <td>${e.hops != null ? e.hops : '—'}</td>
        <td class="mono">${e.command || '—'}</td>
        <td>${stageLabel}</td>
        <td style="color:var(--muted);font-size:13px">${e.reason || '—'}</td>
        <td style="font-size:13px">${detail || '—'}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="8">No recent coordination events</td></tr>';
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
    now = time.time()
    top_repeaters = []
    stage_counts = {"bid": 0, "assigned_us": 0, "assigned_other": 0, "fallback_sent": 0}
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
      cleaned_summary = re.sub(r"\bstage=\w+\b", "", summary).strip()
      return cleaned_summary

    def _parse_summary_parts(summary):
      parts = {}
      for m in re.finditer(r'(\w+)=((?:(?!\s\w+=).)+)', summary):
        parts[m.group(1)] = m.group(2).strip()
      return parts

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
          # May inflate score when mixed prefix length connections exist and public key is missing. Actual scoring implements deduplication.
          cur.execute(
            """
            SELECT COALESCE(mc.to_public_key, mc.to_prefix) AS node,
                 COUNT(DISTINCT mc.from_public_key) AS fan_in,
                 CAST((julianday('now', 'localtime') - julianday(MAX(mc.last_seen))) * 24 AS REAL) AS age_hours,
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
            LIMIT 100
            """
          )
          rows = cur.fetchall()

          for r in rows:
            fan_in = int(r["fan_in"] if "fan_in" in r.keys() else 0)
            out_hops = r["out_hops"] if "out_hops" in r.keys() else None
            age_hours = float(r["age_hours"] if "age_hours" in r.keys() else 999)
            hop_score = 0.25 if out_hops is None else (1.0 / (1 + out_hops))
            if age_hours > 60:  # 2.5 days, ignore
               continue
            top_repeaters.append(
              {
                "node": (r["node"] if "node" in r.keys() else "").upper().replace("!", "")[:4],
                "name": r["name"] if "name" in r.keys() else None,
                "fan_in": fan_in,
                "age_hours": round(age_hours, 1),
                "out_hops": int(out_hops) if out_hops is not None else None,
                "hop_score": round(hop_score, 3),
              }
            )
          # Sort by fan_in desc, hop_score desc as tiebreaker
          top_repeaters.sort(key=lambda x: (x["fan_in"], x["hop_score"]), reverse=True)
          top_repeaters = top_repeaters[:15]  # Keep top 15 for display

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
            if stage == "assigned_us":
              summary_raw = payload.get("response") or ""
              if "random" in summary_raw:
                stage_counts["assigned_us_random"] = stage_counts.get("assigned_us_random", 0) + 1

            summary = payload.get("response") or ""
            # Strip stage= tag from summary display
            summary_clean = _extract_score_from_summary(summary)
            parts = _parse_summary_parts(summary_clean)
            is_random = stage == "assigned_us" and "random" in (parts.get("reason") or "")
            recent_events.append(
              {
                "timestamp": float(r["timestamp"]),
                "stage": stage,
                "is_random": is_random,
                "sender": parts.get("sender"),
                "hops": parts.get("hops"),
                "command": parts.get("command"),
                "winner": parts.get("winner"),
                "score": parts.get("score"),
                "reason": parts.get("reason"),
                "delay": parts.get("delay"),
                "summary": summary_clean,
              }
            )

        # Pair bid+result events into single rows (descending: result appears before its bid)
        combined_events = []
        paired_bids = set()
        for i, evt in enumerate(recent_events):
            if i in paired_bids:
                continue
            if evt["stage"] == "bid":
                combined_events.append(evt)
            else:
                for j in range(i + 1, len(recent_events)):
                    if j in paired_bids:
                        continue
                    b = recent_events[j]
                    if b["stage"] != "bid":
                        continue
                    if (b.get("sender") == evt.get("sender")
                            and abs(evt["timestamp"] - b["timestamp"]) < 5):
                        combined_events.append({**b, **evt, "bid_timestamp": b["timestamp"]})  # result fields win; preserve bid time
                        paired_bids.add(j)
                        break
                else:
                    combined_events.append(evt)

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

    return jsonify(
        {
            "timestamp": now,
            "db_path": Path(viewer.db_path).name,
            "network": {
                "total_nodes": total_nodes,
            },
            "top_repeaters": top_repeaters,
            "coordination": {
                "event_count": event_count,
                "stage_counts": stage_counts,
                "recent_events": combined_events[:25],
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