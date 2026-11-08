"""Offline completion-boundary qualification for TeamShift #1031 and upstream PR134448."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from hermes_cli import goals
from tools.registry import registry
import tools.goal_tool  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    goals._DB_CACHE.clear()
    monkeypatch.setattr(goals, 'agent_goal_tool_enabled', lambda: True)
    monkeypatch.setattr(goals, 'draft_contract', lambda *a, **kw: None)
    yield home
    goals._DB_CACHE.clear()


def call(action, session_id='bound-session', **args):
    return json.loads(registry.dispatch('goal', {'action': action, **args}, session_id=session_id))


def persisted(home, sid='bound-session'):
    with sqlite3.connect(home / 'state.db') as conn:
        return json.loads(conn.execute('SELECT value FROM state_meta WHERE key=?', ('goal:' + sid,)).fetchone()[0])


def create():
    assert call('create', objective='Deliver the verified local test artifact',
                acceptance_criteria='The local artifact passes its required checks')['created']
    return goals.GoalManager(session_id='bound-session')


def test_complete_respects_real_failing_shell_gate(isolated_store, monkeypatch):
    mgr = create()
    mgr.add_gate('exit 7')
    audit = Mock(return_value=('done', 'mocked DONE', False, None, False))
    monkeypatch.setattr(goals, 'judge_goal', audit)
    native = mgr.evaluate_after_turn('The artifact is complete according to this claim')
    assert native['verdict'] == 'gate_failed'
    assert persisted(isolated_store)['gates'][0]['last_exit_code'] == 7
    audit.assert_not_called()
    result = call('complete', evidence='The artifact is complete according to this claim')
    assert not result.get('completed'), result
    assert persisted(isolated_store)['status'] != 'done'
    audit.assert_not_called()


def test_complete_respects_unexpired_wait(isolated_store, monkeypatch):
    mgr = create()
    mgr.wait_for_seconds(300, reason='Owned local prerequisite is still running')
    audit = Mock(return_value=('done', 'mocked DONE', False, None, False))
    monkeypatch.setattr(goals, 'judge_goal', audit)
    result = call('complete', evidence='The artifact is complete according to this claim')
    assert not result.get('completed'), result
    assert persisted(isolated_store)['status'] != 'done'
    audit.assert_not_called()


def test_complete_includes_unfinished_subgoal_in_audit(isolated_store, monkeypatch):
    mgr = create()
    criterion = mgr.add_subgoal('The required review artifact has not been produced yet')
    audit = Mock(return_value=('done', 'mocked DONE', False, None, False))
    monkeypatch.setattr(goals, 'judge_goal', audit)
    call('complete', evidence='The main artifact is complete according to this claim')
    assert audit.call_args.kwargs.get('subgoals') == [criterion]


def test_registry_uses_runtime_session_and_persists_real_db(isolated_store):
    result = json.loads(registry.dispatch('goal', {
        'action': 'create', 'objective': 'Create the artifact only in the bound conversation',
        'acceptance_criteria': 'The artifact exists', 'session_id': 'foreign-session',
    }, session_id='bound-session'))
    assert result['created']
    assert persisted(isolated_store)['status'] == 'active'
    assert call('get', session_id='foreign-session') == {'goal': None}
    assert call('get')['goal'] == result['goal']
    assert 'error' in json.loads(registry.dispatch('goal', {'action': 'get'}))


def test_complete_checks_passing_gate_without_consuming_another_turn(isolated_store, monkeypatch):
    mgr = create()
    mgr.add_gate('exit 0')
    audit = Mock(return_value=('done', 'mocked DONE', False, None, False))
    monkeypatch.setattr(goals, 'judge_goal', audit)
    before = mgr.state.turns_used
    assert call('complete', evidence='The artifact has passed the required local checks')['completed']
    saved = persisted(isolated_store)
    assert saved['status'] == 'done'
    assert saved['gates'][0]['last_exit_code'] == 0
    assert saved['turns_used'] == before
    audit.assert_called_once()


def test_complete_does_not_resume_paused_goal(isolated_store, monkeypatch):
    mgr = create()
    mgr.pause('The owner paused this work')
    audit = Mock(return_value=('done', 'mocked DONE', False, None, False))
    monkeypatch.setattr(goals, 'judge_goal', audit)
    assert not call('complete', evidence='The artifact is complete according to this claim').get('completed')
    assert persisted(isolated_store)['status'] == 'paused'
    audit.assert_not_called()


def test_complete_refuses_when_audit_rejects_required_subgoal(isolated_store, monkeypatch):
    mgr = create()
    criterion = mgr.add_subgoal('The required review artifact has not been produced yet')

    def audit(_goal, _response, **kwargs):
        if criterion in (kwargs.get('subgoals') or []):
            return ('continue', 'Required review artifact is still missing', False, None, False)
        return ('done', 'Main artifact exists', False, None, False)

    monkeypatch.setattr(goals, 'judge_goal', audit)
    result = call('complete', evidence='The main artifact is complete according to this claim')
    assert not result.get('completed'), result
    assert result['verdict'] == 'continue'
    assert persisted(isolated_store)['status'] == 'active'
