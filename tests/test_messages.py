"""Kai-to-Kai messages: MessageService, the context_message_* tools, the
brief's inbox section and the auth label the HTTP middleware publishes."""
from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.core.brief import BriefService
from src.core.message import AUTH_SCOPE_KEY, MessageService
from src.models.message import (
    NOTICE_RULE,
    Message,
    MessageBox,
    MessageKind,
    MessageStatus,
)
from src.transports.mcp.http_server import AdminKeyAuthMiddleware
from src.transports.mcp.server import SynatyxMCPServer, _auth_from_request
from tests.test_brief import FakePostgresBrief, FakeQdrantBrief, _item

TANER = "taner"
SUDE = "sude"


class FakeMessageStorage:
    """In-memory stand-in for the message_* methods of PostgresStorage."""

    def __init__(self, known: set[str] | None = None) -> None:
        self.messages: dict[str, Message] = {}
        self.known = set(known or {TANER, SUDE})
        self.fail = False

    async def message_create(self, m: Message) -> Message:
        self.messages[m.id] = m.model_copy(deep=True)
        return m

    async def message_get(self, message_id: str) -> Message | None:
        m = self.messages.get(message_id)
        return m.model_copy(deep=True) if m else None

    async def message_list(self, user_id, box=MessageBox.INBOX, *, status=None, limit=50):
        if self.fail:
            raise RuntimeError("db down")
        attr = "recipient_user_id" if box == MessageBox.INBOX else "sender_user_id"
        out = [m for m in self.messages.values() if getattr(m, attr) == user_id]
        if status is not None:
            out = [m for m in out if m.status == status]
        out.sort(key=lambda m: m.created_at, reverse=True)
        return [m.model_copy(deep=True) for m in out[:limit]]

    async def message_mark_delivered(self, recipient_user_id, message_ids):
        n = 0
        for mid in message_ids:
            m = self.messages.get(mid)
            if m and m.recipient_user_id == recipient_user_id and m.status == MessageStatus.SENT:
                m.status = MessageStatus.DELIVERED
                m.delivered_at = datetime.now(UTC)
                n += 1
        return n

    async def message_user_known(self, user_id: str) -> bool:
        return user_id in self.known or any(
            m.sender_user_id == user_id for m in self.messages.values()
        )


# ── MessageService ───────────────────────────────────────────────────────────

@pytest.fixture
def store() -> FakeMessageStorage:
    return FakeMessageStorage()


@pytest.fixture
def svc(store: FakeMessageStorage) -> MessageService:
    return MessageService(store)  # type: ignore[arg-type]


async def test_send_stores_sender_details(svc, store) -> None:
    m = await svc.send(TANER, SUDE, "PR is up", MessageKind.QUESTION,
                       sender_session="kai", sender_auth="admin-key")
    stored = store.messages[m.id]
    assert stored.sender_user_id == TANER and stored.recipient_user_id == SUDE
    assert stored.sender_session == "kai" and stored.sender_auth == "admin-key"
    assert stored.kind == MessageKind.QUESTION and stored.status == MessageStatus.SENT
    assert stored.parent_id is None


async def test_send_to_unknown_user_errors_and_stores_nothing(svc, store) -> None:
    with pytest.raises(ValueError, match="unknown recipient user_id 'nobody'"):
        await svc.send(TANER, "nobody", "hi")
    assert store.messages == {}


@pytest.mark.parametrize("body", ["", "   ", "x" * 8001])
async def test_send_rejects_empty_or_huge_body(svc, body) -> None:
    with pytest.raises(ValueError, match="message body"):
        await svc.send(TANER, SUDE, body)


async def test_reply_goes_to_the_other_party(svc) -> None:
    original = await svc.send(TANER, SUDE, "question?")
    back = await svc.reply(SUDE, original.id, "answer")
    assert back.recipient_user_id == TANER and back.parent_id == original.id
    again = await svc.reply(TANER, original.id, "thanks")
    assert again.recipient_user_id == SUDE and again.parent_id == original.id


