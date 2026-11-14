"""The post-turn review fork is told which skills it cannot change (locked, read-only, plugin), as the curator
already skips them, so it stops proposing patches that skill_manage refuses one by one."""

import os
import stat
import sys

import pytest

from agent import background_review as br


def _skill(base, name):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n")
    return d


@pytest.fixture
def skills(tmp_path, monkeypatch):
    import agent.skill_utils as su
    import tools.skill_lock as sl
    _skill(tmp_path, "editable")
    _skill(tmp_path, "locked-one")
    ro = _skill(tmp_path, "mounted")
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setattr(su, "get_all_skills_dirs", lambda: [tmp_path])
    monkeypatch.setattr(sl, "locked_skill_dirs", lambda: [("locked-one", str(tmp_path / "locked-one"))])
    yield tmp_path
    ro.chmod(stat.S_IRWXU)


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="directory write bits are not enforced for root or on Windows")
def test_locked_and_read_only_skills_are_named(skills):
    assert br.unchangeable_skill_names() == ["locked-one", "mounted"]


def test_skill_prompts_carry_the_note_and_memory_only_does_not(skills, monkeypatch):
    monkeypatch.setattr(br, "unchangeable_skill_names", lambda: ["locked-one", "mounted"])
    agent = object()
    for memory, skill in ((False, True), (True, True), (False, False)):
        _target, prompt = br.spawn_background_review_thread(agent, [], review_memory=memory, review_skills=skill, task_cfg={})
        assert "locked or read-only in this profile: locked-one, mounted." in prompt
        assert "plugin:skill is provided by a plugin and is read-only" in prompt
    _target, prompt = br.spawn_background_review_thread(agent, [], review_memory=True, review_skills=False, task_cfg={})
    assert "Skills you cannot change" not in prompt


def test_note_without_frozen_skills_still_covers_plugin_skills_and_caps_the_list():
    assert "locked or read-only" not in br._frozen_skills_note([])
    names = [f"s{i:03d}" for i in range(br._FROZEN_SKILLS_CAP + 5)]
    assert "(and 5 more)" in br._frozen_skills_note(names)


def test_lookup_failures_never_raise(monkeypatch):
    import agent.skill_utils as su
    import tools.skill_lock as sl
    monkeypatch.setattr(sl, "locked_skill_dirs", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(su, "get_all_skills_dirs", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert br.unchangeable_skill_names() == []
