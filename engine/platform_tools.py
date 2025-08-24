"""
Script:         platform_tools.py
Author:         DevOps Team

Description:
    Core module providing a unified API for various platform-related operations, including:
    (Small subset of AutoForge platform tools and toolbox classes)(
"""

import os
import re
import shutil
import string
from typing import Optional, Tuple

# MCP Service imports
from logger import CoreMCPLogger

# Third-party

AUTO_FORGE_MODULE_NAME = "Platform"
AUTO_FORGE_MODULE_DESCRIPTION = "Platform Services"


class CorePlatform:
    """
    a Core class that serves as a platform / shell related operation swissknife.
    """

    def __init__(self, workspace_path: str):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._workspace_path: Optional[str] = workspace_path
        self._subprocess_execution_timeout: int = 30
        self._pre_compiled_escape_patterns = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
        # Set an optional list of keywords which, when found in command output, will be colorized using ANSI colors
        self._build_colorize_keywords: list = []

        self._logger = CoreMCPLogger("Platform")

    @staticmethod
    def _variables_expand(key: str) -> Optional[str]:
        """
        Expand environment variables and user home directory (~) in the given path.
        Args:
            key: Path or string to expand.
        Returns:
            Expanded string if expansion produced a result, else None.
        """
        if key is None:
            return None

        # Normalize to string
        raw = str(key)

        # Perform expansion
        expanded = os.path.expanduser(os.path.expandvars(raw))

        # If expansion didn't change anything and original path looks "bare",
        # treat it as not expanded.
        if expanded == raw and ("$" in raw or "~" in raw):
            return None

        return expanded

    @staticmethod
    def normalize_text(text: Optional[str], allow_empty: bool = False) -> str:
        """
        Normalize the input string by stripping leading and trailing whitespace.
        Args:
            text (Optional[str]): The string to be normalized.
            allow_empty (Optional[bool]): No exception to the output is an empty string

        Returns:
            str: A normalized string with no leading or trailing whitespace.
        """
        # Check for None or empty string after potential stripping
        if text is None or not isinstance(text, str):
            raise ValueError("input must be a non-empty string.")

        # Strip whitespace
        normalized_string = text.strip()
        if not allow_empty and not normalized_string:
            raise ValueError("input string cannot be empty after stripping")

        return normalized_string

    @staticmethod
    def find_pattern_in_line(line: str, patterns: list[str]) -> Optional[Tuple[str, int]]:
        """
        Searches for the first occurrence of any pattern (case-insensitive) in the given line.
        Args:
            line (str): The text line to search.
            patterns (list[str]): List of patterns to search for.

        Returns:
            Optional[Tuple[str, int]]: A tuple of (matched_pattern_original_case, position_in_line),
                                       or None if no pattern is found.
        """
        line_lower = line.lower()
        for pattern in patterns:
            idx = line_lower.find(pattern.lower())
            if idx != -1:
                # Return the pattern from the original line based on its position
                return line[idx:idx + len(pattern)], idx
        return None

    # noinspection SpellCheckingInspection
    def truncate_for_terminal(self, text: Optional[str], reduce_by_chars: int = 0,
                              fallback_width: int = 120) -> Optional[str]:
        """
        Truncates a string to fit within the terminal width, adding "..." if truncated.
        Handles truncation on a line-by-line basis, preserving original newlines or lack thereof, and attempts to
        correctly handle ANSI escape codes by calculating visible width and preserving codes at the end of lines.
        Args:
            text: The string to truncate.
            reduce_by_chars: An optional number of characters to reduce the effective
                             terminal width by (e.g., for padding or other elements).
            fallback_width: The width to use if the terminal size cannot be determined.
                            Defaults to 120.
        Returns:
            The truncated string.
        """

        def _get_visible_width(_text: Optional[str]) -> int:
            """
            Calculates the visible width of a string by removing ANSI escape codes.
            This assumes escape codes don't affect character width (e.g., no double-width chars).
            """
            return len(self._pre_compiled_escape_patterns.sub('', text))

        if not isinstance(text, str):
            return text

        # Calculate width
        terminal_size = shutil.get_terminal_size(fallback=(fallback_width, 24))
        terminal_width = terminal_size.columns

        # Calculate the effective width available for the text
        effective_width = terminal_width - reduce_by_chars

        # Account for the "..." that will be added if truncation occurs
        dots_length = 3
        dots = "." * dots_length

        # Pattern to extract the trailing newline sequence (including \r\n, \n, \r)
        newline_pattern = re.compile(r'(\r?\n|\r)$')

        truncated_segments = []
        # splitlines(keepends=True) correctly separates lines and keeps their specific endings
        segments = text.splitlines(keepends=True)

        # noinspection GrazieInspection
        for segment in segments:
            # Separate actual content from its potential trailing newline
            line_content_with_codes = segment
            line_ending = ""
            match = newline_pattern.search(segment)
            if match:
                line_ending = match.group(0)
                line_content_with_codes = segment[:-len(line_ending)]

            # Extract trailing escape codes (like \x1b[K) that should be preserved
            # This is tricky: we want to preserve codes that clear the line AFTER the content.
            # We assume these codes are at the very end of the *content* part.
            trailing_codes = ""
            content_without_trailing_codes = line_content_with_codes

            # Find all escape sequences in the content part
            all_codes_in_content = list(self._pre_compiled_escape_patterns.finditer(line_content_with_codes))

            if all_codes_in_content:
                # Check if the last found code is at the very end of the content
                last_match = all_codes_in_content[-1]
                if last_match.end() == len(line_content_with_codes):
                    trailing_codes = last_match.group(0)
                    content_without_trailing_codes = line_content_with_codes[:last_match.start()]
                # else: The last code is not at the very end, so we treat it as part of the content
                # that might be truncated. This is a simplification; a full solution might
                # need to render and measure, or parse more deeply.

            # Calculate visible width of the content *without* trailing codes
            visible_width = _get_visible_width(content_without_trailing_codes)

            # Perform truncation based on visible width
            if visible_width > effective_width:
                # Determine target visible length for the actual text part
                target_visible_length = effective_width - dots_length

                if target_visible_length < 0:  # Not even enough space for dots
                    # Fill with as many dots as possible, preserving trailing codes and ending
                    truncated_segment_text = "." * effective_width
                else:
                    current_visible_length = 0
                    truncated_text_chars = []
                    # Iterate through the characters of the string (excluding trailing codes)
                    # and build up the truncated string while tracking visible width.
                    idx = 0
                    while idx < len(
                            content_without_trailing_codes) and current_visible_length < target_visible_length:
                        char = content_without_trailing_codes[idx]
                        if char == '\x1b' and self._pre_compiled_escape_patterns.match(
                                content_without_trailing_codes,
                                idx):
                            # It's the start of an escape sequence, find its end
                            match: re.Match = self._pre_compiled_escape_patterns.match(
                                content_without_trailing_codes,
                                idx)
                            if match:
                                # Add the full escape sequence without counting it towards visible width
                                truncated_text_chars.append(match.group(0))
                                idx = match.end()
                                continue

                        # Regular character, count it
                        truncated_text_chars.append(char)
                        current_visible_length += 1
                        idx += 1

                    truncated_segment_text = "".join(truncated_text_chars) + dots

                # Combine truncated text with preserved trailing codes and line ending
                truncated_segments.append(truncated_segment_text + trailing_codes + line_ending)
            else:
                # No truncation needed for this segment's visible content.
                # Keep the segment as is (including its original codes and ending).
                truncated_segments.append(segment)

        return "".join(truncated_segments)

    @staticmethod
    def strip_ansi(text: Optional[str], bare_text: bool = False) -> Optional[str]:
        """
        Removes ANSI escape sequences and broken hyperlink wrappers,
        but retains useful text such as GCC warning flags.
        Args:
            text (str): The input string possibly containing ANSI and broken links.
            bare_text (bool): If True, reduce to printable ASCII only.
        Returns:
            str: Cleaned text, preserving meaningful info like [-W...]
        """

        if not isinstance(text, str):
            return text

        # Strip and see if we got anything to process
        text = text.strip()
        if not text:
            return text

        # Strip ANSI escape sequences (CSI, OSC, etc.)
        ansi_escape = re.compile(r'''
            \x1B
            (?:
                [@-Z\\-_] |
                \[ [0-?]* [ -/]* [@-~]
            )
        ''', re.VERBOSE)
        text = ansi_escape.sub('', text)
        if not text:
            return text

        def _recover_warning_flag(match):
            """ # Extract and preserve [-W...warning...] from broken [https://...] blocks """
            url = match.group(1)
            warning_match = re.search(r'(-W[\w\-]+)', url)
            return f"[{warning_match.group(1)}]" if warning_match else ""

        text = re.sub(r'\[(https?://[^]]+)]', _recover_warning_flag, text).strip()

        # Optionally reduce to printable ASCII
        if bare_text:
            allowed = set(string.ascii_letters + string.digits + string.punctuation + ' \t\n')
            text = ''.join(c for c in text if c in allowed)

        return text.strip()