async def test_reply_to_a_reply_is_rejected(svc) -> None:
    original = await svc.send(TANER, SUDE, "q")
    back = await svc.reply(SUDE, original.id, "a")
    with pytest.raises(ValueError, match="one level deep") as exc:
        await svc.reply(TANER, back.id, "nested")
    assert original.id in str(exc.value)


async def test_reply_to_someone_elses_message_reads_as_not_found(svc, store) -> None:
    store.known.add("eve")
    original = await svc.send(TANER, SUDE, "private")
    with pytest.raises(ValueError, match="not found"):
        await svc.reply("eve", original.id, "butting in")
    with pytest.raises(ValueError, match="not found"):
        await svc.reply(TANER, "missing-id", "x")


# ── context_message_* tools ──────────────────────────────────────────────────

class _NullRecorder:
    async def record(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
def server(store: FakeMessageStorage) -> SynatyxMCPServer:
    srv = SynatyxMCPServer.__new__(SynatyxMCPServer)
    srv._postgres = store  # type: ignore[assignment]
    srv._tracker = _NullRecorder()  # type: ignore[assignment]
    srv._usage = _NullRecorder()  # type: ignore[assignment]
    return srv


async def _send(server: SynatyxMCPServer, auth: str | None = "admin-key", **over: Any):
    args = {"user_id": TANER, "to": SUDE, "body": "hello", "kind": "request",
            "session": "kai", **over}
    return await server.run_tool("context_message_send", args, auth=auth)


async def test_tool_send_records_auth_and_notice(server, store) -> None:
    res = await _send(server, auth="oauth:client-123")
    msg = res["message"]
    assert msg["from"] == TANER and msg["to"] == SUDE and msg["kind"] == "request"
    assert msg["from_session"] == "kai" and msg["status"] == "sent"
    assert store.messages[msg["id"]].sender_auth == "oauth:client-123"
    assert msg["notice"].startswith(f"From {TANER}'s Kai. {NOTICE_RULE}")
    assert "self-declared" in msg["notice"] and "oauth:client-123" in msg["notice"]


async def test_tool_send_session_falls_back_to_session_id(server, store) -> None:
    res = await _send(server, session=None, session_id="synatyx-lead")
    assert res["message"]["from_session"] == "synatyx-lead"


async def test_tool_auth_does_not_leak_between_calls(server, store) -> None:
    await _send(server, auth="admin-key")
    res = await server._dispatch(
        "context_message_send", {"user_id": TANER, "to": SUDE, "body": "direct"}
    )
    assert res["message"]["sender_auth"] is None


async def test_tool_send_unknown_recipient_is_not_found(server, store) -> None:
    res = await _send(server, to="nobody")
    assert res["code"] == "NOT_FOUND" and "unknown recipient" in res["error"]
    assert store.messages == {}


@pytest.mark.parametrize(
    "over,needle",
    [({"kind": "order"}, "Invalid kind"), ({"to": ""}, "recipient"),
     ({"user_id": ""}, "user_id"), ({"body": None}, "body")],
)
async def test_tool_send_invalid_arguments(server, over, needle) -> None:
    args = {"user_id": TANER, "to": SUDE, "body": "x", **over}
    if over.get("body", "x") is None:
        args.pop("body")
    res = await server.run_tool("context_message_send", args)
    assert res["code"] == "INVALID" and needle in res["error"]


async def test_tool_reply_and_depth_error_codes(server) -> None:
    mid = (await _send(server))["message"]["id"]
    reply = await server.run_tool(
        "context_message_reply", {"user_id": SUDE, "message_id": mid, "body": "on it"}
    )
    assert reply["message"]["to"] == TANER and reply["message"]["parent_id"] == mid
    nested = await server.run_tool(
        "context_message_reply",
        {"user_id": TANER, "message_id": reply["message"]["id"], "body": "x"},
    )
    assert nested["code"] == "INVALID"
    missing = await server.run_tool(
        "context_message_reply", {"user_id": TANER, "message_id": "nope", "body": "x"}
    )
    assert missing["code"] == "NOT_FOUND"


async def test_tool_list_inbox_sent_and_status(server, store) -> None:
    await _send(server, body="one")
    await _send(server, body="two")
    inbox = await server.run_tool("context_message_list", {"user_id": SUDE})
    assert inbox["box"] == "inbox" and inbox["count"] == 2
    assert all(NOTICE_RULE in m["notice"] for m in inbox["messages"])
    sent = await server.run_tool("context_message_list", {"user_id": TANER, "box": "sent"})
    assert sent["count"] == 2
    assert (await server.run_tool("context_message_list", {"user_id": TANER}))["count"] == 0
    delivered = await server.run_tool(
        "context_message_list", {"user_id": SUDE, "status": "delivered"}
    )
    assert delivered["count"] == 0
    # listing never marks anything delivered
    assert all(m.status == MessageStatus.SENT for m in store.messages.values())
    bad = await server.run_tool("context_message_list", {"user_id": SUDE, "box": "trash"})
    assert bad["code"] == "INVALID"


def test_tool_descriptions_carry_the_rule() -> None:
    from src.transports.mcp.tools import TOOL_DEFINITIONS

    by_name = {t["name"]: t for t in TOOL_DEFINITIONS}
    for name in ("context_message_send", "context_message_reply", "context_message_list"):
        desc = by_name[name]["description"]
        assert "From <sender>'s Kai. " + NOTICE_RULE in desc
        assert "self-declared" in desc
    assert "inbox" in by_name["context_brief"]["description"]


def test_auth_from_request() -> None:
    assert _auth_from_request(None) == "stdio"
    assert _auth_from_request(types.SimpleNamespace(scope={})) == "unauthenticated"
    labelled = types.SimpleNamespace(scope={AUTH_SCOPE_KEY: "admin-key"})
    assert _auth_from_request(labelled) == "admin-key"


# ── Middleware labels the credential ─────────────────────────────────────────

class _Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token != "oauth-token":
            return None
        return AccessToken(token=token, client_id="claude-desktop", scopes=["synatyx"])


def test_middleware_publishes_auth_label() -> None:
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse({"auth": request.scope.get(AUTH_SCOPE_KEY)})

    app = Starlette(
        routes=[Route("/echo", echo)],
        middleware=[Middleware(
            AdminKeyAuthMiddleware, admin_key="k", header_name="X-Auth-Key",
            public_paths=frozenset(), token_verifier=_Verifier(),
        )],
    )
    client = TestClient(app)
    assert client.get("/echo", headers={"X-Auth-Key": "k"}).json() == {"auth": "admin-key"}
    oauth = client.get("/echo", headers={"Authorization": "Bearer oauth-token"})
    assert oauth.json() == {"auth": "oauth:claude-desktop"}
    assert client.get("/echo").status_code == 401


# ── context_brief inbox ──────────────────────────────────────────────────────

class FakePostgresInbox(FakePostgresBrief, FakeMessageStorage):
    def __init__(self) -> None:
        FakePostgresBrief.__init__(self)
        FakeMessageStorage.__init__(self)


# Built once so two briefs over them can be compared byte for byte.
_FACTS = [_item(f"fact {i} " + "x" * 200, metadata={"project": "synatyx"}) for i in range(30)]


def _brief_service(postgres: Any) -> BriefService:
    return BriefService(
        FakeQdrantBrief(_FACTS),  # type: ignore[arg-type]
        FakeQdrantBrief([]),  # type: ignore[arg-type]
        postgres,
    )


def _msg(body: str, minutes_ago: int = 0, to: str = "u1", **kw: Any) -> Message:
    return Message(
        sender_user_id=TANER, recipient_user_id=to, body=body, sender_auth="admin-key",
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago), **kw,
    )


