"""Runtime admission regressions for upstream #54086/#54106."""

from pathlib import Path

import pytest

from hermes_cli import kanban as cli
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_swarm import SwarmWorkerSpec, create_swarm


def test_swarm_cli_omitted_runtime_bounds_every_phase(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    cli.run_slash("swarm Bounded --worker default:Evidence --verifier default --synthesizer default --idempotency-key bounded-fixture --json")
    with kb.connect_closing() as conn:
        tasks = kb.list_tasks(conn)
        assert len(tasks) == 4
        assert all(task.max_runtime_seconds == 600 for task in tasks)
    cli.run_slash("swarm Bounded --worker default:Evidence --verifier default --synthesizer default --idempotency-key bounded-fixture --max-runtime 5m --json")
    with kb.connect_closing() as conn:
        assert {task.id for task in kb.list_tasks(conn)} == {task.id for task in tasks}
        assert all(task.max_runtime_seconds == 600 for task in kb.list_tasks(conn))


@pytest.mark.parametrize("bad", [0, -1, True, 0.5, 2**63])
@pytest.mark.parametrize("per_worker", [False, True])
def test_swarm_invalid_runtime_creates_no_graph(tmp_path, bad, per_worker):
    with kb.connect_closing(tmp_path / "board.db") as conn:
        worker = SwarmWorkerSpec(profile="default", title="Evidence", body="Collect evidence", max_runtime_seconds=bad if per_worker else None)
        with pytest.raises(ValueError):
            create_swarm(conn, goal="Bounded", workers=[SwarmWorkerSpec(profile="default", title="First", body="Valid first worker"), worker], verifier_assignee="default", synthesizer_assignee="default", max_runtime_seconds=600 if per_worker else bad)
        assert kb.list_tasks(conn) == []
