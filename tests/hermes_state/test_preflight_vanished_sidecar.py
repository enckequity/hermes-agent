"""The writability preflight must not report a sidecar that SQLite removed mid-check as read-only.

The last connection's close checkpoints and unlinks ``-wal``/``-shm``; when that lands between the
preflight's ``is_file()`` probe and its access check, the file is gone, not read-only.
"""

import sqlite3
from pathlib import Path

import pytest

from hermes_state import preflight_db_writability


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_sidecar_vanishing_after_existence_probe_is_not_read_only(tmp_path, monkeypatch, suffix):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = home / "state.db"
    sqlite3.connect(str(db)).close()
    sidecar = db.with_name(db.name + suffix)
    sidecar.write_bytes(b"")

    real_is_file = Path.is_file

    def is_file_then_vanish(self):
        present = real_is_file(self)
        if self == sidecar and present:
            self.unlink()  # SQLite's checkpoint-on-close removes it right after the probe
        return present

    monkeypatch.setattr(Path, "is_file", is_file_then_vanish)

    preflight_db_writability(db)  # must not raise "... is read-only for this user"
    assert not sidecar.exists()
