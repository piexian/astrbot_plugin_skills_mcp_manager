"""Shared utilities for Skills & MCP management tools."""

from __future__ import annotations

from typing import Any

# Sensitive config keys whose values should be masked in output
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "private_key",
        "access_token",
        "refresh_token",
    }
)

# Maximum length for diff target_content to prevent performance issues
MAX_DIFF_TARGET_LEN = 50000


def mask_sensitive(config: dict) -> dict:
    """Mask sensitive values in config dict for safe display.

    Recursively processes nested dicts and lists, masking string values
    whose parent key (or any ancestor key) matches any entry in SENSITIVE_KEYS.
    A ``parent_sensitive`` flag is threaded through the recursion so that all
    string values under a sensitive ancestor are masked, even when nested
    inside dicts or lists.
    """

    def _mask_str(v: Any) -> Any:
        if isinstance(v, str) and len(v) > 4:
            return v[:2] + "***" + v[-2:]
        return "***"

    def _is_sensitive_key(k: str) -> bool:
        lower = k.lower()
        return any(s in lower for s in SENSITIVE_KEYS)

    def _process_value(k: str, v: Any, parent_sensitive: bool = False) -> Any:
        sensitive = parent_sensitive or _is_sensitive_key(k)
        if isinstance(v, dict):
            return _process(v, sensitive)
        if isinstance(v, list):
            return _process_list(v, sensitive)
        if sensitive:
            return _mask_str(v)
        return v

    def _process(d: dict, parent_sensitive: bool = False) -> dict:
        return {k: _process_value(k, v, parent_sensitive) for k, v in d.items()}

    def _process_list(lst: list, parent_sensitive: bool = False) -> list:
        return [
            _process(item, parent_sensitive)
            if isinstance(item, dict)
            else _process_list(item, parent_sensitive)
            if isinstance(item, list)
            else _mask_str(item)
            if parent_sensitive
            else item
            for item in lst
        ]

    return _process(config)
