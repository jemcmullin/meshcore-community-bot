# MeshCore Community Bot

A multi-bot-aware MeshCore mesh radio bot with coordinated response priority. Built on top of [meshcore-bot](https://github.com/agessaman/meshcore-bot), adding central coordinator integration so multiple bots on the same mesh don't all respond to the same message.

## How It Works

The community bot wraps the existing meshcore-bot and all its commands (weather, satellite passes, solar, etc.). When a channel message arrives, the bot forwards its raw signal data (SNR, RSSI, hops, path) to a central coordinator. The coordinator evaluates all competing bids and assigns one bot to respond. If the coordinator is unreachable, bots fall back to a hop-count-based delay so a likely closer bot responds first.

```
Your Radio ──► Community Bot ──► Coordinator API
                    │                   │
                    ▼                   ▼
              All existing        Who should
              commands work       respond?
```

**DMs always work immediately** - coordination only applies to channel messages where multiple bots might see the same request.

## How the Coordinator Decides Which Bot Responds

When a message is heard by multiple bots at the same time, only one is chosen as primary to respond — otherwise the network gets flooded with duplicate responses.

Here's the coordinator's decision process:

1. **All bots that hear the message check in** with the coordinator within a short window (300 ms). Each bot reports what it heard: signal strength, number of hops the message took, and which repeaters it passed through.

2. **The coordinator scores each bot** based on three things:
   - **Route quality (50%)** — Did the message travel through well-established, widely-used repeaters? A node that many different mesh members route through regularly is trusted infrastructure. A personal node used by only one person scores lower.
   - **Signal strength (25%)** — Was the final link to this bot clean? A good signal implies a higher chance of a reliable connection on the return path. Alternatively, a weak signal is a higher risk of the reply getting lost, so it scores lower. Signal score is bracketed and only penalized on the low end to avoid penalizing bots with less but still usable signal.
   - **Hop count (25%)** — Fewer hops = fewer risks on the reply.

3. **The highest-scoring bot is told to respond.** All others are told to stay silent. The winner is the bot most likely to successfully get a reply _back_ to the original sender.

4. **Optionally, one or more extra bots may also respond** after a short delay (1–1.5 seconds). This is a configurable on the coordinator: this mainly for additional response diversity and is partially a backup mechanism. It also gives quieter bots occasional turns, which helps the coordinator learn more about the network over time.

> **Why route quality dominates:** Signal only tells about the last hop _to_ the bot. It's a small part of whether the reply can make it _back_ to the sender. A message that arrived through an observed, heavily-used repeater is structurally more reliable for the return trip — even if the signal was less (but still usable) than other bots.

## Features

Everything from [meshcore-bot](https://github.com/agessaman/meshcore-bot) as an unmodified upgradeable submodule, plus:

- **Multi-Bot Coordination** - Only one bot responds per channel message; the coordinator picks the best bot based on signal data from all competing bots
- **Automatic Fallback** - Works standalone if coordinator is unreachable; hop-based delay ensures the closest bot responds first
- **Network Reporting** - Messages and packets are reported to the coordinator in batches for network-wide analytics
- **Discord Integration** - Incoming and outgoing mesh messages forwarded to Discord webhooks
- **New Commands** - `botstatus` (coordinator connection and network info) and `bot_top_repeaters` (top infrastructure relays seen by this bot, DM only)
- **Web Viewer Community Dashboard** - Real-time visualization of coordination decisions and packet stream

## Requirements

- Docker & Docker Compose
- Make — on Debian/Ubuntu: `sudo apt-get install -y make` (or use Docker Compose and git submodule commands directly)
- MeshCore-compatible radio (Heltec V3, RAK Wireless, etc.)
- USB cable, BLE, or TCP connection to the radio

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/cj-vana/meshcore-community-bot.git
cd meshcore-community-bot
```

### 2. Configure your environment

Find your radio's serial port:

```bash
ls /dev/serial/by-id/* /dev/ttyUSB*
```

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Your radio connection
MESHCORE_CONNECTION_TYPE=serial
MESHCORE_SERIAL_PORT=/dev/serial/by-id/usb-YourDeviceID # or /dev/ttyUSB0, etc.

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
make start
```

This builds and starts the bot, immediately tails the logs. Ctrl+C stops the log tail but leaves the bot running.

> Or use `make up` to start in the background without attaching to logs. Both initialize the git submodule automatically.

### 5. Check the logs

If you used `make up`, check the logs with:

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

`config.ini` controls bot behavior (keywords, channels, rate limiting, etc.). See meshcore-bot [config.ini.example](config.ini.example) for all options.

Key settings:

- `[Channels] monitor_channels` - Which channels to monitor (default: `#bot`)
- `[Channels] respond_to_dms` - Whether to respond to DMs (default: `true`)
- `[Coordinator]` section - Coordinator-specific settings (usually set via env vars)

## Standalone Mode

If `COORDINATOR_URL` is empty or the coordinator is unreachable, the bot runs standalone - just like a regular meshcore-bot. All commands work normally, there's just no multi-bot coordination.

## Commands

All commands from meshcore-bot are available, plus:

| Command             | Description                                                             |
| ------------------- | ----------------------------------------------------------------------- |
| `botstatus`         | Coordinator connection status, active bot count, uptime, fallback delay |
| `bot_top_repeaters` | Top infrastructure relays seen by this bot, ranked by fan-in (DM only)  |

## Updating

Update to the latest version and redeploy:

_will pull the latest code, initialize the submodule, rebuild the Docker image, and restart the bot with log tailing_

```bash
make redeploy
```

or

```bash
make pull
make build
make down
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
make down
make up
```

If you need to temporarily pin to a specific commit (bug resolution):

```bash
cd meshcore-bot
git fetch
git checkout <commit-sha>   # or a branch/tag, e.g. git checkout main
cd ..
make build
make down
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
      - "${MESHCORE_SERIAL_PORT:-/dev/ttyUSB0}:/dev/meshcore-usb"
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
  --device "/dev/ttyUSB0:/dev/meshcore-usb" \
  -v $(pwd)/config.ini:/app/config.ini:rw \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -p 8081:8081 \
  -e MESHCORE_CONNECTION_TYPE=serial \
  -e MESHCORE_SERIAL_PORT=/dev/meshcore-usb \
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
make submodule
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
