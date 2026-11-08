"""In-place compaction must not persist the gateway's replay-only timestamp prefix on user rows."""

from hermes_state import SessionDB

STAMP = "[Tue 2026-04-28 13:40:53 UTC] "


def test_archive_and_compact_stores_user_rows_without_replay_timestamp(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("s", source="gateway")
        for role, text in (("user", "hello there"), ("assistant", "hi"), ("user", "second"), ("assistant", "ok")):
            db.append_message("s", role, text)
        compacted = db.get_messages_as_conversation("s")
        for msg in compacted:  # what the gateway replay hands the agent with message_timestamps enabled
            if msg["role"] == "user":
                msg["content"] = STAMP + msg["content"]

        db.archive_and_compact("s", compacted, tail_count=len(compacted))

        active = db.get_messages_as_conversation("s")
        assert [m["content"] for m in active if m["role"] == "user"] == ["hello there", "second"]
        assert [m["content"] for m in compacted if m["role"] == "user"] == ["hello there", "second"]
    finally:
        db.close()
