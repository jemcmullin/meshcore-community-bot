# CLAUDE.md

## Overview

MeshCore Community Bot - Extended MeshCore mesh radio bot with multi-bot coordination. Embeds a copy of meshcore-bot and adds coordinator integration for coordinated response priority across multiple bot instances.

## Architecture

- **Base bot:** meshcore-bot (embedded copy at `meshcore-bot/`) - can be modified directly
- **Extension:** `community/` package adds coordinator client, message interceptor, packet reporter
- **Entry point:** `community_bot.py` → `community/community_core.py:CommunityBot` (extends `MeshCoreBot`)
- **Scheduler:** Runs as an asyncio task in the main event loop (not a separate thread)
- **DB access:** Sync sqlite3 calls wrapped with `asyncio.to_thread()` to avoid blocking the event loop. Web viewer runs in its own Flask thread and uses sync sqlite3 directly.

## Key Integration Point

**`CommandManager.send_response()`** at `meshcore-bot/modules/command_manager.py:552` is patched by `MessageInterceptor`. This single method captures ALL bot responses. The interceptor:
1. Lets DMs through immediately (no coordination needed)
2. Checks with coordinator for channel messages, passing signal data (SNR, RSSI, hops, path)
3. Coordinator uses 300ms bidding window + hybrid path quality scoring to pick best bot
4. Falls back to score-based delay if coordinator is unreachable (500ms timeout)

All 20+ existing commands work unchanged - they call `BaseCommand.send_response()` which delegates to `CommandManager.send_response()`.

## Project Structure

```
community_bot.py                    # Entry point
community/
├── community_core.py              # CommunityBot extends MeshCoreBot
├── coordinator_client.py          # httpx client for coordinator API (passes signal data)
├── message_interceptor.py         # Patches send_response for coordination
├── packet_reporter.py             # Background batch reporter
├── coverage_fallback.py           # Score-based delay when coordinator down
├── config.py                      # Coordinator config from env/ini (500ms timeout)
└── commands/
    ├── coverage_command.py        # "coverage" - show bot's score
    └── botstatus_command.py       # "botstatus" - coordinator status
meshcore-bot/                      # Embedded copy (modifiable)
├── modules/
│   ├── core.py                   # MeshCoreBot - main bot class
│   ├── command_manager.py        # Command routing, send_response()
│   ├── message_handler.py        # Incoming message processing
│   ├── scheduler.py              # Asyncio task for scheduled messages, feeds, channel ops
│   ├── db_manager.py             # SQLite DB with sync + async (a*) method variants
│   ├── feed_manager.py           # RSS/API feed polling
│   ├── channel_manager.py        # Channel management
│   ├── repeater_manager.py       # Repeater contact tracking
│   ├── plugin_loader.py          # Auto-discovers command plugins
│   ├── rate_limiter.py           # Rate limiting (uses time.monotonic)
│   ├── commands/                 # Plugin commands (auto-discovered)
│   └── web_viewer/               # Flask+SocketIO web UI (runs in own thread)
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

1. Bot receives channel message matching a command
2. `MessageInterceptor` computes message hash (sha256 of sender + content + time bucket)
3. Asks coordinator `POST /should-respond` with signal data (SNR, RSSI, hops, path)
4. Coordinator collects bids for 300ms, scores each by repeater TX quality + hop count + overall score
5. If coordinator says yes → respond normally
6. If coordinator says no → suppress response (another bot handles it)
7. If coordinator unreachable (>500ms) → wait score-based delay, then respond

## Deployment

- Community members: clone, edit `.env`, `docker compose up -d`
- Auto-release: push tag `v*` for Docker image + GitHub release
- Coordinator URL defaults to `https://coordinator.denvermc.com`
