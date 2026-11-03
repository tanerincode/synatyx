from __future__ import annotations

from src.models.message import Message, MessageBox, MessageKind, MessageStatus
from src.storage.postgres import PostgresStorage

# Key under which the HTTP auth middleware publishes the credential a request
# came in on (see AdminKeyAuthMiddleware). Read back by the MCP server and
# stored on each message as sender_auth.
AUTH_SCOPE_KEY = "synatyx.auth"

MAX_BODY_CHARS = 8000


class MessageService:
    """Messages from one user's Kai to another's, stored in Postgres.

    Unlike handoffs these cross user_ids on purpose: the sender writes into
    the recipient's inbox. The sender's user_id is self-declared by the
    client; sender_auth records the credential the request actually used.
    """

    def __init__(self, storage: PostgresStorage) -> None:
        self._storage = storage

    async def send(
        self,
        sender_user_id: str,
        to: str,
        body: str,
        kind: MessageKind = MessageKind.NOTE,
        *,
        sender_session: str | None = None,
        sender_auth: str | None = None,
    ) -> Message:
        to = (to or "").strip()
        if not to:
            raise ValueError("message needs a recipient user_id in 'to'")
        _check_body(body)
        if not await self._storage.message_user_known(to):
            raise ValueError(
                f"unknown recipient user_id '{to}': no Synatyx activity under that "
                "user_id in the usage-retention window, so the message was not stored"
            )
        message = Message(
            sender_user_id=sender_user_id,
            sender_session=sender_session,
            sender_auth=sender_auth,
            recipient_user_id=to,
            kind=kind,
            body=body,
        )
        return await self._storage.message_create(message)

    async def reply(
        self,
        sender_user_id: str,
        message_id: str,
        body: str,
        *,
        sender_session: str | None = None,
        sender_auth: str | None = None,
    ) -> Message:
        _check_body(body)
        parent = await self._storage.message_get(message_id)
        if parent is None or sender_user_id not in (
            parent.sender_user_id, parent.recipient_user_id
        ):
            # Someone else's message reads the same as a missing one.
            raise ValueError(f"message '{message_id}' not found")
        if parent.parent_id is not None:
            raise ValueError(
                f"message '{message_id}' is already a reply; threads are one level "
                f"deep, so reply to the original message '{parent.parent_id}'"
            )
        recipient = (
            parent.sender_user_id
            if sender_user_id == parent.recipient_user_id
            else parent.recipient_user_id
        )
        message = Message(
            sender_user_id=sender_user_id,
            sender_session=sender_session,
            sender_auth=sender_auth,
            recipient_user_id=recipient,
            kind=MessageKind.NOTE,
            body=body,
            parent_id=parent.id,
        )
        return await self._storage.message_create(message)

    async def list(
        self,
        user_id: str,
        box: MessageBox = MessageBox.INBOX,
        *,
        status: MessageStatus | None = None,
        limit: int = 50,
    ) -> list[Message]:
        """Read messages again. Listing never changes their status."""
        return await self._storage.message_list(user_id, box, status=status, limit=limit)


def _check_body(body: str) -> None:
    if not isinstance(body, str) or not body.strip():
        raise ValueError("message body is empty")
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"message body is longer than {MAX_BODY_CHARS} characters")
