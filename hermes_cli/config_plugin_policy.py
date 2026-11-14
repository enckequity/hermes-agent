"""Administrator-owned optional plugin choices, shared by config and PM admission."""
from __future__ import annotations

import os

from hermes_cli import managed_scope


def _names(value):
    return list(dict.fromkeys(name for name in value if isinstance(name, str) and name)) if isinstance(value, list) else []


def _matches(name, names):
    return name in names or name.rsplit("/", 1)[-1] in names


def optional_policy(managed=None):
    """Only the managed file may grant optional choices; absent means legacy semantics."""
    # Remote config never honors this machine's managed file. PM's isolated runtime
    # reads its local dependency selection without importing the remote backend.
    if managed is None and os.environ.get("HERMES_CONFIG_BACKEND", "").strip().lower() == "remote":
        return None
    managed = managed_scope.load_managed_config() if managed is None else managed
    plugins = managed.get("plugins") or {}
    if not isinstance(plugins, dict) or "optional" not in plugins:
        return None
    required, denied = _names(plugins.get("enabled")), _names(plugins.get("disabled"))
    allowance = plugins["optional"]
    allowance = allowance if isinstance(allowance, list) and all(isinstance(n, str) and n for n in allowance) else []
    optional = [name for name in _names(allowance)
                if not _matches(name, required) and not _matches(name, denied)
                and not any(_matches(pinned, [name]) for pinned in required + denied)]
    return required, denied, optional


def plugin_selection(config, managed=None):
    policy = optional_policy(managed)
    if policy is None:
        return None
    required, denied, optional = policy
    plugins = (config or {}).get("plugins") or {}
    plugins = plugins if isinstance(plugins, dict) else {}
    return {
        "enabled": list(dict.fromkeys(required + [n for n in _names(plugins.get("enabled")) if n in optional])),
        "disabled": list(dict.fromkeys(denied + [n for n in _names(plugins.get("disabled")) if n in optional])),
    }


def apply_plugin_selection(config, managed=None):
    selected = plugin_selection(config, managed)
    if selected is None:
        return config
    plugins = (config or {}).get("plugins") or {}
    return {**(config or {}), "plugins": {**(plugins if isinstance(plugins, dict) else {}), **selected}}


def user_plugin_selection(config, managed=None, *, previous=None):
    policy = optional_policy(managed)
    if policy is None:
        return None
    optional = policy[2]
    plugins = (config or {}).get("plugins") or {}
    old = (previous or {}).get("plugins") or {}
    return {key: list(dict.fromkeys([n for n in _names(old.get(key)) if n not in optional]
                                   + [n for n in _names(plugins.get(key)) if n in optional]))
            for key in ("enabled", "disabled")}


def validate_plugin_selection(enabled, disabled):
    policy = optional_policy()
    if policy is None:
        return
    required, denied, optional = map(set, policy)
    if (not required <= set(enabled) or not set(enabled) <= required | optional
            or not denied <= set(disabled) or not set(disabled) <= denied | optional):
        raise ValueError("Plugin selection conflicts with administrator-managed required, disabled or optional plugins")
