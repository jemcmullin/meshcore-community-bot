"""Community bot configuration handling."""

import os
from dataclasses import dataclass


@dataclass
class CommunityConfig:
    """Configuration for the community bot layer."""

    mesh_region: str = ""
    coordination_debug_no_send: bool = False
    coordination_mode_queue: bool = False

    @classmethod
    def from_env_and_config(cls, config) -> "CommunityConfig":
        """Load community config from environment variables and config.ini."""
        config_debug_no_send = False
        config_mode_queue = False
        if config.has_section("Community"):
            config_debug_no_send = config.getboolean(
                "Community",
                "coordination_debug_no_send",
                fallback=False,
            )
            config_mode_queue = config.getboolean(
                "Community",
                "coordination_mode_queue",
                fallback=False,
            )

        return cls(
            mesh_region=os.environ.get(
                "MESH_REGION",
                config.get("Community", "mesh_region", fallback=""),
            ),
            coordination_debug_no_send=_env_bool(
                "COMMUNITY_COORDINATION_DEBUG_NO_SEND",
                config_debug_no_send,
            ),
            coordination_mode_queue=_env_bool(
                "COMMUNITY_COORDINATION_MODE_QUEUE",
                config_mode_queue,
            ),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


