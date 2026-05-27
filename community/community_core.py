"""Extended MeshCoreBot with firmware-native coordination.

Inherits from MeshCoreBot and adds:
- Firmware-native decentralized response coordination
- Community-specific commands (botstatus, botreps)
"""

import asyncio
import importlib
import importlib.util
import inspect
import logging
import sys
from pathlib import Path

# Add meshcore-bot submodule to path (once, before any meshcore-bot imports)
_bot_path = str(Path(__file__).parent.parent / "meshcore-bot")
if _bot_path not in sys.path:
    sys.path.insert(0, _bot_path)

from community.web_viewer_patch import patch_web_viewer_integration
from modules.commands.base_command import BaseCommand
from modules.core import MeshCoreBot

from .config import CommunityConfig
from .firmware_coordinator import FirmwareCoordinator
from .message_interceptor import MessageInterceptor

logger = logging.getLogger('CommunityBot')


class CommunityBot(MeshCoreBot):
    """MeshCoreBot extended with firmware-native multi-bot coordination."""

    def __init__(self, config_file: str = "config.ini"):
        super().__init__(config_file)

        # Mirror MeshCoreBot log handlers onto the CommunityBot logger
        self._setup_community_logging()

        # Route web viewer subprocess through community wrapper
        patch_web_viewer_integration(self)

        # Load community config (mesh_region only)
        self.community_config = CommunityConfig.from_env_and_config(self.config)
        logger.info("Community config loaded (mesh_region=%s)", self.community_config.mesh_region)

        # Firmware coordinator — identity seed set after radio connects
        self.firmware_coordinator = FirmwareCoordinator()

        # Install message interceptor (patches four methods on meshcore-bot)
        self.message_interceptor = MessageInterceptor(
            bot=self,
            firmware_coordinator=self.firmware_coordinator,
        )

        # Load community-specific commands
        self._load_community_commands()

        logger.info("Community bot initialized (firmware-native coordination)")
    
    def _setup_community_logging(self):
        """Mirror all MeshCoreBot handlers onto the CommunityBot logger.

        Copies every handler (console + file) so community log lines appear
        in the same destinations — including the log file — as MeshCoreBot lines.
        """
        import colorlog

        meshcore_logger = logging.getLogger("MeshCoreBot")
        community_logger = logging.getLogger("CommunityBot")
        community_logger.setLevel(meshcore_logger.level or logging.DEBUG)
        community_logger.propagate = False

        # Remove stale handlers from previous calls (e.g. hot-reload)
        community_logger.handlers.clear()

        if meshcore_logger.handlers:
            # Reuse the exact same handler instances — they already have the
            # right formatter and file path configured by MeshCoreBot.setup_logging()
            for handler in meshcore_logger.handlers:
                community_logger.addHandler(handler)
        else:
            # Fallback: MeshCoreBot not yet configured, add a plain colored console handler
            formatter = colorlog.ColoredFormatter(
                "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "red,bg_white",
                },
            )
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            community_logger.addHandler(handler)

    def _load_community_commands(self):
        """Load community-specific commands into the plugin system.

        The base PluginLoader only scans meshcore-bot/modules/commands/.
        We manually load commands from community/commands/ and register them.
        """
        commands_dir = Path(__file__).parent / "commands"
        if not commands_dir.exists():
            return

        for py_file in commands_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            module_name = f"community.commands.{py_file.stem}"
            try:
                # Import the module
                if module_name in sys.modules:
                    mod = sys.modules[module_name]
                else:
                    mod = importlib.import_module(module_name)

                # Find BaseCommand subclass
                for name, obj in inspect.getmembers(mod, inspect.isclass):
                    if (issubclass(obj, BaseCommand) and
                            obj is not BaseCommand and
                            obj.__module__ == module_name):
                        instance = obj(self)
                        cmd_name = instance.name
                        if cmd_name:
                            # Register with command manager
                            self.command_manager.commands[cmd_name] = instance
                            # Register keywords with plugin loader
                            if hasattr(self, 'plugin_loader') and self.plugin_loader:
                                self.plugin_loader.loaded_plugins[cmd_name] = instance
                                metadata = instance.get_metadata()
                                self.plugin_loader.plugin_metadata[cmd_name] = metadata
                                for kw in metadata.get('keywords', []):
                                    self.plugin_loader.keyword_mappings[kw.lower()] = cmd_name
                            logger.info(f"Loaded community command: {cmd_name}")
                        break
            except Exception as e:
                logger.warning(f"Failed to load community command {py_file.name}: {e}")

    async def start(self):
        """Start the community bot."""
        logger.info("Starting Community Bot...")
        await super().start()

    async def connect(self):
        """Connect to radio and seed the firmware coordinator identity."""
        await super().connect()
        self._seed_firmware_coordinator()

    def _seed_firmware_coordinator(self):
        """Set coordinator identity seed from radio public key after connect."""
        try:
            info = None
            if self.meshcore and hasattr(self.meshcore, "self_info"):
                info = self.meshcore.self_info
            if info:
                if isinstance(info, dict):
                    pk = info.get("public_key", "") or ""
                else:
                    pk = getattr(info, "public_key", "") or ""
                if pk:
                    self.firmware_coordinator.set_identity_seed(pk)
                    return
        except Exception as e:
            logger.debug("Could not read public key for identity seed: %s", e)
        logger.warning("Firmware coordinator identity seed not set (radio public key unavailable)")

    async def stop(self):
        """Stop the bot and cleanup community resources."""
        if hasattr(self, "message_interceptor"):
            self.message_interceptor.restore()

        from .discord_webhook import close as discord_close
        await discord_close()

        await super().stop()
