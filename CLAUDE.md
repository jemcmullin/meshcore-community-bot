# CLAUDE.md

## Overview

MeshCore Community Bot - Extended MeshCore mesh radio bot with multi-bot coordination. Uses meshcore-bot as a **git submodule** and adds coordinator integration for coordinated response priority across multiple bot instances.

## Architecture

- **Base bot:** meshcore-bot at `meshcore-bot/` — **git submodule, do not modify directly**. All behaviour changes go in `community/` or as tracked patches in `MESHCORE-BOT-PATCHES/`.
- **Extension:** `community/` package adds coordinator client, message interceptor, packet reporter, network observer
- **Entry point:** `community_bot.py` → `community/community_core.py:CommunityBot` (extends `MeshCoreBot`)
- **Scheduler:** Runs as an asyncio task in the main event loop (not a separate thread)
- **DB access:** Sync sqlite3 calls wrapped with `asyncio.to_thread()` to avoid blocking the event loop. Web viewer runs in its own Flask thread and uses sync sqlite3 directly.

## Submodule Policy

`meshcore-bot/` is a git submodule tracking upstream. The strong preference is **never modify files inside `meshcore-bot/`**. Instead:

1. **Patch via community layer** — monkey-patch methods at runtime from `community/` code (e.g. `MessageInterceptor` patches `send_response` and `process_message` using `types.MethodType`).
2. **If a submodule change is unavoidable**, record it in `MESHCORE-BOT-PATCHES/` as a numbered `.patch` file (format: `NNN-short-description.patch`) so it can be re-applied after submodule updates.
3. **DB table whitelist** — `db_manager.py` enforces `ALLOWED_TABLES` on `create_table()`/`drop_table()`. Community tables are **not** in that whitelist. Use `db_manager.execute_query("CREATE TABLE IF NOT EXISTS ...")` for DDL in community code — `execute_query()` is not whitelist-gated.

## Key Integration Point

**`CommandManager.send_response()`** and **`MessageHandler.process_message()`** are both patched by `MessageInterceptor` using `types.MethodType`. This captures ALL bot responses and ALL incoming channel messages without touching the submodule.

`send_response` intercept:

1. Lets DMs through immediately (no coordination needed)
2. Queries local DB for outbound hop count + path significance (see Coordination Flow)
3. Checks with coordinator — 300ms bidding window, proximity-scored, best bot responds
4. Falls back to proximity-weighted delay if coordinator unreachable (500ms timeout)

`process_message` intercept:

1. Feeds every non-DM channel message path into `NetworkObserver` before normal processing
2. This gives the observer far more data than commands alone (learns repeater roles faster)

All 20+ existing commands work unchanged — they call `BaseCommand.send_response()` which delegates to `CommandManager.send_response()`.

## Project Structure

```
community_bot.py                    # Entry point
community/
├── community_core.py              # CommunityBot extends MeshCoreBot
├── coordinator_client.py          # httpx client for coordinator API (lazy AsyncClient init)
├── message_interceptor.py         # Patches send_response + process_message via MethodType
├── network_observer.py            # Learns repeater significance from observed traffic
├── packet_reporter.py             # Background batch reporter
├── coverage_fallback.py           # Proximity-weighted delay when coordinator down
├── config.py                      # CoordinatorConfig + ScoringConfig from env/ini
├── scoring_observer_config.ini    # [Scoring] weights + [NetworkObserver] tuning params
└── commands/
    ├── coverage_command.py        # "coverage" - show bot's score
    └── botstatus_command.py       # "botstatus" - coordinator status
MESHCORE-BOT-PATCHES/              # Tracked patches for submodule (apply after updates)
└── README.md                      # Patch naming convention: NNN-short-description.patch
meshcore-bot/                      # Git submodule — DO NOT MODIFY DIRECTLY
├── modules/
│   ├── core.py                   # MeshCoreBot - main bot class
│   ├── command_manager.py        # Command routing, send_response()
│   ├── message_handler.py        # Incoming message processing
│   ├── scheduler.py              # Asyncio task for scheduled messages, feeds, channel ops
│   ├── db_manager.py             # SQLite DB — ALLOWED_TABLES whitelist on create_table()
│   ├── feed_manager.py           # RSS/API feed polling
│   ├── channel_manager.py        # Channel management
│   ├── repeater_manager.py       # Repeater contact tracking
│   ├── plugin_loader.py          # Auto-discovers command plugins
│   ├── rate_limiter.py           # Rate limiting (uses time.monotonic)
│   ├── commands/                 # Plugin commands (auto-discovered)
│   └── web_viewer/               # Flask+SocketIO web UI (runs in own thread)
docs/
└── BEST_PATH_SCORE_DESIGN_PLAN.md # Design plan for best-path scoring feature (in-progress)
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

1. **Every** channel message → `MessageInterceptor._observing_process_message()` feeds path nodes to `NetworkObserver` (learns repeater roles over time)
2. If message matches a command → `_coordinated_send_response()` runs:
   a. Query DB: `outbound_hops` (from `complete_contact_tracking.out_path_len`) + `path_significance` (from `NetworkObserver`)
   b. Compute `sender_proximity_score = hop_score × hop_weight + path_sig × path_sig_weight`
   c. Send hash + proximity score to coordinator `POST /should-respond` (300ms bidding window)
3. If coordinator says yes → respond normally
4. If coordinator says no → suppress (another bot handles it)
5. If coordinator unreachable (>500ms) → `wait_before_responding_with_signal()` — proximity-weighted delay so nearest bot wins the race

**Proximity score formula:** `hop_score = max(0, 1 - best_hops × 0.25)` where `best_hops = min(inbound_hops, outbound_hops)`. Blended with `path_significance` using configurable weights (`scoring_observer_config.ini` or `SCORING_*` env vars). SNR/RSSI kept in payload for analytics only — not used in scoring.

## Deployment

- Community members: clone, edit `.env`, `docker compose up -d`
- Auto-release: push tag `v*` for Docker image + GitHub release
- Coordinator URL defaults to `https://coordinator.denvermc.com`
