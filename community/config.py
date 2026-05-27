"""Community bot configuration handling."""

import os
from dataclasses import dataclass


@dataclass
class CommunityConfig:
    """Configuration for the community bot layer."""

    mesh_region: str = ""

    @classmethod
    def from_env_and_config(cls, config) -> "CommunityConfig":
        """Load community config from environment variables and config.ini."""
        return cls(
            mesh_region=os.environ.get(
                "MESH_REGION",
                config.get("Community", "mesh_region", fallback=""),
            ),
        )


