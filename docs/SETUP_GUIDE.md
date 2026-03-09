# Community Bot Setup Guide

This guide walks you through connecting your MeshCore radio to the Denver MeshCore bot network.

## What You Need

- A MeshCore-compatible radio (Heltec V3, RAK Wireless, T-Beam, etc.)
- A computer to run the bot (Raspberry Pi, VPS, desktop, etc.)
- Docker installed ([Get Docker](https://docs.docker.com/get-docker/))
- A USB cable, BLE, or TCP connection to your radio

## Step 1: Get the Code

```bash
git clone --recurse-submodules https://github.com/cj-vana/meshcore-community-bot.git
cd meshcore-community-bot
```

## Step 2: Find Your Radio

Plug in your radio via USB and find the device:

```bash
# Linux
ls /dev/ttyUSB*
# or
ls /dev/ttyACM*

# macOS
ls /dev/cu.usb*
```

Note the device path (e.g., `/dev/ttyUSB0`).

## Step 3: Configure

```bash
cp .env.example .env
cp config.ini.example config.ini
```

Edit `.env` with your details:

```env
# Connection - match your radio setup
MESHCORE_CONNECTION_TYPE=serial
MESHCORE_SERIAL_PORT=/dev/ttyUSB0

# Identity - pick a unique name
MESHCORE_BOT_NAME=YourBotName

# Location - helps with coverage scoring
MESHCORE_LATITUDE=39.7392
MESHCORE_LONGITUDE=-104.9903

# Region - your mesh region code
MESH_REGION=DEN

# Coordinator - connects you to the network
COORDINATOR_URL=https://coordinator.denvermc.com

# Timezone
TZ=America/Denver
```

### TCP Connection (remote radio)

If your radio is on the network (not USB):

```env
MESHCORE_CONNECTION_TYPE=tcp
MESHCORE_TCP_HOST=192.168.1.100
MESHCORE_TCP_PORT=5555
```

### BLE Connection

```env
MESHCORE_CONNECTION_TYPE=ble
MESHCORE_BLE_DEVICE=YourRadioName
```

## Step 4: Configure Channels

Edit `config.ini` and set which channels your bot monitors:

```ini
[Channels]
monitor_channels = #bot
respond_to_dms = true
```

## Step 5: Start the Bot

```bash
docker compose up -d
```

Check the logs to make sure it's working:

```bash
docker compose logs -f
```

You should see:

```
[INFO] Starting MeshCore Community Bot...
[INFO] Registered with coordinator as YourBotName
[INFO] Bot is running.
```

## Step 6: Verify

Send a DM to your bot from another MeshCore device with `ping` - you should get `Pong!` back.

Check your bot is visible on the network:

```bash
curl https://coordinator.denvermc.com/api/v1/bots
```

## Optional: Discord Webhooks

To forward mesh messages to Discord, create webhooks in your Discord server and add them to `.env`:

```env
DISCORD_BOT_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook
DISCORD_EMERGENCY_WEBHOOK_URL=https://discord.com/api/webhooks/your/emergency-webhook
```

## Optional: API Keys

Some commands need API keys to work:

| Key              | Command   | Get It From                                  |
| ---------------- | --------- | -------------------------------------------- |
| `N2YO_API_KEY`   | `satpass` | [n2yo.com](https://www.n2yo.com/api/)        |
| `AIRNOW_API_KEY` | `aqi`     | [airnowapi.org](https://docs.airnowapi.org/) |

Add them to `.env`:

```env
N2YO_API_KEY=your-key-here
AIRNOW_API_KEY=your-key-here
```

## Updating Your Bot

```bash
cd meshcore-community-bot
git pull --recurse-submodules
docker compose up -d --build
```

## Checking Your Status

From any MeshCore device, DM your bot with `coverage` to see legacy coordinator stats, or `botstatus` for full network info.

## How Coordination Works

When multiple bots are on the same mesh:

1. A user sends a command on the `#bot` channel
2. All bots see it and compute a **delivery score** based on path metrics
3. Bots bid with the coordinator - highest delivery score wins
4. Only that bot responds - no duplicate messages

If the coordinator is unreachable, bots use the same delivery score for fallback delay - best-path bot responds fastest.

**Delivery score** (per-message) is based on:

- Infrastructure score: harmonic mean of path-node links/fan-in (40%)
- Hop count: closer is better (35%)
- Path familiarity: exact sender+path match bonus (15%)
- Path freshness: recent observations (10%)

The better your radio's infrastructure position and the more familiar paths it observes, the higher your delivery scores.

## Troubleshooting

### "Serial device not found"

- Check the USB cable is plugged in
- Run `ls /dev/ttyUSB*` to find the correct device
- Update `MESHCORE_SERIAL_PORT` in `.env`

### "Failed to connect to MeshCore node"

- Make sure the radio is powered on and in Companion mode
- Try unplugging and replugging the USB cable
- Check the serial port isn't being used by another program

### "Coordinator registration failed"

- This is OK - your bot still works in standalone mode
- Check that `COORDINATOR_URL` is correct
- The bot will retry automatically on the next heartbeat

### Bot isn't responding to messages

- Check `docker compose logs -f` for errors
- Make sure the channel is in `monitor_channels` in config.ini
- Check if you're rate-limited (default: 10 seconds between responses)

## Getting Help

Reach out on the Denver MeshCore Discord or open an issue on GitHub.