async def test_brief_is_byte_identical_when_inbox_is_empty() -> None:
    # Before messages existed (storage without message_* methods) vs an empty inbox
    # vs an inbox holding only someone else's and already-delivered messages.
    legacy = await _brief_service(FakePostgresBrief()).brief("u1", project="synatyx")
    empty = await _brief_service(FakePostgresInbox()).brief("u1", project="synatyx")
    pg = FakePostgresInbox()
    for m in (_msg("not yours", to=SUDE), _msg("old", status=MessageStatus.DELIVERED)):
        pg.messages[m.id] = m
    others = await _brief_service(pg).brief("u1", project="synatyx")
    assert "inbox" not in legacy
    assert json.dumps(empty, sort_keys=True) == json.dumps(legacy, sort_keys=True)
    assert json.dumps(others, sort_keys=True) == json.dumps(legacy, sort_keys=True)


async def test_brief_inbox_newest_first_with_notice_and_marks_delivered() -> None:
    pg = FakePostgresInbox()
    old = _msg("older", minutes_ago=10)
    new = _msg("newer", minutes_ago=1, kind=MessageKind.QUESTION)
    for m in (old, new):
        pg.messages[m.id] = m
    result = await _brief_service(pg).brief("u1", project="synatyx")
    inbox = result["inbox"]
    assert [e["body"] for e in inbox] == ["newer", "older"]
    assert inbox[0]["kind"] == "question" and inbox[0]["from"] == TANER
    assert inbox[0]["notice"].startswith(f"From {TANER}'s Kai. {NOTICE_RULE}")
    assert all(m.status == MessageStatus.DELIVERED for m in pg.messages.values())
    # delivered messages do not come back in the next brief
    assert "inbox" not in await _brief_service(pg).brief("u1", project="synatyx")


