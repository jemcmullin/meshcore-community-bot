# MeshCore Community Bot

A multi-bot-aware MeshCore mesh radio bot with coordinated response priority. Built on top of [meshcore-bot](https://github.com/cj-vana/meshcore-bot), adding central coordinator integration so multiple bots on the same mesh don't all respond to the same message.

## How It Works

The community bot wraps the existing meshcore-bot and all its commands (weather, satellite passes, solar, etc.). When a message comes in on a channel, the bot checks with a central coordinator to see if it should respond. The bot with the highest coverage score gets priority. If the coordinator is unreachable, bots fall back to a score-based delay system.

```
Your Radio ──► Community Bot ──► Coordinator API
                    │                   │
                    ▼                   ▼
              All existing        Who should
              commands work       respond?
```

**DMs always work immediately** - coordination only applies to channel messages where multiple bots might see the same request.

## Features

Everything from [meshcore-bot](https://github.com/cj-vana/meshcore-bot), plus:

- **Multi-Bot Coordination** - Only one bot responds per message, based on coverage score
- **Coverage Scoring** - Your bot's score reflects signal quality, reachability, and uptime
- **Automatic Fallback** - Works standalone if coordinator is unreachable
- **Network Reporting** - Messages/packets are reported for network-wide analytics
- **New Commands** - `coverage` (show your score) and `botstatus` (coordinator status)

## Requirements

- Docker & Docker Compose
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
docker compose up -d
```

### 5. Check the logs

```bash
docker compose logs -f
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

| Variable | Required | Description |
|----------|----------|-------------|
| `MESHCORE_CONNECTION_TYPE` | Yes | `serial`, `ble`, or `tcp` |
| `MESHCORE_SERIAL_PORT` | For serial | Device path (e.g., `/dev/ttyUSB0`) |
| `MESHCORE_TCP_HOST` | For TCP | Radio IP address |
| `MESHCORE_BOT_NAME` | Yes | Your bot's display name |
| `MESHCORE_LATITUDE` | Recommended | Your location (for scoring) |
| `MESHCORE_LONGITUDE` | Recommended | Your location (for scoring) |
| `COORDINATOR_URL` | Recommended | Coordinator API URL |
| `COORDINATOR_REGISTRATION_KEY` | For coordinator | Registration key from network admin |
| `MESH_REGION` | Optional | Region code (e.g., `DEN`) |
| `WEB_VIEWER_PORT` | Optional | Web viewer port (default: `8081`) |
| `DISCORD_BOT_WEBHOOK_URL` | Optional | Discord webhook for #bot messages |
| `DISCORD_EMERGENCY_WEBHOOK_URL` | Optional | Discord webhook for #emergency |
| `TZ` | Optional | Timezone (default: `America/Denver`) |
| `N2YO_API_KEY` | Optional | For satellite pass command |
| `AIRNOW_API_KEY` | Optional | For air quality command |

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

| Command | Description |
|---------|-------------|
| `coverage` | Shows your bot's current coverage score |
| `botstatus` | Shows coordinator connection status and network info |

## Updating

Pull the latest changes and rebuild:

```bash
git pull
docker compose up -d --build
```

## Releases

Docker images are automatically built on new releases and available at:
```
ghcr.io/cj-vana/meshcore-community-bot:latest
```

To use the pre-built image instead of building locally, update your `docker-compose.yml`:
```yaml
services:
  community-bot:
    image: ghcr.io/cj-vana/meshcore-community-bot:latest
    # ... rest of config
```

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
