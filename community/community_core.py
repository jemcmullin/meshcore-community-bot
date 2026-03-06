"""Extended MeshCoreBot with coordinator integration.

Inherits from MeshCoreBot and adds:
- Coordinator registration and heartbeat
- Message coordination (who should respond)
- Packet/message reporting to central service
- Community-specific commands (coverage, botstatus)
"""

import asyncio
import importlib
import importlib.util
import inspect
import logging
import sys
import time
from pathlib import Path

# Add meshcore-bot submodule to path (once, before any meshcore-bot imports)
_bot_path = str(Path(__file__).parent.parent / "meshcore-bot")
if _bot_path not in sys.path:
    sys.path.insert(0, _bot_path)

from modules.commands.base_command import BaseCommand
from modules.core import MeshCoreBot

from .config import CoordinatorConfig, ScoringConfig
from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback
from .message_interceptor import MessageInterceptor
from .network_observer import NetworkObserver
from .packet_reporter import PacketReporter

logger = logging.getLogger("CommunityBot")


class CommunityBot(MeshCoreBot):
    """MeshCoreBot extended with multi-bot coordination."""

    def __init__(self, config_file: str = "config.ini"):
        # Initialize the base bot
        super().__init__(config_file)

        # Apply the same colored formatter to all community.* loggers
        self._setup_community_logging()

        # Metrics counters (must be set before MessageInterceptor is created)
        self.messages_processed_count: int = 0
        self.messages_responded_count: int = 0

        # Load coordinator config
        self.coordinator_config = CoordinatorConfig.from_env_and_config(self.config)

        # Load scoring config
        self.scoring_config = ScoringConfig.from_env_and_config(self.config)

        # Initialize coordinator client
        self.coordinator = CoordinatorClient(
            base_url=self.coordinator_config.url,
            timeout_ms=self.coordinator_config.coordination_timeout_ms,
            data_dir=str(self.bot_root / "data"),
            registration_key=self.coordinator_config.registration_key,
        )

        # Initialize fallback with scoring config
        self.coverage_fallback = CoverageFallback(scoring_config=self.scoring_config)

        # Initialize network observer
        self.network_observer = NetworkObserver(self.db_manager)

        # Initialize packet reporter
        self.packet_reporter = PacketReporter(
            coordinator=self.coordinator,
            batch_interval=self.coordinator_config.batch_interval_seconds,
            batch_max_size=self.coordinator_config.batch_max_size,
        )

        # Install message interceptor (patches send_response + process_message)
        self.message_interceptor = MessageInterceptor(
            bot=self,
            coordinator=self.coordinator,
            fallback=self.coverage_fallback,
            reporter=self.packet_reporter,
            network_observer=self.network_observer,
        )

        # Load community-specific commands
        self._load_community_commands()

        # Background tasks
        self._coordinator_tasks: list[asyncio.Task] = []
        self._registered_with_real_key = False

        self.logger.info("Community bot initialized with coordinator support")

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
                            self.logger.info(f"Loaded community command: {cmd_name}")
                        break
            except Exception as e:
                self.logger.warning(f"Failed to load community command {py_file.name}: {e}")

    async def start(self):
        """Start the bot with coordinator integration."""
        self.logger.info("Starting Community Bot...")

        # Start coordinator background tasks (heartbeat will handle registration)
        self._start_coordinator_tasks()

        # Start the base bot (connects to radio, starts event loop)
        # Registration happens in _heartbeat_loop after radio connects
        await super().start()

    async def stop(self):
        """Stop the bot and cleanup coordinator resources."""
        # Cancel coordinator tasks
        for task in self._coordinator_tasks:
            task.cancel()
        self._coordinator_tasks.clear()

        # Restore original send_response
        if hasattr(self, "message_interceptor"):
            self.message_interceptor.restore()

        # Close coordinator client
        if hasattr(self, "coordinator"):
            await self.coordinator.close()

        # Stop base bot
        await super().stop()

    async def _register_with_coordinator(self) -> bool:
        """Register this bot with the coordinator using the real radio public key.

        Returns True if registration succeeded.
        """
        if not self.coordinator.is_configured:
            return False

        # Try to get the real radio public key
        public_key = ""
        if self.meshcore and hasattr(self.meshcore, "self_info"):
            try:
                info = self.meshcore.self_info
                if info and isinstance(info, dict):
                    public_key = info.get("public_key", "") or ""
                elif info and hasattr(info, "public_key"):
                    public_key = info.public_key or ""
            except Exception:
                pass

        if not public_key:
            # Radio not connected yet — can't register with real key
            return False

        bot_name = self.config.get("Bot", "bot_name", fallback="CommunityBot")
        lat = self.config.getfloat("Bot", "bot_latitude", fallback=None)
        lon = self.config.getfloat("Bot", "bot_longitude", fallback=None)
        conn_type = self.config.get("Connection", "connection_type", fallback="serial")

        # Get loaded command names
        capabilities = list(self.command_manager.commands.keys())

        success = await self.coordinator.register(
            bot_name=bot_name,
            public_key=public_key,
            latitude=lat,
            longitude=lon,
            connection_type=conn_type,
            capabilities=capabilities,
            version="0.1.0",
            mesh_region=self.coordinator_config.mesh_region,
        )

        if success:
            self._registered_with_real_key = True
            self.logger.info(
                f"Registered with coordinator as {bot_name} "
                f"(bot_id={self.coordinator.bot_id}, pubkey={public_key[:12]}...)"
            )
        return success

    def _start_coordinator_tasks(self):
        """Start background tasks for coordinator communication."""
        if not self.coordinator.is_configured:
            return

        # Heartbeat loop (also handles registration)
        task = asyncio.create_task(self._heartbeat_loop())
        self._coordinator_tasks.append(task)

        # Packet reporter loop
        task = asyncio.create_task(self.packet_reporter.run())
        self._coordinator_tasks.append(task)

        self.logger.info("Coordinator background tasks started")

    async def _heartbeat_loop(self):
        """Send periodic heartbeats to the coordinator.

        Also handles registration — waits for the radio to connect,
        then registers with the real public key.
        """
        while True:
            try:
                # If not yet registered with real key, try each heartbeat cycle
                if not self._registered_with_real_key:
                    if self.connected and self.meshcore:
                        success = await self._register_with_coordinator()
                        if not success:
                            self.logger.debug("Waiting for radio to provide public key...")
                    # Don't send heartbeats until registered
                    await asyncio.sleep(5)
                    continue

                uptime = int(time.time() - self.start_time)
                contact_count = 0
                channel_count = 0

                if self.meshcore:
                    if hasattr(self.meshcore, "contacts") and self.meshcore.contacts:
                        contact_count = len(self.meshcore.contacts)

                success = await self.coordinator.heartbeat(
                    uptime_seconds=uptime,
                    connected=self.connected,
                    contact_count=contact_count,
                    channel_count=channel_count,
                )

                if success:
                    # Update fallback score
                    self.coverage_fallback.update_score(self.coordinator.current_score)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.debug(f"Heartbeat error: {e}")

            await asyncio.sleep(self.coordinator.heartbeat_interval)
