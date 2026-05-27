# Community Bot Setup Guide

This guide walks you through connecting your MeshCore radio to the community bot using the current firmware-native coordination design.

## What You Need

- A MeshCore-compatible radio (Heltec V3, RAK Wireless, T-Beam, etc.)
- A computer to run the bot (Raspberry Pi, VPS, desktop, etc.)
- Docker & Docker Compose installed ([Get Docker](https://docs.docker.com/get-docker/))
- `make` — on Debian/Ubuntu: `sudo apt-get install -y make`
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
make up
```

Check the logs to make sure it's working:

```bash
make logs
```

You should see:

```
[INFO] Starting MeshCore Community Bot...
[INFO] Community bot initialized (firmware-native coordination)
[INFO] Bot is running.
```

## Step 6: Verify

Send a DM to your bot from another MeshCore device with `ping` - you should get `Pong!` back.

For a channel-level coordination check, send `ping` or `test` on `#bot` from a mesh radio. The reply should be prefixed with a 4-hex token, for example:

```
[1a2b] Pong!
```

To inspect local coordination state, DM the bot with `botstatus`.

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
make redeploy
```

## How Coordination Works

When multiple bots are on the same mesh:

1. A user sends a command on the `#bot` channel
2. All bots that hear it compute the same request fingerprint and request token locally
3. Each bot computes a firmware-compatible delay from channel type, hops, queue depth, bot identity seed, and jitter
4. The first bot to respond transmits a token-prefixed message like `[1a2b] Pong!`
5. Any other bot still waiting suppresses itself if it hears that same token first

There is no coordinator service and no registration step. All coordination happens locally on each bot.

## Troubleshooting

### "Serial device not found"

- Check the USB cable is plugged in
- Run `ls /dev/ttyUSB*` to find the correct device
- Update `MESHCORE_SERIAL_PORT` in `.env`

### "Failed to connect to MeshCore node"

- Make sure the radio is powered on and in Companion mode
- Try unplugging and replugging the USB cable
- Check the serial port isn't being used by another program

### "Firmware coordinator identity seed not set"

- The bot derives a tie-break seed from the radio public key after connect
- Check radio connectivity and log output for `self_info`
- Coordination still works with seed `0`, but tie-break behavior is less distinctive until the radio identity is available

### Bot isn't responding to messages

- Check `make logs` (i.e.`docker compose logs -f`) for errors
- Make sure the channel is in `monitor_channels` in config.ini
- Check if you're rate-limited (default: 10 seconds between responses)

## Getting Help

Reach out on the Colorado Mesh Discord or open an issue on GitHub.
