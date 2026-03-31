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

    Recursively processes nested dicts and lists, masking values whose
    parent key matches any entry in SENSITIVE_KEYS.

    For collections (lists/dicts) under a sensitive key, the entire
    collection is masked rather than exposing nested structure.
    """

    def _mask_str(v: Any) -> Any:
        if isinstance(v, str) and len(v) > 4:
            return v[:2] + "***" + v[-2:]
        return "***"

    def _is_sensitive_key(k: str) -> bool:
        lower = k.lower()
        return any(s in lower for s in SENSITIVE_KEYS)

    def _process_value(k: str, v: Any) -> Any:
        if _is_sensitive_key(k):
            # Entire value under a sensitive key is masked, including
            # nested dicts/lists, so no internal fields are exposed.
            return _mask_str(v)
        if isinstance(v, dict):
            return _process(v)
        if isinstance(v, list):
            return _process_list(v)
        return v

    def _process(d: dict) -> dict:
        return {k: _process_value(k, v) for k, v in d.items()}

    def _process_list(lst: list) -> list:
        return [
            _process(item)
            if isinstance(item, dict)
            else _process_list(item)
            if isinstance(item, list)
            else item
            for item in lst
        ]

    if not isinstance(config, dict):
        return config

    return _process(config)