async def test_brief_inbox_takes_its_share_from_project_knowledge() -> None:
    plain = await _brief_service(FakePostgresInbox()).brief("u1", project="synatyx")
    pg = FakePostgresInbox()
    m = _msg("hi")
    pg.messages[m.id] = m
    with_inbox = await _brief_service(pg).brief("u1", project="synatyx")
    assert len(with_inbox["project_knowledge"]) < len(plain["project_knowledge"])


async def test_brief_inbox_budget_leaves_the_rest_unread() -> None:
    pg = FakePostgresInbox()
    msgs = [_msg(f"message {i} " + "y" * 300, minutes_ago=i) for i in range(10)]
    for m in msgs:
        pg.messages[m.id] = m
    result = await _brief_service(pg).brief("u1", project="synatyx", max_tokens=2000)
    shown = {e["id"] for e in result["inbox"]}
    assert 0 < len(shown) < 10
    assert shown == {m.id for m in msgs[: len(shown)]}  # the newest ones
    for m in pg.messages.values():
        expected = MessageStatus.DELIVERED if m.id in shown else MessageStatus.SENT
        assert m.status == expected


async def test_brief_inbox_long_first_message_is_truncated_not_hidden() -> None:
    pg = FakePostgresInbox()
    m = _msg("z" * 8000)
    pg.messages[m.id] = m
    result = await _brief_service(pg).brief("u1", project="synatyx", max_tokens=1000)
    entry = result["inbox"][0]
    assert entry["truncated"] is True and len(entry["body"]) < 8000
    assert NOTICE_RULE in entry["notice"]


async def test_brief_inbox_storage_failure_is_swallowed() -> None:
    pg = FakePostgresInbox()
    m = _msg("hi")
    pg.messages[m.id] = m
    pg.fail = True
    legacy = await _brief_service(FakePostgresBrief()).brief("u1", project="synatyx")
    result = await _brief_service(pg).brief("u1", project="synatyx")
    assert json.dumps(result, sort_keys=True) == json.dumps(legacy, sort_keys=True)
