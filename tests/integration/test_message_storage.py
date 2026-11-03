from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.message import MessageService
from src.models.message import Message, MessageBox, MessageKind, MessageStatus
from src.storage.postgres import PostgresStorage


def _m(body: str, sender: str = "taner", to: str = "sude", minutes_ago: int = 0,
       **kw: object) -> Message:
    return Message(
        sender_user_id=sender, recipient_user_id=to, body=body,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago), **kw,  # type: ignore[arg-type]
    )


async def test_create_get_and_list_by_box(storage: PostgresStorage) -> None:
    old = await storage.message_create(_m("old", minutes_ago=5, sender_auth="admin-key"))
    new = await storage.message_create(_m("new", kind=MessageKind.REQUEST, sender_session="kai"))
    await storage.message_create(_m("to someone else", to="eve"))

    got = await storage.message_get(new.id)
    assert got is not None and got.kind == MessageKind.REQUEST and got.sender_session == "kai"
    assert (await storage.message_get(old.id)).sender_auth == "admin-key"  # type: ignore[union-attr]

    inbox = await storage.message_list("sude", MessageBox.INBOX)
    assert [m.body for m in inbox] == ["new", "old"]
    sent = await storage.message_list("taner", MessageBox.SENT)
    assert len(sent) == 3
    assert await storage.message_list("taner", MessageBox.INBOX) == []


async def test_mark_delivered_only_recipients_unread(storage: PostgresStorage) -> None:
    a = await storage.message_create(_m("a"))
    b = await storage.message_create(_m("b"))
    assert await storage.message_mark_delivered("eve", [a.id]) == 0  # not hers
    assert await storage.message_mark_delivered("sude", [a.id]) == 1
    assert await storage.message_mark_delivered("sude", [a.id]) == 0  # only once
    unread = await storage.message_list("sude", status=MessageStatus.SENT)
    assert [m.id for m in unread] == [b.id]
    delivered = await storage.message_get(a.id)
    assert delivered is not None and delivered.delivered_at is not None


async def test_user_known_from_usage_or_sent_messages(storage: PostgresStorage) -> None:
    assert not await storage.message_user_known("sude")
    await storage.usage_add(user_id="sude", tool="context_brief", input_tokens=1, output_tokens=1)
    assert await storage.message_user_known("sude")
    await storage.message_create(_m("hi", sender="nina", to="sude"))
    assert await storage.message_user_known("nina")
    assert not await storage.message_user_known("sude-typo")


async def test_service_send_reply_round_trip(storage: PostgresStorage) -> None:
    await storage.usage_add(user_id="sude", tool="context_brief", input_tokens=1, output_tokens=1)
    svc = MessageService(storage)
    with pytest.raises(ValueError, match="unknown recipient"):
        await svc.send("taner", "nobody", "hi")
    first = await svc.send("taner", "sude", "question?", MessageKind.QUESTION)
    reply = await svc.reply("sude", first.id, "answer")
    assert reply.recipient_user_id == "taner" and reply.parent_id == first.id
    with pytest.raises(ValueError, match="one level deep"):
        await svc.reply("taner", reply.id, "nested")


async def test_parent_must_exist(storage: PostgresStorage) -> None:
    with pytest.raises(IntegrityError):
        await storage.message_create(_m("orphan", parent_id="missing"))
