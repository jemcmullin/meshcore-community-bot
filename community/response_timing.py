# This module has been removed.
# Response timing is now handled by the firmware coordination formula
# in community/meshcore_response_coordinator.py and community/firmware_coordinator.py.
raise ImportError("response_timing has been removed — use firmware_coordinator instead")


# Delay parameters — more hops means more delay so closer bots respond first
BASE_DELAY_MS = 200   # Delay for a direct (0-hop) message
HOP_DELAY_MS = 600    # Additional delay added per inbound hop
MAX_JITTER_MS = 150   # Random jitter to prevent simultaneous transmissions

class ResponseTiming:
    """Response timing including fallback based on inbound hop count."""

    async def wait_delay_ms(self, delay_ms: int) -> None:
        """Wait an explicit delay in milliseconds (used for coordinator-provided delays)."""
        if delay_ms > 0:
            logger.info(f"Coordinator delay: waiting {delay_ms}ms")
            await asyncio.sleep(delay_ms / 1000.0)

    async def fallback_wait_before_responding(self, hops: int = 0) -> float:
        """Wait the computed hop-based delay before responding (fallback when coordinator unreachable)."""
        delay_ms = self.compute_fallback_delay_ms(hops)
        delay_s = delay_ms / 1000.0
        logger.info(f"Fallback mode: waiting {delay_ms}ms (hops={hops})")
        await asyncio.sleep(delay_s)
        return delay_s
            
    def compute_fallback_delay_ms(self, hops: int = 0) -> int:
        """Compute response delay. More hops = longer delay."""
        delay = BASE_DELAY_MS + hops * HOP_DELAY_MS
        jitter = random.randint(0, MAX_JITTER_MS)
        return delay + jitter