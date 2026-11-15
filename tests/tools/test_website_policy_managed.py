"""The managed scope (/etc/hermes/config.yaml, or $HERMES_MANAGED_DIR) binds the web tools' website blocklist."""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_yaml as yaml
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli import managed_scope
from tools import website_policy


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    for name in ("_cached_policy", "_cached_policy_path"):
        setattr(website_policy, name, None)
    website_policy._cached_policy_time = 0.0
    managed = tmp_path / "managed"
    managed.mkdir()
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    managed_scope.invalidate_managed_cache()
    token = set_hermes_home_override(tmp_path / "home")
    (tmp_path / "home").mkdir()
    yield
    reset_hermes_home_override(token)
    managed_scope.invalidate_managed_cache()
    website_policy._cached_policy = None


def _managed(tmp_path: Path, domains: list[str], enabled: bool = True) -> None:
    (tmp_path / "managed" / "config.yaml").write_text(
        yaml.safe_dump({"security": {"website_blocklist": {"enabled": enabled, "domains": domains}}}), encoding="utf-8")
    managed_scope.invalidate_managed_cache()


def test_managed_blocklist_applies_with_no_user_config(tmp_path):
    _managed(tmp_path, ["blocked.example"])
    assert website_policy.check_website_access("https://www.blocked.example/x")["rule"] == "blocked.example"
    assert website_policy.check_website_access("https://fine.example/") is None


def test_managed_value_wins_per_leaf_over_the_users_own_blocklist(tmp_path):
    (tmp_path / "home" / "config.yaml").write_text(
        yaml.safe_dump({"security": {"website_blocklist": {"enabled": False, "domains": ["user.example"]}}}), encoding="utf-8")
    _managed(tmp_path, ["blocked.example"])
    policy = website_policy.load_website_blocklist()
    assert policy["enabled"] is True
    assert [rule["pattern"] for rule in policy["rules"]] == ["blocked.example"]


def test_without_a_managed_scope_the_users_config_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_MANAGED_DIR")
    managed_scope.invalidate_managed_cache()
    (tmp_path / "home" / "config.yaml").write_text(
        yaml.safe_dump({"security": {"website_blocklist": {"enabled": True, "domains": ["user.example"]}}}), encoding="utf-8")
    assert website_policy.check_website_access("https://user.example/")["rule"] == "user.example"


def test_an_explicit_config_path_ignores_the_managed_scope(tmp_path):
    _managed(tmp_path, ["blocked.example"])
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(yaml.safe_dump({"security": {"website_blocklist": {"enabled": True, "domains": []}}}), encoding="utf-8")
    assert website_policy.check_website_access("https://blocked.example/", config_path=explicit) is None
