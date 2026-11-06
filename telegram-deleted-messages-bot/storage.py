"""Хранилище сообщений и бизнес-подключений на SQLite (aiosqlite)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    business_connection_id TEXT PRIMARY KEY,
    owner_user_id          INTEGER,
    user_chat_id           INTEGER,
    is_enabled             INTEGER,
    updated_at             INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    business_connection_id TEXT,
    chat_id                INTEGER,
    message_id             INTEGER,
    sender_id              INTEGER,
    sender_name            TEXT,
    chat_title             TEXT,
    is_outgoing            INTEGER,
    text                   TEXT,
    media_type             TEXT,
    file_id                TEXT,
    msg_date               INTEGER,
    edited                 INTEGER DEFAULT 0,
    created_at             INTEGER,
    PRIMARY KEY (business_connection_id, chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
"""


@dataclass
class StoredConnection:
    business_connection_id: str
    owner_user_id: int
    user_chat_id: int
    is_enabled: bool


@dataclass
class StoredMessage:
    chat_id: int
    message_id: int
    sender_id: Optional[int]
    sender_name: str
    chat_title: str
    is_outgoing: bool
    text: Optional[str]
    media_type: Optional[str]
    file_id: Optional[str]
    msg_date: int
    edited: bool


class Storage:
    def __init__(self, path: str):
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage is not connected. Call connect() first.")
        return self._db

    # --- подключения ---

    async def save_connection(
        self,
        business_connection_id: str,
        owner_user_id: int,
        user_chat_id: int,
        is_enabled: bool,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO connections
                (business_connection_id, owner_user_id, user_chat_id, is_enabled, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(business_connection_id) DO UPDATE SET
                owner_user_id = excluded.owner_user_id,
                user_chat_id  = excluded.user_chat_id,
                is_enabled    = excluded.is_enabled,
                updated_at    = excluded.updated_at
            """,
            (business_connection_id, owner_user_id, user_chat_id,
             1 if is_enabled else 0, int(time.time())),
        )
        await self.db.commit()

    async def get_connection(self, business_connection_id: str) -> Optional[StoredConnection]:
        async with self.db.execute(
            "SELECT * FROM connections WHERE business_connection_id = ?",
            (business_connection_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return StoredConnection(
            business_connection_id=row["business_connection_id"],
            owner_user_id=row["owner_user_id"],
            user_chat_id=row["user_chat_id"],
            is_enabled=bool(row["is_enabled"]),
        )

    # --- сообщения ---

    async def save_message(
        self,
        business_connection_id: str,
        chat_id: int,
        message_id: int,
        sender_id: Optional[int],
        sender_name: str,
        chat_title: str,
        is_outgoing: bool,
        text: Optional[str],
        media_type: Optional[str],
        file_id: Optional[str],
        msg_date: int,
        edited: bool = False,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO messages
                (business_connection_id, chat_id, message_id, sender_id, sender_name,
                 chat_title, is_outgoing, text, media_type, file_id, msg_date, edited, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(business_connection_id, chat_id, message_id) DO UPDATE SET
                text       = excluded.text,
                media_type = excluded.media_type,
                file_id    = excluded.file_id,
                edited     = excluded.edited
            """,
            (business_connection_id, chat_id, message_id, sender_id, sender_name,
             chat_title, 1 if is_outgoing else 0, text, media_type, file_id,
             msg_date, 1 if edited else 0, int(time.time())),
        )
        await self.db.commit()

    async def get_message(
        self, business_connection_id: str, chat_id: int, message_id: int
    ) -> Optional[StoredMessage]:
        async with self.db.execute(
            """
            SELECT * FROM messages
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
            """,
            (business_connection_id, chat_id, message_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return StoredMessage(
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            chat_title=row["chat_title"],
            is_outgoing=bool(row["is_outgoing"]),
            text=row["text"],
            media_type=row["media_type"],
            file_id=row["file_id"],
            msg_date=row["msg_date"],
            edited=bool(row["edited"]),
        )

    async def count_messages(self, business_connection_id: str) -> int:
        async with self.db.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE business_connection_id = ?",
            (business_connection_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def purge_older_than(self, days: int) -> int:
        """Удаляет логи старше N дней. Возвращает число удалённых строк."""
        if days <= 0:
            return 0
        cutoff = int(time.time()) - days * 86400
        cur = await self.db.execute(
            "DELETE FROM messages WHERE created_at < ?", (cutoff,)
        )
        await self.db.commit()
        return cur.rowcount or 0
