"""Config-only PM admission for administrator-allowed dependency-free bundled plugins."""
from __future__ import annotations

import threading
from pathlib import Path

_LOCK_HOLDER = threading.local()


def publish_optional_selection(selection: dict) -> bool:
    """Return False for legacy selections; managed optional choices never change dependencies."""
    from hermes_cli.config_plugin_policy import optional_policy
    policy = optional_policy()
    if policy is None:
        return False
    from pm import receipt
    token = receipt.begin("sync")
    outcome = "failed"
    try:
        _publish(selection, policy)
        receipt.record_venv_rebuild(False, "dependency-free managed optional plugin selection")
        outcome = "ok"
        return True
    except BaseException as exc:
        receipt.record_step("dependency-sync", False, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        receipt.finalize(outcome, 0 if outcome == "ok" else 1, token=token)


def _publish(selection, policy):
    from hermes_cli.config_plugin_policy import optional_policy, user_plugin_selection, validate_plugin_selection
    from hermes_constants import get_hermes_home
    from hermes_cli.auth import _file_lock
    from hermes_cli.config import atomic_config_replace
    from hermes_cli.config_backend import read_config_doc
    from hermes_cli.plugins_cmd import _find_plugin_entry
    from pm.filesystem import file_digest
    from pm.plugin_declarations import read_python_declaration
    from pm.plugin_eviction import settle_record
    from pm.plugins_state import read_home_selection
    from pm.publication import validate_manifest

    home = Path(selection["home"]).resolve()
    if home != get_hermes_home().resolve() or selection.get("extra_dirs"):
        raise ValueError("Managed optional selection must target only the active home and installed bundled plugins")
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.yaml"
    with _file_lock(home / ".optional-plugins.lock", _LOCK_HOLDER, 15.0, "Optional plugin configuration is busy"):
        expected = selection.get("expected_config")
        before = file_digest(path) or "missing"
        if expected is not None and expected != before:
            raise ValueError("Plugin configuration changed since this selection was read; retry")
        validate_plugin_selection(selection["enabled"], selection["disabled"])
        current = (read_home_selection(home) or {}).get("plugins") or {}
        changed = (set(current.get("enabled") or ()) ^ set(selection["enabled"])) | (
            set(current.get("disabled") or ()) ^ set(selection["disabled"]))
        if not changed <= set(policy[2]):
            raise ValueError("Only administrator-allowed optional plugins may change")
        for name in changed:
            entry = _find_plugin_entry(name)
            if entry is None or entry[3] != "bundled" or entry[5] != name:
                raise ValueError(f"Optional plugin '{name}' must resolve to its installed bundled source")
            source = Path(entry[4])
            validate_manifest(source)
            declaration = read_python_declaration(source)
            if declaration.installs_packages or declaration.requirements or declaration.pyproject is not None:
                raise ValueError(f"Optional plugin '{name}' changes dependencies; rebuild the managed image instead")
        config = read_config_doc(path) if path.exists() else {}
        config = config or {}
        plugins = config.setdefault("plugins", {})
        requested = {"plugins": {key: sorted(selection[key]) for key in ("enabled", "disabled")}}
        plugins.update(user_plugin_selection(requested, previous=config))
        settle_record(plugins, selection.get("settled", ()))
        if (file_digest(path) or "missing") != before or optional_policy() != policy:
            raise ValueError("Plugin configuration or administrator policy changed during admission; retry")
        atomic_config_replace(path, config)
