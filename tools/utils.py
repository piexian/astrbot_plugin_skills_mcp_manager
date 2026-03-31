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
    whose parent key matches any entry in SENSITIVE_KEYS.
    """

    def _mask_str(v: Any) -> Any:
        if isinstance(v, str) and len(v) > 4:
            return v[:2] + "***" + v[-2:]
        return "***"

    def _process_value(k: str, v: Any) -> Any:
        if isinstance(v, dict):
            return _process(v)
        if isinstance(v, list):
            return _process_list(v, k)
        if any(s in k.lower() for s in SENSITIVE_KEYS):
            return _mask_str(v)
        return v

    def _process(d: dict) -> dict:
        return {k: _process_value(k, v) for k, v in d.items()}

    def _process_list(lst: list, parent_key: str = "") -> list:
        is_sensitive = any(s in parent_key.lower() for s in SENSITIVE_KEYS)
        return [
            _process(item)
            if isinstance(item, dict)
            else _process_list(item, parent_key)
            if isinstance(item, list)
            else _mask_str(item)
            if is_sensitive
            else item
            for item in lst
        ]

    return _process(config)
