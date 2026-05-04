"""Community bot configuration handling."""

import os
from dataclasses import dataclass


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


