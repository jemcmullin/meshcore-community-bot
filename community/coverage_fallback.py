"""Score-based delay fallback when coordinator is unreachable."""

import asyncio
import logging
import random
import time
from typing import Optional

logger = logging.getLogger('CommunityBot')

# Delay parameters
BASE_DELAY_MS = 2000  # Maximum delay window
MIN_DELAY_MS = 100  # Even highest-scored bot waits a bit
MAX_JITTER_MS = 200  # Random jitter to prevent ties

# Score degradation
DEGRADE_AFTER_SECONDS = 3600  # Start degrading after 1 hour without coordinator
DEGRADE_TARGET = 0.5  # Degrade toward neutral midpoint


class CoverageFallback:
    """Fallback response timing based on cached coverage score."""

    def __init__(self):
        self.cached_score: float = 0.5
        self.last_coordinator_contact: float = time.time()

    def update_score(self, score: float):
        """Update cached score from coordinator heartbeat."""
        self.cached_score = score
        self.last_coordinator_contact = time.time()

    @property
    def effective_score(self) -> float:
        """Get the effective score, degraded if coordinator hasn't been contacted recently."""
        elapsed = time.time() - self.last_coordinator_contact
        if elapsed <= DEGRADE_AFTER_SECONDS:
            return self.cached_score

        # Linearly degrade toward DEGRADE_TARGET over 24 hours
        degrade_progress = min(1.0, (elapsed - DEGRADE_AFTER_SECONDS) / 86400)
        return self.cached_score + (DEGRADE_TARGET - self.cached_score) * degrade_progress

    def compute_delay_ms(self, delivery_score: Optional[float] = None) -> int:
            """Compute response delay based on delivery score (preferred) or effective score.
            Higher score = shorter delay.
            """
            score = delivery_score if delivery_score is not None else self.effective_score
            delay = BASE_DELAY_MS * (1.0 - score) + MIN_DELAY_MS
            jitter = random.randint(0, MAX_JITTER_MS)
            return int(delay + jitter)

    async def wait_before_responding(self, delivery_score: Optional[float] = None) -> float:
        """Wait the computed delay before responding. Accepts per-message delivery score."""
        delay_ms = self.compute_delay_ms(delivery_score)
        delay_s = delay_ms / 1000.0
        logger.info(
            f"Fallback mode: waiting {delay_ms}ms "
            f"(score={delivery_score if delivery_score is not None else self.effective_score:.2f})"
        )
        await asyncio.sleep(delay_s)
        return delay_s
