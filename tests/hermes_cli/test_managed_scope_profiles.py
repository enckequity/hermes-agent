"""Administrator-selected profile approval modes (TeamShift #898)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest


@pytest.fixture
def policy_homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    managed = tmp_path / "managed"
    managed.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    from hermes_cli import config, config_effective, managed_scope
    config._LOAD_CONFIG_CACHE.clear()
    config._RAW_CONFIG_CACHE.clear()
    config._LAST_EXPANDED_CONFIG_BY_PATH.clear()
    config_effective._EFFECTIVE_CACHE.clear()
    config_effective._LAST_GOOD_USER_RAW.clear()
    managed_scope.invalidate_managed_cache()
    (home / "config.yaml").write_text("approvals:\n  mode: off\n")
    return home, managed


def write_policy(managed, table="{default: manual}"):
    (managed / "config.yaml").write_text(
        "approvals:\n  mode: off\n  cron_mode: deny\n  profile_modes: " + table + "\n"
    )


@contextmanager
def profile(home):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def mode():
    from tools.approval_context import _get_approval_mode
    return _get_approval_mode()


def test_default_override_preserves_named_workers_and_ignores_launch_names(policy_homes, monkeypatch):
    home, managed = policy_homes
    worker = home / "profiles" / "team-owner"
    worker.mkdir(parents=True)
    (worker / "config.yaml").write_text("approvals:\n  mode: manual\n")
    monkeypatch.setenv("HERMES_PROFILE_NAME", "default")
    (home / "active_profile").write_text("team-owner")
    write_policy(managed)
    for target, expected in ((home, "manual"), (worker, "off"), (home, "manual")):
        with profile(target):
            assert mode() == expected


def test_concurrent_profile_contexts_do_not_share_selected_mode(policy_homes):
    home, managed = policy_homes
    worker = home / "profiles" / "team-owner"
    worker.mkdir(parents=True)
    write_policy(managed)

    def read(target):
        with profile(target):
            return [mode() for _ in range(8)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, [home, worker, home, worker]))
    assert results == [["manual"] * 8, ["off"] * 8, ["manual"] * 8, ["off"] * 8]


def test_custom_sentinel_is_not_a_profile_but_named_custom_is(policy_homes, tmp_path):
    home, managed = policy_homes
    write_policy(managed, "{custom: manual}")
    unknown = tmp_path / "unrelated"
    unknown.mkdir()
    named = home / "profiles" / "custom"
    named.mkdir(parents=True)
    with profile(unknown):
        assert mode() == "off"
    with profile(named):
        assert mode() == "manual"


def test_user_table_has_no_policy_authority(policy_homes):
    home, managed = policy_homes
    (managed / "config.yaml").write_text("approvals:\n  mode: off\n")
    (home / "config.yaml").write_text("approvals:\n  mode: manual\n  profile_modes: {default: manual}\n")
    assert mode() == "off"


def test_mode_stays_managed_and_normal_config_set_refuses(policy_homes):
    _, managed = policy_homes
    write_policy(managed)
    from hermes_cli import config, managed_scope
    assert "approvals.mode" in managed_scope.managed_config_keys()
    assert not any("profile_modes" in key for key in managed_scope.managed_config_keys())
    with pytest.raises(SystemExit):
        config.set_config_value("approvals.mode", "off")
    with pytest.raises(SystemExit):
        config.unset_config_value("approvals.mode")


def test_all_loaders_and_managed_metadata(policy_homes):
    _, managed = policy_homes
    write_policy(managed)
    from hermes_cli import cli_config_load, config, config_effective, managed_scope
    for cfg in (config.load_config(), config.load_config_readonly(),
                config_effective.load_user_config_effective(),
                cli_config_load.load_cli_config(), managed_scope.apply_managed_overlay({})):
        assert cfg["approvals"]["mode"] == "manual"
        assert cfg["approvals"]["cron_mode"] == "deny"
        assert "profile_modes" not in cfg["approvals"]


def test_managed_edit_and_removal_refresh_load_cache(policy_homes):
    _, managed = policy_homes
    from hermes_cli.config import load_config
    write_policy(managed)
    assert load_config()["approvals"]["mode"] == "manual"
    write_policy(managed, "{default: smart}")
    assert load_config()["approvals"]["mode"] == "smart"
    write_policy(managed, "{}")
    assert load_config()["approvals"]["mode"] is False


def test_effective_cache_keeps_explicit_path_reads_in_runtime_profile_scope(policy_homes):
    home, managed = policy_homes
    write_policy(managed)
    from hermes_cli.config_effective import load_user_config_effective
    path = home / "config.yaml"
    assert load_user_config_effective(path)["approvals"]["mode"] == "manual"
    worker = home / "profiles" / "team-owner"
    worker.mkdir(parents=True)
    with profile(worker):
        assert load_user_config_effective(path)["approvals"]["mode"] is False


@pytest.mark.parametrize("table", ["[]", "null", "{default: nope}", "{default: true}",
                                        "{default: {mode: manual}}", "{../bad: manual}"])
def test_invalid_parsed_table_propagates_through_all_loaders(policy_homes, table):
    _, managed = policy_homes
    write_policy(managed, table)
    from hermes_cli import cli_config_load, config, config_effective, managed_scope
    for load in (config.load_config, config.load_config_readonly,
                 config_effective.load_user_config_effective, cli_config_load.load_cli_config,
                 lambda: managed_scope.apply_managed_overlay({}), mode):
        with pytest.raises(managed_scope.ManagedProfilePolicyError):
            load()


def test_legacy_no_table_and_whole_yaml_parse_failure_are_unchanged(policy_homes):
    _, managed = policy_homes
    from hermes_cli import managed_scope
    (managed / "config.yaml").write_text("approvals:\n  mode: off\n")
    assert mode() == "off"
    (managed / "config.yaml").write_text("approvals: [\n")
    assert managed_scope.load_managed_config() == {}


def test_snapshot_reapplies_current_managed_mode(policy_homes, monkeypatch):
    _, managed = policy_homes
    write_policy(managed)
    from agent import safe_worker_policy
    from hermes_cli import config, config_effective
    snapshot = {"approvals": {"mode": "off"}, "agent": {"max_turns": 17}}
    monkeypatch.setattr(safe_worker_policy, "worker_config_snapshot", lambda: snapshot.copy())
    for load in (config.load_config, config_effective.load_user_config_effective):
        loaded = load()
        assert loaded["approvals"]["mode"] == "manual"
        assert loaded["agent"]["max_turns"] == 17
    write_policy(managed, "{default: nope}")
    from hermes_cli.managed_scope import ManagedProfilePolicyError
    for load in (config.load_config, config_effective.load_user_config_effective):
        with pytest.raises(ManagedProfilePolicyError):
            load()


def test_last_good_user_config_reapplies_changed_managed_mode(policy_homes):
    home, managed = policy_homes
    from hermes_cli.config import load_config
    (managed / "config.yaml").write_text("approvals:\n  mode: off\n")
    assert load_config()["approvals"]["mode"] is False
    (home / "config.yaml").write_text("approvals: [\n")
    write_policy(managed)
    assert load_config()["approvals"]["mode"] == "manual"
    write_policy(managed, "{default: nope}")
    from hermes_cli.managed_scope import ManagedProfilePolicyError
    with pytest.raises(ManagedProfilePolicyError):
        load_config()


def test_invalid_policy_reaches_actual_approval_gate(policy_homes, monkeypatch):
    _, managed = policy_homes
    write_policy(managed, "{default: nope}")
    from hermes_cli.managed_scope import ManagedProfilePolicyError
    from tools import approval
    monkeypatch.setattr(approval, "_yolo_active", lambda: False)
    with pytest.raises(ManagedProfilePolicyError):
        approval.request_tool_approval("terminal", "Read-only approval test")


def test_existing_yolo_bypass_is_unchanged(policy_homes, monkeypatch):
    _, managed = policy_homes
    write_policy(managed)
    from tools import approval
    monkeypatch.setattr(approval, "_yolo_active", lambda: True)
    assert approval.request_tool_approval("terminal", "Read-only approval test")["approved"]


def test_roomlink_catalog_uses_current_profile_mode(policy_homes):
    home, managed = policy_homes
    write_policy(managed)
    from gateway.hosted_room_execution_policy import execution_policy_mapping
    from gateway.platforms.api_server_room_grants import _require_unchanged_execution_policy
    from tools import approval
    # Preserve the native yolo precedence: this test qualifies non-yolo mode selection.
    assert not approval._YOLO_MODE_FROZEN
    first = execution_policy_mapping(target_profile="default")
    assert first["approval_mode"] == "manual"
    worker = home / "profiles" / "team-owner"
    worker.mkdir(parents=True)
    with profile(worker):
        assert execution_policy_mapping(target_profile="team-owner")["approval_mode"] == "off"
    write_policy(managed, "{default: smart}")
    current = execution_policy_mapping(target_profile="default")
    with pytest.raises(ValueError, match="policy"):
        _require_unchanged_execution_policy({"execution_policy_digest": first["policy_digest"]}, current)
