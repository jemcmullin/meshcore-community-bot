#!/usr/bin/env python3
"""Community wrapper that forces the `path` command responses to lowercase.

This replaces the registered `path` command with a wrapper that delegates
to the original `PathCommand` but lowercases all outgoing responses.
"""

import types
from typing import Any

from modules.commands.base_command import BaseCommand
from modules.commands.path_command import PathCommand


class PathLowercaseCommand(BaseCommand):
    """Wrapper around PathCommand that lowercases replies."""

    name = "path"
    keywords = ["path", "p"]
    description = "Lowercase wrapper for the path command"

    SEND_2BYTE_TIP = True

    def __init__(self, bot: Any) -> None:
        super().__init__(bot)
        # Instantiate the original PathCommand to reuse its logic
        self._wrapped = PathCommand(bot)

    def matches_keyword(self, message) -> bool:
        # Delegate to wrapped command keyword matching
        return self._wrapped.matches_keyword(message)

    async def execute(self, message) -> bool:
        # Monkey-patch the wrapped command's send methods to lowercase content
        orig_send = getattr(self._wrapped, 'send_response')
        orig_send_chunked = getattr(self._wrapped, 'send_response_chunked')

        async def _lower_send(self_wrapped, msg, content, *args, **kwargs):
            # Ensure content is a string, strip leading "prefix: " from each line, then lowercase
            try:
                if isinstance(content, str):
                    import re

                    def strip_prefix(line: str) -> str:
                        return re.sub(r'^[^:\n]+:\s*', '', line)

                    lines = content.splitlines(keepends=True)
                    stripped = ''.join(strip_prefix(l) for l in lines)
                    # lower = stripped.lower()  # commented out for testing
                    lower = stripped
                else:
                    lower = content
            except Exception:
                lower = content
            return await self_wrapped.bot.command_manager.send_response(msg, lower, *args, **kwargs)

        async def _lower_send_chunked(self_wrapped, msg, chunks, *args, **kwargs):
            try:
                import re

                def strip_prefix(line: str) -> str:
                    return re.sub(r'^[^:\n]+:\s*', '', line)

                lower_chunks = []
                for c in chunks:
                    if isinstance(c, str):
                        lines = c.splitlines(keepends=True)
                        stripped = ''.join(strip_prefix(l) for l in lines)
                        # lower_chunks.append(stripped.lower())  # commented out for testing
                        lower_chunks.append(stripped)
                    else:
                        lower_chunks.append(c)
            except Exception:
                lower_chunks = chunks
            return await self_wrapped.bot.command_manager.send_response_chunked(msg, lower_chunks, *args, **kwargs)

        # Bind the functions to the wrapped instance
        self._wrapped.send_response = types.MethodType(_lower_send, self._wrapped)
        self._wrapped.send_response_chunked = types.MethodType(_lower_send_chunked, self._wrapped)

        try:
            result = await self._wrapped.execute(message)
            
            # After path command completes, check if path was 1-byte and send upgrade message
            if self.SEND_2BYTE_TIP:
                await self._check_and_suggest_2byte_upgrade(message)
            
            return result
        finally:
            # Restore original methods
            try:
                self._wrapped.send_response = orig_send
            except Exception:
                pass
            try:
                self._wrapped.send_response_chunked = orig_send_chunked
            except Exception:
                pass

    def _is_1byte_path(self, path_input: str) -> bool:
        """Check if path input uses 1-byte (2-char hex) encoding.
        
        Returns True if path contains comma-separated 2-char hex values.
        """
        import re
        
        # Extract path data from input
        path_input = re.sub(r'\s*\([^)]*hops?[^)]*\)', '', path_input, flags=re.IGNORECASE)
        path_input = path_input.strip()
        
        if ',' in path_input:
            tokens = [t.strip() for t in path_input.split(',') if t.strip()]
            if tokens:
                lengths = {len(t) for t in tokens}
                # Check if all tokens are 2-char hex (1-byte encoding)
                if len(lengths) == 1 and 2 in lengths:
                    valid_hex = all(
                        all(c in '0123456789aAbBcCdDeEfF' for c in t)
                        for t in tokens
                    )
                    return valid_hex
        return False

    async def _check_and_suggest_2byte_upgrade(self, message) -> None:
        """Send upgrade message if path was 1-byte encoded."""
        try:
            content = message.content.strip()
            parts = content.split()
            
            if len(parts) >= 2:
                path_input = " ".join(parts[1:])
                
                if self._is_1byte_path(path_input):
                    # Send follow-up message suggesting 2-byte upgrade
                    upgrade_msg = (
                        "ColoradoMesh recommends: Settings > Path Hash 2-bytes"
                    )
                    if hasattr(self.bot, 'message_interceptor'):
                        await self.bot.command_manager.send_response(
                            message,
                            upgrade_msg,
                            bypass_coordination=True,
                        )
                    else:
                        await self.send_response(message, upgrade_msg)
        except Exception as e:
            self.logger.debug(f"Error checking for 1-byte path upgrade: {e}")
