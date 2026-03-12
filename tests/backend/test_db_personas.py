"""Tests for persona DB helpers."""

import pytest
from backend.db import models, connection as db


@pytest.mark.asyncio
async def test_built_in_personas_seeded(mem_db):
    """conftest seeds built-in personas; they should be visible."""
    rows = await models.get_personas(mem_db)
    ids = {r["id"] for r in rows}
    assert "default" in ids
    assert "socratic" in ids


@pytest.mark.asyncio
async def test_create_and_list_persona(mem_db):
    row = await models.create_persona(
        mem_db,
        persona_id="test-persona",
        user_id=db.ANON_USER_ID,
        name="Tester",
        instructions="Be helpful.",
    )
    assert row["id"] == "test-persona"

    rows = await models.get_personas(mem_db, db.ANON_USER_ID)
    ids = {r["id"] for r in rows}
    assert "test-persona" in ids


@pytest.mark.asyncio
async def test_delete_own_persona(mem_db):
    await models.create_persona(
        mem_db, "bye-persona", db.ANON_USER_ID, "Bye", "instructions"
    )
    deleted = await models.delete_persona(mem_db, "bye-persona", db.ANON_USER_ID)
    assert deleted is True
    rows = await models.get_personas(mem_db, db.ANON_USER_ID)
    assert all(r["id"] != "bye-persona" for r in rows)


@pytest.mark.asyncio
async def test_delete_builtin_persona_denied(mem_db):
    """Built-in personas have user_id=None; delete by user should fail."""
    deleted = await models.delete_persona(mem_db, "socratic", db.ANON_USER_ID)
    assert deleted is False
    rows = await models.get_personas(mem_db)
    assert any(r["id"] == "socratic" for r in rows)
