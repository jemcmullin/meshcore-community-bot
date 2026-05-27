# MeshCore Community Bot

A firmware-native multi-bot MeshCore radio bot built on top of [meshcore-bot](https://github.com/agessaman/meshcore-bot). It keeps the upstream bot as an upgradeable git submodule and adds a community layer that prevents duplicate channel replies without requiring any central coordinator service.

## How It Works

The community bot wraps the existing meshcore-bot and all its commands. When a channel message arrives, each bot independently computes the same request fingerprint from the inbound packet, derives a 16-bit response token, and waits a deterministic firmware-compatible delay. If a bot hears another peer reply first with the same `[xxxx]` token, it suppresses its own pending response.

```
Your Radio ──► Community Bot ──► Deterministic local delay
        │                       │
        ▼                       ▼
      All existing            Peer-heard token
      commands work           suppression
```

DMs bypass coordination entirely and respond immediately.

## Firmware-Native Coordination

Channel coordination now works like this:

1. A channel message is received.
2. The bot computes a firmware-compatible FNV-1a request fingerprint from the raw inbound packet text and metadata.
3. The low 16 bits become the request token shown in channel replies as `[xxxx]`.
4. The bot computes a total delay using the firmware timing formula: base delay, channel bias, hop bias, queue bias, tie-break bias, and jitter.
5. While waiting, the bot observes incoming channel traffic. If another bot replies first with the same token prefix, the pending reply is suppressed.
6. If the wait completes without suppression, the bot prepends `[xxxx] ` to the response text and sends it.

This design is fully decentralized. No HTTP coordinator, registration key, heartbeat, or external scoring service is required.

## Features

Everything from [meshcore-bot](https://github.com/agessaman/meshcore-bot) as an unmodified upgradeable submodule, plus:

- **Firmware-Native Multi-Bot Coordination** - Channel replies use deterministic local delays and token-based suppression instead of a central coordinator
- **Firmware-Compatible Fingerprinting** - Request tokens and response timing are computed from raw inbound packet text and firmware-matched message fingerprints
- **Discord Integration** - Incoming and outgoing mesh messages forwarded to Discord webhooks
- **New Commands** - `botstatus` (firmware coordination state) and `botreps` (top infrastructure relays seen by this bot, DM only)
- **Web Viewer Community Dashboard** - Real-time visualization of firmware coordination events and packet stream data

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
[INFO] Community bot initialized (firmware-native coordination)
[INFO] Bot is running. Press Ctrl+C to stop.
```

## Configuration

### Environment Variables

Set these in your `.env` file:

| Variable                        | Required    | Description                          |
| ------------------------------- | ----------- | ------------------------------------ |
| `MESHCORE_CONNECTION_TYPE`      | Yes         | `serial`, `ble`, or `tcp`            |
| `MESHCORE_SERIAL_PORT`          | For serial  | Device path (e.g., `/dev/ttyUSB0`)   |
| `MESHCORE_TCP_HOST`             | For TCP     | Radio IP address                     |
| `MESHCORE_BOT_NAME`             | Yes         | Your bot's display name              |
| `MESHCORE_LATITUDE`             | Recommended | Your location                        |
| `MESHCORE_LONGITUDE`            | Recommended | Your location                        |
| `MESH_REGION`                   | Optional    | Region code (e.g., `DEN`)            |
| `WEB_VIEWER_PORT`               | Optional    | Web viewer port (default: `8081`)    |
| `DISCORD_BOT_WEBHOOK_URL`       | Optional    | Discord webhook for #bot messages    |
| `DISCORD_EMERGENCY_WEBHOOK_URL` | Optional    | Discord webhook for #emergency       |
| `TZ`                            | Optional    | Timezone (default: `America/Denver`) |
| `N2YO_API_KEY`                  | Optional    | For satellite pass command           |
| `AIRNOW_API_KEY`                | Optional    | For air quality command              |

### Config File

`config.ini` controls bot behavior (keywords, channels, rate limiting, etc.). See meshcore-bot [config.ini.example](config.ini.example) for all options.

Key settings:

- `[Channels] monitor_channels` - Which channels to monitor (default: `#bot`)
- `[Channels] respond_to_dms` - Whether to respond to DMs (default: `true`)
- `[Community] mesh_region` - Optional region code for deployment naming and metadata

## Operating Mode

The bot always runs in firmware-native coordination mode. There is no coordinator dependency and no degraded standalone fallback mode to configure. If multiple community bots hear the same channel request, they resolve the race locally using deterministic delay plus peer-heard token suppression.

## Commands

All commands from meshcore-bot are available, plus:

| Command     | Description                                                             |
| ----------- | ----------------------------------------------------------------------- |
| `botstatus` | Firmware coordination status, pending count, recent count, uptime       |
| `botreps`   | Top infrastructure relays seen by this bot, ranked by fan-in (DM only)  |
| `test`      | Custom `test`/`t` response format with optional phrase and path metrics |

Additional `Keywords.test` placeholders: `direct_signal` (direct/0-hop SNR+RSSI only) and `path_hash_size` (path byte length; direct empty unless explicit metadata, unknown `?`).

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

The `meshcore-bot` submodule tracks the upstream `main` branch by default.

To update the submodule to the latest `main` branch revision:

```bash
git submodule update --remote --merge
git add meshcore-bot
git commit -m 'chore: update meshcore-bot submodule'
make restart
```

If you need to temporarily pin to a specific commit (bug resolution):

```bash
cd meshcore-bot
git fetch
git checkout <commit-sha>   # or a branch/tag, e.g. git checkout main
cd ..
make restart
```

If you want to track the bleeding-edge `dev` branch from `meshcore-bot`:

```bash
make redeploy-dev
```

This pulls the latest community code, updates the submodule to `dev`, and rebuilds. For ongoing updates while staying on `dev`, use `make redeploy-dev` instead of `make redeploy`.

Silent failure risks on `dev`:

- Firmware coordination interception can stop working if upstream changes patched method names/signatures (`handle_channel_message`, `process_message`, `send_response`, `send_channel_message`).
- Community dashboard stats can silently degrade if upstream DB schema changes affect `mesh_connections` or `complete_contact_tracking`.

To switch back to `main`, run `make redeploy` — it resets the submodule to `main` as part of the normal update flow.

> **Note:** This is a local override only — it does not affect other users. Use `make redeploy` to return to `main`.

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

**Firmware identity seed not set:**

- The bot derives its local tie-break seed from the radio public key after connect
- Check radio connectivity and `self_info` availability in logs
- Coordination still works with seed `0`, but tie-break behavior is less distinctive across bots until the radio identity is available

**Commands not responding:**

- Check `docker compose logs -f` for errors
- Verify the channel is in `monitor_channels` in config.ini
- Check rate limiting settings

## License

Private - contact for access.
