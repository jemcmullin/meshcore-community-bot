"""Runtime patch for web viewer integration.

Redirects web viewer subprocess launch to community wrapper app so we can
add community-specific pages/routes without modifying the meshcore-bot submodule.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import MethodType



def patch_web_viewer_integration(bot) -> None:
    """Patch web viewer launcher to use community wrapper script."""
    wv = getattr(bot, "web_viewer_integration", None)
    if not wv:
        bot.logger.info("Community web viewer patch skipped (web viewer integration unavailable)")
        return

    if getattr(wv, "_community_wrapper_enabled", False):
        return

    def _run_viewer_with_community_wrapper(self):
        """Run the web viewer using community wrapper entrypoint."""
        stdout_file = None
        stderr_file = None

        try:
            wrapper_script = Path(__file__).with_name("web_viewer_community_app.py")
            config_path = getattr(self.bot, "config_file", "config.ini")
            config_path = str(Path(config_path).resolve()) if config_path else "config.ini"

            cmd = [
                sys.executable,
                str(wrapper_script),
                "--config",
                config_path,
                "--host",
                self.host,
                "--port",
                str(self.port),
            ]
            if self.debug:
                cmd.append("--debug")

            os.makedirs("logs", exist_ok=True)
            stdout_file = open("logs/web_viewer_stdout.log", "w")
            stderr_file = open("logs/web_viewer_stderr.log", "w")

            self._viewer_stdout_file = stdout_file
            self._viewer_stderr_file = stderr_file

            self.viewer_process = subprocess.Popen(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )

            time.sleep(2)
            if self.viewer_process and self.viewer_process.poll() is not None:
                self.logger.error(
                    "Community web viewer wrapper failed to start (code=%s)",
                    self.viewer_process.returncode,
                )
                self.viewer_process = None
                self._viewer_stdout_file = None
                self._viewer_stderr_file = None
                return

            self.logger.info("Community web viewer wrapper active")

            while self.running and self.viewer_process and self.viewer_process.poll() is None:
                time.sleep(1)

            if self.viewer_process and self.viewer_process.returncode not in (None, 0):
                self.logger.error(
                    "Community web viewer process exited with code %s",
                    self.viewer_process.returncode,
                )
            elif self.viewer_process and self.viewer_process.returncode == 0:
                self.logger.info("Community web viewer process exited normally")

        except Exception as e:
            self.logger.error("Error running community web viewer wrapper: %s", e)
        finally:
            try:
                if stdout_file:
                    stdout_file.close()
            except Exception:
                pass
            try:
                if stderr_file:
                    stderr_file.close()
            except Exception:
                pass
            self.running = False

    wv._run_viewer = MethodType(_run_viewer_with_community_wrapper, wv)
    wv._community_wrapper_enabled = True
    bot.logger.info("Community web viewer wrapper patch installed")
