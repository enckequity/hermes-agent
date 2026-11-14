#!/usr/bin/env python3
"""Offline native CLI/config/PM regression. Run: python test_managed_optional.py ENGINE_ROOT."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[2]
CHILD = r'''
import argparse, json, sys
from hermes_cli.config import load_config, save_config
from hermes_cli.config_backend import read_config_doc
from hermes_cli.config_effective import load_user_config_effective
from hermes_constants import get_hermes_home
from pm.plugins_state import read_home_selection
action, name = sys.argv[1:3]
if action in ("enable", "disable"):
    from hermes_cli.plugins_cmd import plugins_command
    plugins_command(argparse.Namespace(plugins_action=action, name=name))
elif action == "save":
    cfg = load_config()
    cfg.setdefault("display", {})["show_reasoning"] = False
    save_config(cfg)
elif action == "race":
    from unittest.mock import patch
    from hermes_cli import plugins_cmd
    from hermes_cli.config import atomic_config_replace
    from hermes_cli.plugins_admission import admit_plugin_set_change
    from pm.filesystem import file_digest
    path = get_hermes_home() / "config.yaml"
    before = file_digest(path)
    find = plugins_cmd._find_plugin_entry
    def race(plugin):
        entry = find(plugin)
        current = read_config_doc(path)
        current["unrelated_writer"] = "preserved"
        atomic_config_replace(path, current)
        return entry
    with patch.object(plugins_cmd, "_find_plugin_entry", side_effect=race):
        admit_plugin_set_change({"required", "conductor"}, {"forbidden"},
                                expected_config=before, active_plugins_dir=get_hermes_home() / "plugins")
elif action == "inspect":
    cfg = load_config()
    print("RESULT:" + json.dumps({
        "loaded": cfg.get("plugins"),
        "effective": load_user_config_effective().get("plugins"),
        "selection": (read_home_selection(get_hermes_home()) or {}).get("plugins"),
        "raw": read_config_doc(get_hermes_home() / "config.yaml"),
    }))
elif action == "legacy_generator":
    from unittest.mock import patch
    from hermes_cli.plugins_admission import admit_plugin_set_change
    paths = [get_hermes_home() / part for part in ("first", "second")]
    with patch("pm.client.sync_venv") as sync:
        admit_plugin_set_change({"legacy"}, set(), extra_dirs=(p for p in paths))
    print("RESULT:" + json.dumps(sync.call_args.kwargs["plugins"].data["extra_dirs"]))
else:
    raise AssertionError(action)
'''


class ManagedOptionalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="managed-optional-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.managed = self.root / "managed"
        self.bundled = self.root / "bundled"
        for path in (self.home, self.managed, self.bundled):
            path.mkdir()
        self.installs = self.home / "installs"
        self.installs.mkdir(mode=0o555)
        self.addCleanup(lambda: self.installs.chmod(0o755))
        self.policy = {"plugins": {"enabled": ["required"], "disabled": ["forbidden"], "optional": ["conductor"]}}
        self.raw = {"plugins": {"enabled": ["private-dormant"], "disabled": [],
                                "entries": {"private-dormant": {"settings": {"keep": True}}}},
                    "display": {"show_reasoning": True}, "memory": {"provider": "none"}}
        self.write(self.managed / "config.yaml", self.policy)
        self.write(self.home / "config.yaml", self.raw)
        for name in ("required", "forbidden", "conductor", "rogue"):
            self.plugin(self.bundled / name, name)
        self.env = {**os.environ, "HOME": str(self.root), "HERMES_HOME": str(self.home),
                    "HERMES_MANAGED_DIR": str(self.managed), "HERMES_BUNDLED_PLUGINS": str(self.bundled),
                    "HERMES_CONFIG_BACKEND": "file", "HERMES_DISABLE_LAZY_INSTALLS": "1",
                    "PYTHONPATH": str(ENGINE), "PYTHONDONTWRITEBYTECODE": "1"}
        for name in ("HERMES_PROFILE", "HERMES_PROFILE_HOME", "HERMES_RUNTIME_DIR", "HERMES_MANAGED"):
            self.env.pop(name, None)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def plugin(self, path, name, dependency=False):
        self.write(path / "plugin.yaml", {"name": name, "version": "1.0.0", "description": "Offline fixture",
                                         **({"python_dependencies": ["not-installed-for-test==1"]} if dependency else {})})
        (path / "__init__.py").write_text("def register(ctx):\n    pass\n")

    def native(self, action, name="conductor", *, ok=True, home=None):
        env = {**self.env, **({"HERMES_HOME": str(home)} if home else {})}
        before = sorted(str(p.relative_to(self.installs)) for p in self.installs.rglob("*"))
        result = subprocess.run([sys.executable, "-c", CHILD, action, name], cwd=ENGINE,
                                env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertEqual(before, sorted(str(p.relative_to(self.installs)) for p in self.installs.rglob("*")))
        return result

    def inspect(self, *, home=None):
        output = self.native("inspect", home=home).stdout
        return json.loads(next(line[7:] for line in output.splitlines() if line.startswith("RESULT:")))

    def assert_selection(self, inspected, enabled, disabled):
        for view in ("loaded", "effective", "selection"):
            self.assertEqual(set(inspected[view]["enabled"]), set(enabled), view)
            self.assertEqual(set(inspected[view]["disabled"]), set(disabled), view)

    def test_native_enable_disable_survive_save_and_restart_without_dependency_writes(self):
        self.assert_selection(self.inspect(), ["required"], ["forbidden"])
        self.native("enable")
        enabled = self.inspect()
        self.assert_selection(enabled, ["required", "conductor"], ["forbidden"])
        self.assertIn("private-dormant", enabled["raw"]["plugins"]["enabled"])
        self.native("disable")
        self.native("save")
        disabled = self.inspect()
        self.assert_selection(disabled, ["required"], ["forbidden", "conductor"])
        self.assertIn("private-dormant", disabled["raw"]["plugins"]["enabled"])
        self.assertTrue(disabled["raw"]["plugins"]["entries"]["private-dormant"]["settings"]["keep"])
        self.assertNotIn("optional", disabled["raw"]["plugins"])

    def test_required_and_forbidden_choices_are_refused(self):
        before = (self.home / "config.yaml").read_bytes()
        self.native("disable", "required", ok=False)
        self.native("enable", "forbidden", ok=False)
        self.native("enable", "rogue", ok=False)
        self.assertEqual(before, (self.home / "config.yaml").read_bytes())

    def test_raw_user_cannot_expand_allowance_or_disable_required_plugins(self):
        self.raw["plugins"].update(optional=["rogue"], enabled=["rogue", "conductor"], disabled=["required"])
        self.write(self.home / "config.yaml", self.raw)
        self.assert_selection(self.inspect(), ["required", "conductor"], ["forbidden"])
        self.native("enable", "rogue", ok=False)

    def test_managed_disabled_wins_over_optional(self):
        self.policy["plugins"]["disabled"].append("conductor")
        self.write(self.managed / "config.yaml", self.policy)
        self.raw["plugins"]["enabled"].append("conductor")
        self.write(self.home / "config.yaml", self.raw)
        self.native("enable", ok=False)
        self.assert_selection(self.inspect(), ["required"], ["forbidden", "conductor"])

    def test_malformed_allowance_fails_closed(self):
        self.raw["plugins"]["enabled"].append("conductor")
        self.write(self.home / "config.yaml", self.raw)
        for malformed in ("conductor", ["conductor", 7]):
            self.policy["plugins"]["optional"] = malformed
            self.write(self.managed / "config.yaml", self.policy)
            self.assert_selection(self.inspect(), ["required"], ["forbidden"])
            self.native("enable", ok=False)

    def test_concurrent_unrelated_write_refuses_without_overwriting_it(self):
        self.native("race", ok=False)
        result = self.inspect()
        self.assert_selection(result, ["required"], ["forbidden"])
        self.assertEqual(result["raw"]["unrelated_writer"], "preserved")

    def test_dependency_changing_and_private_optional_plugins_are_refused(self):
        self.policy["plugins"]["optional"] += ["dependent", "private"]
        self.write(self.managed / "config.yaml", self.policy)
        self.plugin(self.bundled / "dependent", "dependent", dependency=True)
        self.plugin(self.home / "plugins" / "private", "private")
        before = (self.home / "config.yaml").read_bytes()
        self.native("enable", "dependent", ok=False)
        self.native("enable", "private", ok=False)
        self.assertEqual(before, (self.home / "config.yaml").read_bytes())

    def test_named_profile_choices_do_not_enable_other_profiles(self):
        alpha, beta = (self.home / "profiles" / name for name in ("alpha", "beta"))
        for home in (alpha, beta):
            self.write(home / "config.yaml", self.raw)
        self.native("enable", home=alpha)
        self.assert_selection(self.inspect(home=alpha), ["required", "conductor"], ["forbidden"])
        self.assert_selection(self.inspect(home=beta), ["required"], ["forbidden"])
        self.assert_selection(self.inspect(), ["required"], ["forbidden"])

    def test_no_managed_optional_key_preserves_legacy_overlay_and_raw_selection(self):
        del self.policy["plugins"]["optional"]
        self.write(self.managed / "config.yaml", self.policy)
        result = self.inspect()
        self.assertEqual(result["loaded"]["enabled"], ["required"])
        self.assertEqual(result["selection"]["enabled"], ["private-dormant"])

    def test_legacy_admission_preserves_generator_extra_directories(self):
        del self.policy["plugins"]["optional"]
        self.write(self.managed / "config.yaml", self.policy)
        output = self.native("legacy_generator").stdout
        result = json.loads(next(line[7:] for line in output.splitlines() if line.startswith("RESULT:")))
        self.assertEqual(result, [str((self.home / part).resolve()) for part in ("first", "second")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
