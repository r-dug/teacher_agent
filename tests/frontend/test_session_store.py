"""Tests for the frontend session store."""

import pytest
from frontend.session_store import SessionStore


@pytest.fixture
def store():
    return SessionStore()


def test_add_and_get(store):
    entry = store.add("sess-1", "user-1")
    assert entry.session_id == "sess-1"
    assert entry.user_id == "user-1"
    assert store.get("sess-1") is entry


def test_get_nonexistent(store):
    assert store.get("no-such-session") is None


def test_remove(store):
    store.add("sess-2", "user-2")
    store.remove("sess-2")
    assert store.get("sess-2") is None


def test_remove_nonexistent_is_safe(store):
    store.remove("ghost")  # should not raise


def test_len(store):
    assert len(store) == 0
    store.add("s1", "u1")
    store.add("s2", "u2")
    assert len(store) == 2


def test_set_turn_status(store):
    store.add("sess-3", "user-3")
    store.set_turn("sess-3", "turn-uuid", "running")
    turn_id, status = store.get_turn_status("sess-3")
    assert turn_id == "turn-uuid"
    assert status == "running"


def test_turn_status_default(store):
    turn_id, status = store.get_turn_status("nonexistent")
    assert turn_id is None
    assert status == "idle"


def test_set_turn_on_nonexistent_session_is_safe(store):
    store.set_turn("ghost", "turn-id", "running")  # should not raise
    assert store.get("ghost") is None
