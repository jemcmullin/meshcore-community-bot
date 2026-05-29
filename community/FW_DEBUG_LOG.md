FW Debug Log — quick reference

Purpose

- Quick reference for where FW coordination diagnostics are written and useful CLI commands to capture/inspect them.

Files touched

- community/message_interceptor.py — emits `[FW_DIAG]` info log entries and appends JSON lines to diagnostics file (env `FW_DIAG_LOG`, defaults to `/tmp/mesh_fw_diag.log`).
- community/firmware_coordinator.py — records recently observed peer tokens and exposes `get_observed_peer_tokens(window_ms)` for diagnostics.
- meshcore-bot/modules/message_handler.py — attaches `payload_hex` / `payload_bytes` to `MeshMessage` so the interceptor can compute firmware-exact fingerprints.

Default diagnostic file

- `/tmp/mesh_fw_diag.log` (override with `FW_DIAG_LOG` env var)

Common commands

# Set custom diag path and run the bot (writes JSON lines as events occur)

export FW_DIAG_LOG=/path/to/fw_diag.log
python3 community_bot.py

# Tail live JSON diag file

tail -F /tmp/mesh_fw_diag.log

# Show last 20 entries, pretty-printed with jq

tail -n 20 /tmp/mesh_fw_diag.log | jq .

# Show just token + observed peers + raw prefix columns

tail -n 50 /tmp/mesh_fw_diag.log | jq -r '"ts=" + (.ts_ms|tostring) + " token=" + (.token // "null") + " peers=" + (.observed_peers|tostring) + " raw=" + (.raw_hex_prefix // "null")'

# Grep application logs (logger name CommunityBot) for FW_DIAG lines

# If running in foreground and logs go to stdout

grep "FW_DIAG" -n /path/to/app.log

# Run targeted pytest verification (tokens & mismatch tests)

pytest -q tests/test_mismatch_tokens.py
pytest -q tests/test_mismatch_regression.py

# Run a single failing test to reproduce quickly

pytest -q tests/test_mismatch_tokens.py::test_real_messages_match_firmware_token -q

Notes & tips

- `raw_hex_prefix` in the JSON is truncated to keep logs small — use the app-level debug logs in `CommunityBot` logger for full output if enabled.
- The diagnostics file is appended to; rotate or remove it between runs to avoid mixing captures from different sessions.
- To capture observed peer tokens within the 20s window used for diagnostics, run the bot and capture traffic for ~10–30s while peers respond; the JSON lines will include `observed_peers` (4-hex strings) for comparison.

If you want a different filename, more fields in the JSON, or automatic rotation/CLI wrapper scripts, I can add them.
