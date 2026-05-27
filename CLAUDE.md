# CLAUDE.md

## Overview

MeshCore Community Bot extends the MeshCore mesh radio bot with firmware-native multi-bot coordination. It uses meshcore-bot as a **git submodule** and adds a community layer for deterministic response timing, token-based suppression, and real-time monitoring.

## Architecture

- **Base bot:** meshcore-bot at `meshcore-bot/` — **git submodule, do not modify directly**. All behaviour changes go in `community/`.
- **Extension:** `community/` package adds firmware fingerprinting, response coordination, message interception, new commands, and a web viewer dashboard.
- **Entry point:** `community_bot.py` → `community/community_core.py:CommunityBot` (extends `MeshCoreBot`)
- **Scheduler:** Runs as an asyncio task in the main event loop (not a separate thread)
- **DB access:** Sync sqlite3 calls wrapped with `asyncio.to_thread()` to avoid blocking the event loop. Web viewer runs in its own Flask thread and uses sync sqlite3 directly.

## Submodule Policy

`meshcore-bot/` is a git submodule tracking upstream. The strong preference is **never modify files inside `meshcore-bot/`**. Instead:

1. **Patch via community layer** — monkey-patch methods at runtime from `community/` code (e.g. `MessageInterceptor` patches `send_response` using `types.MethodType`).
2. **DB tables** — Community code reads **only existing submodule tables** (`complete_contact_tracking`, `mesh_connections`). The `botreps_command` reads `mesh_connections` for fan-in data. No community tables are created.

## Key Integration Point

`MessageInterceptor` is the core integration point. It patches four meshcore-bot methods so channel coordination matches the firmware-native design:

- `MessageHandler.handle_channel_message()` captures raw packet text before the sender prefix is stripped.
- `MessageHandler.process_message()` sets the current inbound message context and observes peer `[xxxx]` responses for suppression.
- `CommandManager.send_response()` coordinates command-triggered channel replies.
- `CommandManager.send_channel_message()` coordinates keyword-triggered channel replies and other channel sends that bypass `send_response()`.

The interceptor:

1. Lets DMs through immediately with no coordination, no delay, and no token prefix.
2. Converts inbound channel messages into firmware-compatible fingerprint input using the raw packet text.
3. Computes request fingerprints, request tokens, and firmware delay locally.
4. Suppresses pending replies if another bot is heard responding first with the same token.
5. Prefixes successful channel replies with `[xxxx] ` where `xxxx` is the low 16 bits of the request fingerprint.
6. Keeps a per-token first-chunk outcome cache so multi-chunk responses are consistent.

All meshcore-bot commands work unchanged because they still route through `BaseCommand.send_response()` and `CommandManager.send_response()`. Channel keyword triggers are coordinated through the patched `send_channel_message()` path, and the `coordinated_var` guard prevents double-coordination when both send paths fire for the same command.

## Project Structure

```
community_bot.py                    # Entry point
├── community_core.py              # CommunityBot extends MeshCoreBot
├── firmware_coordinator.py        # Bot-scoped pending/recent state and delay scheduling
├── meshcore_request_token.py      # Firmware-compatible request/response fingerprint helpers
├── meshcore_response_coordinator.py # Firmware delay constants and suppression helpers
├── message_interceptor.py         # Patches meshcore-bot message/response flow for coordination
├── config.py                      # CommunityConfig from env/ini
├── discord_webhook.py             # Discord webhook integration for bot alerts
├── web_viewer_community_page.py   # Community dashboard web UI (Flask+SocketIO)
├── web_viewer_packet_stream.py    # Publishes events for web viewer
├── web_viewer_patch.py            # Integrates with meshcore-bot web viewer
└── commands/
   ├── botstatus_command.py       # "botstatus" - firmware coordination status
  └── botreps_command.py         # "botreps" - top infra relays nearby the bot by fan-in/hops
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
cd meshcore-community-bot
git submodule update --init --recursive  # Pull meshcore-bot submodule

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
   - `MessageInterceptor` captures raw text before colon-splitting.
   - The bot computes a firmware-compatible request fingerprint.
   - The low 16 bits become the request token shown as `[xxxx]`.
   - The bot computes the firmware delay locally and adds a pending response entry.
2. While waiting, the bot watches all incoming channel messages.
3. If a peer bot responds first with the same token prefix, the pending reply is suppressed.
4. If not suppressed, the bot prepends `[xxxx] ` to the response and sends it.

**Key properties:**

- **Fully decentralized:** No HTTP coordinator, registration key, or heartbeat task.
- **Firmware-compatible:** Request fingerprints, response fingerprints, token prefixes, and timing constants match the firmware helpers.
- **Raw-text aware:** The fingerprint `text` field uses the original packet text, not the colon-stripped message content.
- **Multi-chunk support:** A per-token outcome cache keeps later chunks consistent with the first coordinated chunk.

## Deployment

- Community members: clone, edit `.env`, edit `config.ini`, `make deploy`
- Auto-release: push tag `v*` for Docker image + GitHub release
