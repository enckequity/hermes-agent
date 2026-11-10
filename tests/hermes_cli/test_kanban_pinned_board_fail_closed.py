"""#1198: a fenced worker's explicit board= must fail closed, not silently
write the HERMES_KANBAN_DB-pinned board. Same/omitted board and machine
pin-first iteration keep native behaviour."""
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def worker(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for var in ("HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT",
                "HERMES_KANBAN_ATTACHMENTS_ROOT", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kbc.connect(board="default").close()
    other = kb.boards_root() / "other"
    other.mkdir(parents=True)
    (other / "board.json").write_text(json.dumps({"slug": "other"}))
    kbc.connect(board="other").close()
    pinned = home / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(pinned))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    return pinned


def _count(path):
    import sqlite3
    with sqlite3.connect(path) as c:
        return c.execute("select count(*) from tasks").fetchone()[0]


@pytest.mark.parametrize("board", ["missing-board", "other"])
def test_conflicting_or_missing_board_fails_before_write(worker, board):
    before = _count(worker)
    with pytest.raises(ValueError, match="pinned"):
        kbc.connect(board=board)
    assert _count(worker) == before
    assert not (kb.boards_root() / "missing-board").exists()


def test_scoped_board_context_conflict_fails(worker):
    with kb.scoped_current_board("other"):
        with pytest.raises(ValueError, match="pinned"):
            kbc.connect()


@pytest.mark.parametrize("board", [None, "default", "DEFAULT"])
def test_same_or_omitted_board_uses_pin(worker, board):
    conn = kbc.connect(board=board)
    try:
        assert conn.execute("select count(*) from tasks").fetchone()[0] >= 0
    finally:
        conn.close()


def test_machine_pin_first_iteration_unchanged(worker, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    with kb.pin_first_board_resolution():
        for slug in ("default", "other", "missing-board"):
            assert kb.kanban_db_path(slug) == worker
        kbc.connect(board="other").close()


def test_unfenced_explicit_board_still_wins(worker, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert kb.kanban_db_path("other") == kb.boards_root() / "other" / "kanban.db"
    kbc.connect(board="other").close()


def test_kanban_create_tool_reports_error_and_writes_nothing(worker):
    from tools import kanban_tools
    before = _count(worker)
    out = json.loads(kanban_tools._handle_create(
        {"title": "x", "assignee": "nobody", "board": "missing-board"}))
    assert "error" in out and "pinned" in out["error"]
    assert _count(worker) == before
