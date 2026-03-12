# CLAUDE.md

## Overview

MeshCore Community Bot extends the MeshCore mesh radio bot with multi-bot coordination. It uses meshcore-bot as a **git submodule** and adds a community layer for coordinated response priority, delivery scoring, and real-time monitoring.

## Architecture

- **Base bot:** meshcore-bot at `meshcore-bot/` — **git submodule, do not modify directly**. All behaviour changes go in `community/`.
- **Extension:** `community/` package adds coordinator client, message interceptor, packet reporter, and delivery scoring, plus new commands and web viewer dashboard.
- **Entry point:** `community_bot.py` → `community/community_core.py:CommunityBot` (extends `MeshCoreBot`)
- **Scheduler:** Runs as an asyncio task in the main event loop (not a separate thread)
- **DB access:** Sync sqlite3 calls wrapped with `asyncio.to_thread()` to avoid blocking the event loop. Web viewer runs in its own Flask thread and uses sync sqlite3 directly.

## Submodule Policy

`meshcore-bot/` is a git submodule tracking upstream. The strong preference is **never modify files inside `meshcore-bot/`**. Instead:

1. **Patch via community layer** — monkey-patch methods at runtime from `community/` code (e.g. `MessageInterceptor` patches `send_response` using `types.MethodType`).
2. **DB tables** — Community code uses **only existing submodule tables** (`complete_contact_tracking`, `observed_paths`, `mesh_connections`). No community tables are created.

## Key Integration Point

`MessageInterceptor` patches two methods on meshcore-bot:

**`CommandManager.send_response()`** at `meshcore-bot/modules/command_manager.py:552` is patched by `MessageInterceptor`. This method captures all COMMAND bot responses.

**`ChannelManager.send_channel_message()`** at `meshcore-bot/modules/channel_manager.py:85` is patched by `MessageInterceptor`. This captures all channel responses, including those triggered by KEYWORDS that bypass `send_response()`.

The interceptor:

1. Lets DMs through immediately (no coordination needed)
   For channel messages, queries local DB for delivery scoring components:

- **infrastructure:**
  - For direct (hops == 0): SNR and RSSI normalized and blended from `message.snr` and `message.rssi`
  - For relayed: harmonic mean of normalized fan-in/connectedness per path node from `mesh_connections`
- **path_bonus:**
  - For relayed: 1.0 if exact sender+path match in `observed_paths`, else 0.0
  - For direct or unknown: always 0.0
- **path_freshness:**
  - Recency decay from `observed_paths` (exp(-age_hours / 24.0)), or 0.5 if unknown
- **hop_score:**
  - Computed as 1 / (1 + inbound_hops) from `message.hops`, or 0.5 if unknown

3. Computes `delivery_score` as a weighted sum of these four components
4. Checks with coordinator — 300ms bidding window, delivery-scored, best bot responds
5. Falls back to delivery-score-aware delay if coordinator unreachable (500ms timeout)

All meshcore-bot commands (20+) work unchanged — they call `BaseCommand.send_response()` which delegates to `CommandManager.send_response()`, and are transparently coordinated via the community layer. Channel keyword triggers are also coordinated via the patched `ChannelManager.send_channel_message()` method. These would otherwise bypass `send_response()` and not be coordinated, but the interceptor ensures all channel messages are subject to the same coordination logic. A flag prevents double-coordination for channel commands that trigger both methods. Future optimization could unify this further.

## Project Structure

```
community_bot.py                    # Entry point
├── community_core.py              # CommunityBot extends MeshCoreBot
├── coordinator_client.py          # Coordinator API client (httpx, async client)
├── coordinator_scoring.py         # Delivery scoring logic
├── message_interceptor.py         # Patches send_response and send_channel_message; computes delivery score
├── packet_reporter.py             # Background batch reporter
├── coverage_fallback.py           # Delivery-score-aware delay when coordinator down
├── config.py                      # CoordinatorConfig + ScoringConfig from env/ini
├── scoring_observer_config.ini    # [Scoring] weights (infrastructure, hop, path_bonus, freshness)
├── web_viewer_community_page.py   # Community dashboard web UI (Flask+SocketIO)
├── web_viewer_packet_stream.py    # Publishes events for web viewer
├── web_viewer_patch.py            # Integrates with meshcore-bot web viewer
└── commands/
  ├── coverage_command.py        # "coverage" - show bot's delivery score
  ├── botstatus_command.py       # "botstatus" - coordinator status
  └── scoring_command.py         # "scoring" - top repeaters to this bot by simulated bid score
meshcore-bot/                      # Git submodule — DO NOT MODIFY DIRECTLY
├── modules/
│   ├── core.py                   # MeshCoreBot - main bot class
│   ├── command_manager.py        # Command routing, send_response()
│   ├── message_handler.py        # Incoming message processing
│   ├── scheduler.py              # Asyncio task for scheduled messages, feeds, channel ops
│   ├── db_manager.py             # SQLite DB — ALLOWED_TABLES whitelist on create_table()
│   ├── feed_manager.py           # RSS/API feed polling
│   ├── channel_manager.py        # Channel management, send_channel_message()
│   ├── repeater_manager.py       # Repeater contact tracking
│   ├── plugin_loader.py          # Auto-discovers command plugins
│   ├── rate_limiter.py           # Rate limiting (uses time.monotonic)
│   ├── commands/                 # Plugin commands (auto-discovered)
│   └── web_viewer/               # Flask+SocketIO web UI (runs in own thread)
docs/
└── COMMUNITY_DESIGN.md # Delivery scoring design and coordination flow details
```

