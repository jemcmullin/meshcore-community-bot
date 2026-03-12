# MeshCore Community Bot

A multi-bot-aware MeshCore mesh radio bot with coordinated response priority. Built on top of [meshcore-bot](https://github.com/agessaman/meshcore-bot), adding central coordinator integration so multiple bots on the same mesh don't all respond to the same message.

## How It Works

The community bot wraps the existing meshcore-bot and all its commands (weather, satellite passes, solar, etc.). When a message comes in on a channel, the bot checks with a central coordinator to see if it should respond. The bot with the highest **delivery score** gets priority. If the coordinator is unreachable, bots fall back to a delivery-score-based delay system.

```
Your Radio ──► Community Bot ──► Coordinator API
                    │                   │
                    ▼                   ▼
              All existing        Who should
              commands work       respond?
```

**DMs always work immediately** - coordination only applies to channel messages where multiple bots might see the same request.

## Features

Everything from [meshcore-bot](https://github.com/agessaman/meshcore-bot) as an unmodified upgradeable submodule, plus:

- **Multi-Bot Coordination** - Only one bot responds per message, based on per-message delivery score
- **Path-Based Scoring** - Delivery score reflects infrastructure, hops, path familiarity, and freshness
- **Automatic Fallback** - Works standalone if coordinator is unreachable
- **Network Reporting** - Messages/packets are reported for network-wide analytics
- **New Commands** - `coverage` (show your score), `botstatus` (coordinator status), and `scoring` (top repeaters for use to this bot)
- **Web Viewer Community Dashboard** - Real-time visualization of coordination decisions, delivery scores, and repeater significance to this bot

## Delivery Scoring Overview

The bot computes a delivery score for each channel message using:

- Infrastructure fan-in/links/connectedness (mesh_connections)
- Hop count (message.hops)
- Exact path familiarity (observed_paths)
- Path freshness (recency decay)
  Weights and fallback behavior are configurable in [Scoring] section and env vars.
  If coordinator is unreachable, bots use delivery-score-based delay and suppress responses below minimum score.
  See docs/COMMUNITY_DESIGN.md for full details.

## Requirements

- Docker & Docker Compose
- `make` — on Debian/Ubuntu: `sudo apt-get install -y make`
- MeshCore-compatible radio (Heltec V3, RAK Wireless, etc.)
- USB cable, BLE, or TCP connection to the radio

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/cj-vana/meshcore-community-bot.git
cd meshcore-community-bot
```

### 2. Configure your environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Your radio connection
MESHCORE_CONNECTION_TYPE=serial
MESHCORE_SERIAL_PORT=/dev/ttyUSB0

# Your bot identity
MESHCORE_BOT_NAME=MyBot
MESHCORE_LATITUDE=39.7392
MESHCORE_LONGITUDE=-104.9903
MESH_REGION=DEN

# Coordinator (provided by network admin)
COORDINATOR_URL=https://coordinator.denvermc.com
COORDINATOR_REGISTRATION_KEY=your-key-here
```

### 3. Configure the bot

```bash
cp config.ini.example config.ini
# Edit config.ini for additional settings (keywords, API keys, channels, etc.)
```

### 4. Start the bot

```bash
make deploy
```

This builds and starts the bot, immediately tails the logs. Ctrl+C stops the log tail but leaves the bot running.

> Or use `make up` to start in the background without attaching to logs. `make up` and `make start` both initialise the git submodule automatically.

### 5. Check the logs

```bash
make logs
```

You should see:

```
[INFO] Starting MeshCore Community Bot...
[INFO] Registered with coordinator as MyBot (uuid-here)
[INFO] Coordinator background tasks started
[INFO] Bot is running. Press Ctrl+C to stop.
```

## Configuration

### Environment Variables

Set these in your `.env` file:

| Variable                        | Required        | Description                          |
| ------------------------------- | --------------- | ------------------------------------ |
| `MESHCORE_CONNECTION_TYPE`      | Yes             | `serial`, `ble`, or `tcp`            |
| `MESHCORE_SERIAL_PORT`          | For serial      | Device path (e.g., `/dev/ttyUSB0`)   |
| `MESHCORE_TCP_HOST`             | For TCP         | Radio IP address                     |
| `MESHCORE_BOT_NAME`             | Yes             | Your bot's display name              |
| `MESHCORE_LATITUDE`             | Recommended     | Your location (for scoring)          |
| `MESHCORE_LONGITUDE`            | Recommended     | Your location (for scoring)          |
| `COORDINATOR_URL`               | Recommended     | Coordinator API URL                  |
| `COORDINATOR_REGISTRATION_KEY`  | For coordinator | Registration key from network admin  |
| `MESH_REGION`                   | Optional        | Region code (e.g., `DEN`)            |
| `WEB_VIEWER_PORT`               | Optional        | Web viewer port (default: `8081`)    |
| `DISCORD_BOT_WEBHOOK_URL`       | Optional        | Discord webhook for #bot messages    |
| `DISCORD_EMERGENCY_WEBHOOK_URL` | Optional        | Discord webhook for #emergency       |
| `TZ`                            | Optional        | Timezone (default: `America/Denver`) |
| `N2YO_API_KEY`                  | Optional        | For satellite pass command           |
| `AIRNOW_API_KEY`                | Optional        | For air quality command              |

### Config File

`config.ini` controls bot behavior (keywords, channels, rate limiting, etc.). See [config.ini.example](config.ini.example) for all options.

Key settings:

- `[Channels] monitor_channels` - Which channels to monitor (default: `#bot`)
- `[Channels] respond_to_dms` - Whether to respond to DMs (default: `true`)
- `[Coordinator]` section - Coordinator-specific settings (usually set via env vars)

## Standalone Mode

If `COORDINATOR_URL` is empty or the coordinator is unreachable, the bot runs standalone - just like a regular meshcore-bot. All commands work normally, there's just no multi-bot coordination.

## Commands

All commands from meshcore-bot are available, plus:

| Command     | Description                                                       |
| ----------- | ----------------------------------------------------------------- |
| `coverage`  | Shows your bot's current coverage score (current coordinator api) |
| `botstatus` | Shows coordinator connection status and network info              |
| `scoring`   | Shows top repeaters contributing to your delivery score           |

## Updating

Pull the latest changes and rebuild:

```bash
make redeploy
```

or

```bash
make pull
make build
make up
```

### Submodule Version Management

The `meshcore-bot` submodule is pinned to a tested version and updated deliberately by the maintainers. This ensures compatibility and reliable community coordination. All official upgrades are packaged and released by maintainers.

> **Warning:** Manually changing the submodule version may break community coordination and is not recommended. Only do this if you understand the risks and accept that your bot may not interoperate correctly with others.

To upgrade the pinned `meshcore-bot` submodule version (latest):

```bash
git submodule update --remote --merge
git add meshcore-bot
git commit -m 'chore: update meshcore-bot submodule'
make build
make up
```

If you need to temporarily pin to a specific commit (bug resolution):

```bash
cd meshcore-bot
git fetch
git checkout <commit-sha>   # or a branch/tag, e.g. git checkout main
cd ..
make build
make up
```

> **Note:** This is a local override only — it does not affect other users and will be overwritten the next time you run `make redeploy` from a fresh clone. If you find a fix is needed upstream, please open an issue so the maintainers can update the pinned version for everyone.

## Pre-Built Docker Images

Docker images are automatically built on new releases and published to GitHub Container Registry.

### Using Docker Compose (recommended)

Create a `docker-compose.yml`:

```yaml
services:
  community-bot:
    image: ghcr.io/cj-vana/meshcore-community-bot:latest
    devices:
      - "/dev/ttyUSB0:/dev/ttyUSB0"
    volumes:
      - ./config.ini:/app/config.ini:rw
      - ./data:/app/data
      - ./logs:/app/logs
    env_file: .env
    ports:
      - "${WEB_VIEWER_PORT:-8081}:${WEB_VIEWER_PORT:-8081}"
    restart: unless-stopped
```

Then:

```bash
cp .env.example .env        # Edit with your settings
cp config.ini.example config.ini  # Edit with your preferences
make up
```

### Using Docker Run

```bash
docker run -d \
  --name community-bot \
  --device /dev/ttyUSB0:/dev/ttyUSB0 \
  -v $(pwd)/config.ini:/app/config.ini:rw \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -p 8081:8081 \
  -e MESHCORE_CONNECTION_TYPE=serial \
  -e MESHCORE_SERIAL_PORT=/dev/ttyUSB0 \
  -e MESHCORE_BOT_NAME=MyBot \
  -e MESHCORE_LATITUDE=39.7392 \
  -e MESHCORE_LONGITUDE=-104.9903 \
  -e MESH_REGION=DEN \
  -e COORDINATOR_URL=https://coordinator.denvermc.com \
  -e COORDINATOR_REGISTRATION_KEY=your-key-here \
  -e WEB_VIEWER_PORT=8081 \
  -e TZ=America/Denver \
  --restart unless-stopped \
  ghcr.io/cj-vana/meshcore-community-bot:latest
```

### Available Tags

| Tag      | Description         |
| -------- | ------------------- |
| `latest` | Most recent release |
| `0.1.0`  | Specific version    |

Images are published at: `ghcr.io/cj-vana/meshcore-community-bot`

## Development

```bash
git clone https://github.com/cj-vana/meshcore-community-bot.git
cd meshcore-community-bot
pip install -r requirements.txt
python3 community_bot.py
```

## Troubleshooting

**Bot can't connect to radio:**

- Check `MESHCORE_SERIAL_PORT` matches your device (`ls /dev/ttyUSB*`)
- Use `ls /dev/by-id/` as an alternative stable device path
- Make sure Docker has device access (check `docker-compose.yml` devices section)

**Coordinator registration failed:**

- Ensure `COORDINATOR_REGISTRATION_KEY` is set (obtain from network admin)
- Check `COORDINATOR_URL` is correct
- Bot still works in standalone mode - it will retry on next heartbeat

**Commands not responding:**

- Check `docker compose logs -f` for errors
- Verify the channel is in `monitor_channels` in config.ini
- Check rate limiting settings

## License

Private - contact for access.
