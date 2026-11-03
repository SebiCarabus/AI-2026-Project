"""PostgreSQL persistence for compressed simulation snapshots."""

from __future__ import annotations

import os
import zlib
from datetime import datetime
from typing import Any, Callable

import psycopg


class SnapshotNotFound(LookupError):
    """No saved snapshot has the requested identifier."""


class SnapshotStore:
    """Small Postgres repository; state payloads are compressed as opaque bytes."""

    def __init__(self, database_url: str | None = None, connect: Callable[..., Any] | None = None):
        self.database_url = database_url
        self._connect = connect or psycopg.connect

    def _connection(self):
        database_url = self.database_url or os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required for snapshot storage")
        return self._connect(database_url)

    def ensure_schema(self) -> None:
        """Create the additive snapshot table/index on both fresh and existing volumes."""
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS snapshots (
                      snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                      config_hash TEXT NOT NULL,
                      config_yaml TEXT NOT NULL,
                      preset TEXT NOT NULL,
                      display_name TEXT NOT NULL,
                      width BIGINT NULL CHECK (width IS NULL OR width > 0),
                      height BIGINT NULL CHECK (height IS NULL OR height > 0),
                      state_counts BIGINT[] NOT NULL,
                      generation BIGINT NOT NULL CHECK (generation >= 0),
                      state_encoding TEXT NOT NULL,
                      state_blob BYTEA NOT NULL,
                      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_snapshots_config_hash ON snapshots USING btree (config_hash)"
                )
                cursor.execute("ALTER TABLE snapshots ALTER COLUMN width TYPE BIGINT")
                cursor.execute("ALTER TABLE snapshots ALTER COLUMN height TYPE BIGINT")
                cursor.execute("ALTER TABLE snapshots ALTER COLUMN state_blob SET STORAGE EXTERNAL")

    def save(self, snapshot: dict[str, Any], display_name: str) -> dict[str, Any]:
        self.ensure_schema()
        encoding, compressed = compress_state(snapshot["state_format"], snapshot["state_blob"])
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO snapshots (
                      config_hash, config_yaml, preset, display_name, width, height,
                      state_counts, generation, state_encoding, state_blob
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING snapshot_id, config_hash, preset, display_name, width,
                              height, state_counts, generation, created_at
                    """,
                    (
                        snapshot["config_hash"], snapshot["config_yaml"], snapshot["preset"],
                        display_name, snapshot["width"], snapshot["height"], snapshot["counts"],
                        snapshot["generation"], encoding, compressed,
                    ),
                )
                row = cursor.fetchone()
        return _metadata(row)

    def list(self, config_hash: str | None = None) -> list[dict[str, Any]]:
        self.ensure_schema()
        sql = """
            SELECT snapshot_id, config_hash, preset, display_name, width, height,
                   state_counts, generation, created_at
            FROM snapshots
        """
        values: tuple[Any, ...] = ()
        if config_hash is not None:
            sql += " WHERE config_hash = %s"
            values = (config_hash,)
        sql += " ORDER BY created_at DESC, snapshot_id DESC"
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, values)
                rows = cursor.fetchall()
        return [_metadata(row) for row in rows]

    def get(self, snapshot_id: int) -> dict[str, Any]:
        self.ensure_schema()
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT config_hash, config_yaml, preset, width, height, state_counts,
                           generation, state_encoding, state_blob
                    FROM snapshots WHERE snapshot_id = %s
                    """,
                    (snapshot_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise SnapshotNotFound(snapshot_id)
        config_hash, config_yaml, preset, width, height, counts, generation, encoding, blob = row
        state_format, state_blob = decompress_state(encoding, blob)
        return {
            "config_hash": config_hash,
            "config_yaml": config_yaml,
            "preset": preset,
            "width": width,
            "height": height,
            "counts": list(counts),
            "generation": generation,
            "state_format": state_format,
            "state_blob": state_blob,
        }


def compress_state(state_format: str, state_blob: bytes) -> tuple[str, bytes]:
    """Wrap an opaque service payload with a versionable compression encoding tag."""
    if not isinstance(state_format, str) or not state_format:
        raise ValueError("state_format must be a non-empty string")
    if not isinstance(state_blob, bytes):
        raise ValueError("state_blob must be bytes")
    return f"zlib:{state_format}", zlib.compress(state_blob)


def decompress_state(encoding: str, blob: bytes) -> tuple[str, bytes]:
    if not encoding.startswith("zlib:"):
        raise ValueError(f"unsupported snapshot state encoding {encoding!r}")
    return encoding[len("zlib:"):], zlib.decompress(blob)


def _metadata(row: tuple[Any, ...]) -> dict[str, Any]:
    snapshot_id, config_hash, preset, display_name, width, height, counts, generation, created_at = row
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {
        "id": snapshot_id,
        "config_hash": config_hash,
        "preset": preset,
        "display_name": display_name,
        "width": width,
        "height": height,
        "counts": list(counts),
        "generation": generation,
        "created_at": created_at,
    }
