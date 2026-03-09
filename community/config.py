"""Community bot configuration handling."""

import os
from dataclasses import dataclass, field


@dataclass
class CoordinatorConfig:
    """Configuration for coordinator connectivity."""

    url: str = ""
    registration_key: str = ""
    bot_token: str = ""
    heartbeat_interval: int = 30
    coordination_timeout_ms: int = 500
    batch_interval_seconds: int = 5
    batch_max_size: int = 50
    mesh_region: str = ""

    @classmethod
    def from_env_and_config(cls, config) -> "CoordinatorConfig":
        """Load coordinator config from environment variables and config.ini."""
        return cls(
            url=os.environ.get(
                "COORDINATOR_URL",
                config.get("Coordinator", "url", fallback=""),
            ),
            registration_key=os.environ.get(
                "COORDINATOR_REGISTRATION_KEY",
                config.get("Coordinator", "registration_key", fallback=""),
            ),
            bot_token="",  # Loaded from file at runtime
            heartbeat_interval=int(
                os.environ.get(
                    "COORDINATOR_HEARTBEAT_INTERVAL",
                    config.get("Coordinator", "heartbeat_interval", fallback="30"),
                )
            ),
            coordination_timeout_ms=int(
                os.environ.get(
                    "COORDINATOR_TIMEOUT_MS",
                    config.get("Coordinator", "timeout_ms", fallback="500"),
                )
            ),
            batch_interval_seconds=int(
                os.environ.get(
                    "COORDINATOR_BATCH_INTERVAL",
                    config.get("Coordinator", "batch_interval", fallback="5"),
                )
            ),
            batch_max_size=int(
                os.environ.get(
                    "COORDINATOR_BATCH_SIZE",
                    config.get("Coordinator", "batch_size", fallback="50"),
                )
            ),
            mesh_region=os.environ.get(
                "MESH_REGION",
                config.get("Coordinator", "mesh_region", fallback=""),
            ),
        )


@dataclass
class ScoringConfig:
    """Configuration for delivery scoring and fallback timing."""

    infrastructure_weight: float = 0.40
    hop_weight: float = 0.35
    path_bonus_weight: float = 0.15
    freshness_weight: float = 0.10
    base_delay_ms: int = 2000
    min_delay_ms: int = 100
    max_jitter_ms: int = 200
    degrade_after_seconds: int = 3600
    degrade_target: float = 0.5
    degrade_window_seconds: int = 86400

    @classmethod
    def from_env_and_config(cls, config) -> "ScoringConfig":
        """Load scoring config from [Scoring] section and SCORING_* env vars."""

        def _get(key, default):
            env_key = f"SCORING_{key.upper()}"
            if env_key in os.environ:
                return os.environ[env_key]
            return config.get("Scoring", key, fallback=str(default))

        return cls(
            infrastructure_weight=float(_get("infrastructure_weight", 0.40)),
            hop_weight=float(_get("hop_weight", 0.35)),
            path_bonus_weight=float(_get("path_bonus_weight", 0.15)),
            freshness_weight=float(_get("freshness_weight", 0.10)),
            base_delay_ms=int(_get("base_delay_ms", 2000)),
            min_delay_ms=int(_get("min_delay_ms", 100)),
            max_jitter_ms=int(_get("max_jitter_ms", 200)),
            degrade_after_seconds=int(_get("degrade_after_seconds", 3600)),
            degrade_target=float(_get("degrade_target", 0.5)),
            degrade_window_seconds=int(_get("degrade_window_seconds", 86400)),
        )
