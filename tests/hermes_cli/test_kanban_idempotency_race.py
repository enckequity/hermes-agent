"""create_task idempotency holds across connections racing on the same key."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_second_connection_racing_on_same_key_returns_the_first_task(kanban_home, monkeypatch):
    first, second = kbc.connect(), kbc.connect()
    try:
        new_task_id = kb._new_task_id
        winner = []

        def interleave():
            # The second creator has passed argument validation; the first one commits now.
            if not winner:
                winner.append(None)
                winner[0] = kb.create_task(first, title="first", idempotency_key="k1")
            return new_task_id()

        monkeypatch.setattr(kb, "_new_task_id", interleave)
        loser = kb.create_task(second, title="second", idempotency_key="k1")
        rows = second.execute("SELECT id FROM tasks WHERE idempotency_key = 'k1'").fetchall()
        assert [row["id"] for row in rows] == winner == [loser]
    finally:
        first.close()
        second.close()


def test_archived_key_still_admits_a_new_task(kanban_home):
    with kbc.connect() as conn:
        old = kb.create_task(conn, title="old", idempotency_key="k2")
        conn.execute("UPDATE tasks SET status = 'archived' WHERE id = ?", (old,))
        conn.commit()
        assert kb.create_task(conn, title="new", idempotency_key="k2") != old
