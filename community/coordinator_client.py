"""HTTP client for the MeshCore Coordinator API."""

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class CoordinatorClient:
    """Client for communicating with the central MeshCore Coordinator."""

    def __init__(self, base_url: str, timeout_ms: int = 100, data_dir: str = "data", registration_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms
        self.registration_key = registration_key
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.bot_id: Optional[str] = None
        self.bot_token: str = ""
        self.current_score: float = 0.5
        self.active_bots: int = 0
        self.heartbeat_interval: int = 30
        self._last_score_update: float = 0.0

        # Load saved token
        self._load_token()

        # HTTP client lazily initialized on first use (avoids event-loop
        # binding issues when __init__ runs before asyncio starts)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        """Check if the coordinator URL is configured."""
        return bool(self.base_url)

    @property
    def is_registered(self) -> bool:
        """Check if this bot is registered with the coordinator."""
        return bool(self.bot_id and self.bot_token)

    def _token_path(self) -> Path:
        return self.data_dir / ".bot_token"

    def _botid_path(self) -> Path:
        return self.data_dir / ".bot_id"

    def _load_token(self):
        """Load saved bot token and ID from disk."""
        try:
            if self._token_path().exists():
                self.bot_token = self._token_path().read_text().strip()
            if self._botid_path().exists():
                self.bot_id = self._botid_path().read_text().strip()
        except Exception as e:
            logger.warning(f"Failed to load saved token: {e}")

    def _save_token(self):
        """Save bot token and ID to disk."""
        try:
            self._token_path().write_text(self.bot_token)
            self._token_path().chmod(0o600)
            self._botid_path().write_text(self.bot_id or "")
        except Exception as e:
            logger.warning(f"Failed to save token: {e}")

    def _auth_headers(self) -> dict:
        """Get authorization headers."""
        if self.bot_token:
            return {"Authorization": f"Bearer {self.bot_token}"}
        return {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared AsyncClient, creating it on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(5.0, connect=2.0),
            )
        return self._client

    async def register(
        self,
        bot_name: str,
        public_key: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        connection_type: str = "serial",
        capabilities: Optional[list[str]] = None,
        version: str = "0.1.0",
        mesh_region: str = "",
    ) -> bool:
        """Register this bot with the coordinator."""
        if not self.is_configured:
            logger.info("No coordinator URL configured, running standalone")
            return False

        payload = {
            "bot_name": bot_name,
            "public_key": public_key,
            "connection_type": connection_type,
            "capabilities": capabilities or [],
            "version": version,
            "mesh_region": mesh_region,
            "registration_key": self.registration_key,
        }
        if latitude is not None and longitude is not None:
            payload["location"] = {
                "latitude": latitude,
                "longitude": longitude,
            }

        try:
            client = await self._ensure_client()
            resp = await client.post(
                "/api/v1/bots/register",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            self.bot_id = data["bot_id"]
            self.bot_token = data["bot_token"]
            self.heartbeat_interval = data.get("heartbeat_interval_seconds", 30)
            self._save_token()

            logger.info(f"Registered with coordinator as {bot_name} ({self.bot_id})")
            return True
        except Exception as e:
            logger.warning(f"Failed to register with coordinator: {e}")
            return False

    async def heartbeat(
        self,
        uptime_seconds: int = 0,
        messages_processed: int = 0,
        messages_responded: int = 0,
        connected: bool = True,
        contact_count: int = 0,
        channel_count: int = 0,
    ) -> bool:
        """Send heartbeat to coordinator."""
        if not self.is_registered:
            return False

        try:
            client = await self._ensure_client()
            resp = await client.post(
                "/api/v1/bots/heartbeat",
                json={
                    "bot_id": self.bot_id,
                    "uptime_seconds": uptime_seconds,
                    "messages_processed": messages_processed,
                    "messages_responded": messages_responded,
                    "connected": connected,
                    "contact_count": contact_count,
                    "channel_count": channel_count,
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            self.current_score = data.get("your_score", 0.5)
            self.active_bots = data.get("active_bots", 0)
            self.heartbeat_interval = data.get("next_heartbeat_seconds", 30)
            self._last_score_update = time.time()

            return True
        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")
            return False

    async def should_respond(
        self,
        message_hash: str,
        sender_pubkey: str = "",
        channel: Optional[str] = None,
        content_prefix: str = "",
        is_dm: bool = False,
        timestamp: int = 0,
        receiver_snr: Optional[float] = None,
        receiver_rssi: Optional[int] = None,
        receiver_hops: Optional[int] = None,
        receiver_path: Optional[str] = None,
        outbound_hops: Optional[int] = None,
        path_significance: Optional[float] = None,
        hop_weight: float = 0.6,
        path_sig_weight: float = 0.4,
    ) -> Optional[bool]:
        """Ask coordinator if this bot should respond to a message.

        Includes signal data (SNR, RSSI, hops, path) for the coordinator's
        bidding window to evaluate path quality across competing bots.

        Returns:
            True if should respond, False if should not, None if coordinator unreachable.
        """
        if not self.is_registered:
            return None

        payload = {
            "bot_id": self.bot_id,
            "message_hash": message_hash,
            "sender_pubkey": sender_pubkey,
            "channel": channel or "",
            "content_prefix": content_prefix,
            "is_dm": is_dm,
            "timestamp": timestamp,
        }
        # Include signal data (analytics only)
        if receiver_snr is not None:
            payload["receiver_snr"] = receiver_snr
        if receiver_rssi is not None:
            payload["receiver_rssi"] = receiver_rssi
        if receiver_hops is not None:
            payload["receiver_hops"] = receiver_hops
        if receiver_path is not None:
            payload["receiver_path"] = receiver_path
        # Include computed proximity score as primary bidding field
        sender_proximity_score = self.compute_sender_proximity_score(
            inbound_hops=receiver_hops,
            outbound_hops=outbound_hops,
            path_significance=path_significance,
            hop_weight=hop_weight,
            path_sig_weight=path_sig_weight,
        )
        payload["sender_proximity_score"] = sender_proximity_score

        try:
            client = await self._ensure_client()
            resp = await client.post(
                "/api/v1/coordination/should-respond",
                json=payload,
                headers=self._auth_headers(),
                timeout=httpx.Timeout(self.timeout_ms / 1000.0),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("should_respond", True)
        except Exception as e:
            logger.debug(f"Coordination check failed: {e}")
            return None  # Unreachable - caller should use fallback

    async def report_batch(
        self,
        messages: Optional[list[dict]] = None,
        packets: Optional[list[dict]] = None,
    ) -> bool:
        """Report a batch of messages and packets to the coordinator."""
        if not self.is_registered:
            return False

        try:
            client = await self._ensure_client()
            resp = await client.post(
                "/api/v1/messages/batch",
                json={
                    "bot_id": self.bot_id,
                    "messages": messages or [],
                    "packets": packets or [],
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Batch report failed: {e}")
            return False

    async def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def parse_path_nodes(path_string: Optional[str]) -> list[str]:
        """Extract repeater node IDs from a path string.

        Examples:
          "98,11,a4 (2 hops via Flood)" → ["98", "11", "A4"]
          "Direct"                       → []
          None / ""                      → []
        """
        if not path_string:
            return []
        path_string = path_string.strip()
        if path_string.lower().startswith("direct"):
            return []
        # Strip trailing annotation like " (2 hops via Flood)"
        path_string = re.sub(r"\s*\(.*?\)\s*$", "", path_string).strip()
        if not path_string:
            return []
        return [n.strip().upper() for n in path_string.split(",") if n.strip()]

    @staticmethod
    def compute_sender_proximity_score(
        inbound_hops: Optional[int],
        outbound_hops: Optional[int],
        path_significance: Optional[float],
        hop_weight: float = 0.6,
        path_sig_weight: float = 0.4,
    ) -> float:
        """Compute a blended proximity score for bidding.

        Higher score = this bot is closer to / has a better path to sender.

        hop_score = max(0, 1 - best_hops * 0.25)
        best_hops = min(inbound_hops, outbound_hops) if both available
        blended   = hop_score * hop_weight + path_significance * path_sig_weight
        """
        best_hops: Optional[int] = None
        if inbound_hops is not None and outbound_hops is not None:
            best_hops = min(inbound_hops, outbound_hops)
        elif inbound_hops is not None:
            best_hops = inbound_hops
        elif outbound_hops is not None:
            best_hops = outbound_hops

        if best_hops is not None:
            hop_score = max(0.0, 1.0 - best_hops * 0.25)
        else:
            hop_score = 0.5  # unknown — neutral

        sig = path_significance if path_significance is not None else 0.5

        return hop_score * hop_weight + sig * path_sig_weight

    @staticmethod
    def compute_message_hash(
        sender_pubkey: str, content: str, timestamp: int
    ) -> str:
        """Compute a deterministic hash for message deduplication.

        Uses 10-second time buckets so bots that receive the same message
        at slightly different times produce the same hash.
        """
        bucket = timestamp // 10
        raw = f"{sender_pubkey}:{content}:{bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()