## DB Access Patterns

- **db_manager.py** has sync methods (`execute_query`, `get_cached_value`, etc.) and async wrappers (`aexecute_query`, `aget_cached_value`, etc.) using `asyncio.to_thread()`
- **In async methods:** Use `await self.db_manager.aexecute_query(...)` or `await asyncio.to_thread(sync_func)`
- **In sync methods / web viewer:** Use sync methods directly
- **Commands that bypass db_manager** (stats, greeter, feed) extract DB blocks into sync helpers called via `asyncio.to_thread()`

## Adding New Community Commands

Same pattern as meshcore-bot - create a file in `community/commands/`:

```python
from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

class MyCommand(BaseCommand):
    name = "mycommand"
    keywords = ["mycommand"]
    description = "Does something"

    async def execute(self, message: MeshMessage) -> bool:
        await self.send_response(message, "Hello!")
        return True
```

## Configuration

Config via environment variables (`.env`) mapped to `config.ini` by `docker/entrypoint.sh`:

- `COORDINATOR_URL` - Central coordinator API URL
- `COORDINATOR_REGISTRATION_KEY` - Registration key (required, from network admin)
- `COORDINATOR_TIMEOUT_MS` - Coordination timeout (default 500ms for bidding window)
- `MESH_REGION` - Region code (e.g., DEN)
- `WEB_VIEWER_PORT` - Web viewer port (default 8081)
- `DISCORD_BOT_WEBHOOK_URL` - Discord webhook for #bot channel
- `DISCORD_EMERGENCY_WEBHOOK_URL` - Discord webhook for #emergency
- `MESHCORE_*` - All standard meshcore-bot settings
- See `.env.example` for full list

## Development

```bash
# Clone
git clone <repo-url>

# Local dev
pip install -r requirements.txt
python3 community_bot.py

# Docker
cp .env.example .env  # Edit with your values
docker compose up -d
docker compose logs -f
```

## Coordination Flow

1. Bot receives a channel message (command or keyword trigger):
   - MessageInterceptor runs coordination logic for both `send_response` and `send_channel_message` (patched methods).
   - Queries DB for delivery scoring components:
     - infrastructure (from mesh_connections)
     - path_bonus (from observed_paths)
     - path_freshness (from observed_paths)
     - hop_score (from message.hops)
   - Computes:
     - hop_score = 1 / (1 + inbound_hops) (or 0.5 if unknown)
     - delivery_score = infrastructure×0.40 + hop_score×0.35 + path_bonus×0.15 + path_freshness×0.10
     - Defaults: infrastructure/freshness unknown → 0.5; path_bonus unknown → 0.0
   - Sends hash + delivery_score to coordinator (`POST /api/v1/coordination/should-respond`, 300ms bidding window)
2. If coordinator assigns this bot → respond normally
3. If coordinator assigns another bot → suppress local response
4. If coordinator unreachable (>500ms):
   - Fallback uses delivery-score-aware delay
   - Suppress response when delivery_score < fallback_min_delivery_score
   - Otherwise, wait_before_responding_with_signal() so best delivery potential bot wins the race

**Key properties:**

- **Infrastructure-priority weighting:** Use of good infrastructure is the dominant term at 40%
- **Hop count:** rewards shorter paths (35% weight)
- **Exact path bonus:** boolean 0/1 bonus from sender+path lookup (15% weight)
- **Freshness decay:** recency decay on sender+path observations (10% weight)
- **Per-message scoring:** recomputed for every response bid
- **Signal-aware fallback:** local delay uses same delivery formula (no blending with coordinator score)
- **Fallback cutoff:** bots below `fallback_min_delivery_score` are silenced when coordinator is unreachable
- Weights configurable via `scoring_observer_config.ini` or `SCORING_*` env vars

## Deployment

- Community members: clone, edit `.env`, edit `config.ini`, `make deploy`
- Auto-release: push tag `v*` for Docker image + GitHub release
- Coordinator URL defaults to `https://coordinator.denvermc.com`
