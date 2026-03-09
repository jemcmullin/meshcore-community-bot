"""Score-based delay fallback when coordinator is unreachable."""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("CommunityBot")

# Delay parameters (used when no ScoringConfig is provided)
BASE_DELAY_MS = 2000
MIN_DELAY_MS = 100
MAX_JITTER_MS = 200

# Score degradation
DEGRADE_AFTER_SECONDS = 3600
DEGRADE_TARGET = 0.5


@dataclass
class _DefaultConfig:
    """Fallback constants when no ScoringConfig is injected."""
    base_delay_ms: int = BASE_DELAY_MS
    min_delay_ms: int = MIN_DELAY_MS
    max_jitter_ms: int = MAX_JITTER_MS
    degrade_after_seconds: int = DEGRADE_AFTER_SECONDS
    degrade_target: float = DEGRADE_TARGET
    degrade_window_seconds: int = 86400
    fallback_min_delivery_score: float = 0.30
    infrastructure_weight: float = 0.40
    hop_weight: float = 0.35
    path_bonus_weight: float = 0.15
    freshness_weight: float = 0.10


class CoverageFallback:
    """Fallback response timing based on per-message delivery score.
    
    Note: cached_score/effective_score still exist for backward compatibility
    with commands during testing, but are NOT used in delay calculation.
    """

    def __init__(self, scoring_config=None):
        self.cached_score: float = 0.5
        self.last_coordinator_contact: float = time.time()
        self._cfg = scoring_config if scoring_config is not None else _DefaultConfig()

    def update_score(self, score: float):
        """Update cached score from coordinator heartbeat."""
        self.cached_score = score
        self.last_coordinator_contact = time.time()

    @property
    def effective_score(self) -> float:
        """Get the effective score, degraded if coordinator hasn't been contacted recently."""
        elapsed = time.time() - self.last_coordinator_contact
        if elapsed <= self._cfg.degrade_after_seconds:
            return self.cached_score

        degrade_progress = min(
            1.0,
            (elapsed - self._cfg.degrade_after_seconds) / self._cfg.degrade_window_seconds,
        )
        return self.cached_score + (self._cfg.degrade_target - self.cached_score) * degrade_progress

    def compute_delay_ms(self) -> int:
        """Compute response delay based on effective score.

        Higher score = shorter delay:
          Score 1.0 → ~100-300ms
          Score 0.5 → ~1100-1300ms
          Score 0.0 → ~2100-2300ms
        """
        score = self.effective_score
        delay = self._cfg.base_delay_ms * (1.0 - score) + self._cfg.min_delay_ms
        jitter = random.randint(0, self._cfg.max_jitter_ms)
        return int(delay + jitter)

    def compute_delay_ms_with_signal(
        self,
        hops: Optional[int],
        infrastructure: Optional[float] = None,
        path_bonus: Optional[float] = None,
        path_freshness: Optional[float] = None,
    ) -> int:
        """Compute delay using only per-message delivery score.

        Uses the same delivery score formula as the coordinator bid so the
        nearest / best-path bot wins the race when coordinator is unreachable.
        """
        from .coordinator_client import CoordinatorClient

        delivery_score = CoordinatorClient.compute_delivery_score(
            infrastructure=infrastructure,
            inbound_hops=hops,
            path_bonus=path_bonus,
            path_freshness=path_freshness,
            w_infrastructure=self._cfg.infrastructure_weight,
            w_hops=self._cfg.hop_weight,
            w_path_bonus=self._cfg.path_bonus_weight,
            w_freshness=self._cfg.freshness_weight,
        )
        # Use delivery score directly - no blending with coordinator coverage score
        delay = self._cfg.base_delay_ms * (1.0 - delivery_score) + self._cfg.min_delay_ms
        jitter = random.randint(0, self._cfg.max_jitter_ms)
        total_delay = int(delay + jitter)
        logger.info(
            "Fallback delay: delivery_score=%.3f delay_ms=%d",
            delivery_score,
            total_delay,
        )
        return total_delay

    async def wait_before_responding(self) -> float:
        """Wait the computed delay before responding.

        Returns the delay in seconds that was waited.
        """
        delay_ms = self.compute_delay_ms()
        delay_s = delay_ms / 1000.0
        logger.info(
            f"Fallback mode: waiting {delay_ms}ms "
            f"(score={self.effective_score:.2f})"
        )
        await asyncio.sleep(delay_s)
        return delay_s

    async def wait_before_responding_with_signal(
        self,
        hops: Optional[int],
        infrastructure: Optional[float] = None,
        path_bonus: Optional[float] = None,
        path_freshness: Optional[float] = None,
    ) -> float:
        """Delivery-score-aware fallback delay. Returns seconds waited."""
        delay_ms = self.compute_delay_ms_with_signal(
            hops, infrastructure, path_bonus, path_freshness
        )
        delay_s = delay_ms / 1000.0
        logger.info(
            f"Fallback mode (signal-aware): waiting {delay_ms}ms "
            f"(hops={hops}, infra={infrastructure}, path_bonus={path_bonus}, fresh={path_freshness})"
        )
        await asyncio.sleep(delay_s)
        return delay_s
