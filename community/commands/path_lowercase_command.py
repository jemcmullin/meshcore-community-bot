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
            return await self._wrapped.execute(message)
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
