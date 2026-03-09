# CLAUDE.md

## Overview

MeshCore Community Bot - Extended MeshCore mesh radio bot with multi-bot coordination. Uses meshcore-bot as a **git submodule** and adds coordinator integration for coordinated response priority across multiple bot instances.

## Architecture

- **Base bot:** meshcore-bot at `meshcore-bot/` — **git submodule, do not modify directly**. All behaviour changes go in `community/` or as tracked patches in `MESHCORE-BOT-PATCHES/`.
- **Extension:** `community/` package adds coordinator client, message interceptor, packet reporter, and delivery scoring
- **Entry point:** `community_bot.py` → `community/community_core.py:CommunityBot` (extends `MeshCoreBot`)
- **Scheduler:** Runs as an asyncio task in the main event loop (not a separate thread)
- **DB access:** Sync sqlite3 calls wrapped with `asyncio.to_thread()` to avoid blocking the event loop. Web viewer runs in its own Flask thread and uses sync sqlite3 directly.

## Submodule Policy

`meshcore-bot/` is a git submodule tracking upstream. The strong preference is **never modify files inside `meshcore-bot/`**. Instead:

1. **Patch via community layer** — monkey-patch methods at runtime from `community/` code (e.g. `MessageInterceptor` patches `process_message` and `send_response` using `types.MethodType`).
2. **If a submodule change is unavoidable**, record it in `MESHCORE-BOT-PATCHES/` as a numbered `.patch` file (format: `NNN-short-description.patch`) so it can be re-applied after submodule updates.
3. **DB tables** — Community code uses **only existing submodule tables** (`complete_contact_tracking`, `observed_paths`, `mesh_connections`). No community tables are created.

## Key Integration Point

`MessageInterceptor` patches two methods on the bot via `types.MethodType`:

**`MessageHandler.process_message()`** (messages_processed counter):

1. Increments `messages_processed_count` on the bot
2. Runs original handler unchanged

**`CommandManager.send_response()`** (coordination gate + delivery scoring):

1. Lets DMs through immediately (no coordination needed)
2. For channel messages, queries local DB for:

- `infrastructure` (log-scaled fan-in, harmonic mean across path nodes) from `mesh_connections`
- `path_bonus` (exact sender+path match) from `observed_paths`
- `path_freshness` (recency decay) from `observed_paths`
- `inbound_hops` from live `message.hops`

3. Computes `delivery_score` = weighted blend of 4 components (see Coordination Flow)
4. Checks with coordinator — 300ms bidding window, delivery-scored, best bot responds
5. Falls back to delivery-score-aware delay if coordinator unreachable (500ms timeout)

All 20+ existing commands work unchanged — they call `BaseCommand.send_response()` which delegates to `CommandManager.send_response()`.

## Project Structure

```
community_bot.py                    # Entry point
community/
├── community_core.py              # CommunityBot extends MeshCoreBot
├── coordinator_client.py          # httpx client for coordinator API (lazy AsyncClient init)
├── message_interceptor.py         # Patches process_message + send_response via MethodType; queries DB for delivery scoring
├── packet_reporter.py             # Background batch reporter
├── coverage_fallback.py           # Delivery-score-aware delay when coordinator down
├── config.py                      # CoordinatorConfig + ScoringConfig from env/ini
├── scoring_observer_config.ini    # [Scoring] weights (infrastructure, hop, path_bonus, freshness)
└── commands/
    ├── coverage_command.py        # "coverage" - show bot's score
    ├── botstatus_command.py       # "botstatus" - coordinator status
    └── scoring_command.py         # "scoring" - top repeaters by infrastructure score
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
└── PATH_FAMILIARITY_SCORE_DESIGN_PLAN.md # Delivery scoring design that matches community implementation
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

1. If message matches a command → `_coordinated_send_response()` runs:
   a. Query DB for delivery metrics:
   - `infrastructure` from `mesh_connections` fan-in along inbound path
   - `path_bonus` from exact sender+path match in `observed_paths`
   - `path_freshness` from latest sender observation in `observed_paths`
   - `inbound_hops` from live `message.hops`
     b. Compute:
   - `hop_score = 1 / (1 + inbound_hops)`
   - `delivery_score = infrastructure×0.40 + hop_score×0.35 + path_bonus×0.15 + path_freshness×0.10`
   - Defaults: infrastructure/freshness unknown → `0.5`; path_bonus unknown → `0.0`
     c. Send hash + delivery_score to coordinator `POST /api/v1/coordination/should-respond` (300ms bidding window)
2. If coordinator says yes → respond normally
3. If coordinator says no → suppress (another bot handles it)
4. If coordinator unreachable (>500ms) → fallback uses delivery-score-aware behavior:

- suppress response when `delivery_score < fallback_min_delivery_score`
- otherwise `wait_before_responding_with_signal()` so best-path bot wins the race

**Key properties:**

- **Infrastructure-priority weighting:** infrastructure is the dominant term at 40%
- **Exact path bonus:** boolean 0/1 bonus from sender+path lookup
- **Per-message scoring:** recomputed for every response bid
- **Signal-aware fallback:** local delay uses same delivery formula (no blending with coordinator score)
- **Fallback cutoff:** bots below `fallback_min_delivery_score` are silenced when coordinator is unreachable
- Weights configurable via `scoring_observer_config.ini` or `SCORING_*` env vars

## Deployment

- Community members: clone, edit `.env`, `docker compose up -d`
- Auto-release: push tag `v*` for Docker image + GitHub release
- Coordinator URL defaults to `https://coordinator.denvermc.com`
