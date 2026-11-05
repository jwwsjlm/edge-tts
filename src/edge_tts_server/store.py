"""Small SQLite store for channels and recoverable, browser-private jobs."""

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .config import ServerConfig

TERMINAL_STATES = ("completed", "failed", "cancelled", "interrupted")


class Store:
    """Keep metadata transactions short; audio lives in the adjacent jobs directory."""

    def __init__(self, root: Path, config: ServerConfig) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(root / "workspace.sqlite3")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        if self.connection.execute("PRAGMA user_version").fetchone()[0] > 1:
            raise ValueError("Workspace database was created by a newer server")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS channels (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL,
                payload TEXT NOT NULL, channel TEXT NOT NULL,
                segments TEXT NOT NULL DEFAULT '[]', total_segments INTEGER NOT NULL,
                error TEXT, audio_bytes INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS jobs_owner_created ON jobs(owner, created_at DESC);
            CREATE INDEX IF NOT EXISTS jobs_expiry ON jobs(expires_at);
            PRAGMA user_version=1;
        """)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO settings VALUES ('retention_days', ?)",
                (str(config.job_retention_days),),
            )
            if not self.connection.execute(
                "SELECT 1 FROM settings WHERE key='initialized'"
            ).fetchone():
                self.save_channel(
                    {
                        "id": "edge-default",
                        "name": "Microsoft Edge",
                        "provider": "edge",
                        "enabled": True,
                        "api_key": None,
                        "base_url": None,
                        "proxy": config.proxy,
                        "default_voice": "zh-CN-XiaoxiaoNeural",
                        "default_mode": "preset",
                    }
                )
                self.save_channel(
                    {
                        "id": "mimo-default",
                        "name": "小米 MiMo",
                        "provider": "mimo",
                        "enabled": bool(config.mimo_api_key),
                        "api_key": config.mimo_api_key,
                        "base_url": config.mimo_base_url,
                        "proxy": config.proxy,
                        "default_voice": "mimo_default",
                        "default_mode": "preset",
                    }
                )
                self.connection.execute(
                    "INSERT INTO settings VALUES ('initialized', '1')"
                )

    def close(self) -> None:
        """Flush committed metadata on application shutdown."""
        self.connection.close()

    def channels(self) -> list[dict[str, Any]]:
        """Read administrator channel snapshots."""
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT data FROM channels ORDER BY rowid"
            )
        ]

    def channel(self, channel_id: str) -> dict[str, Any] | None:
        """Return one channel, including its backend-only credentials."""
        row = self.connection.execute(
            "SELECT data FROM channels WHERE id=?", (channel_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_channel(self, channel: dict[str, Any]) -> dict[str, Any]:
        """Atomically replace a channel; task snapshots remain unchanged."""
        channel = {
            **channel,
            "id": channel.get("id") or uuid.uuid4().hex,
            "updated_at": time.time(),
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO channels VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (channel["id"], json.dumps(channel, ensure_ascii=False)),
            )
        return channel

    def delete_channel(self, channel_id: str) -> None:
        """Delete only the catalog entry, never an in-flight task configuration."""
        with self.connection:
            self.connection.execute("DELETE FROM channels WHERE id=?", (channel_id,))

    def retention_days(self) -> int:
        """Read the current retention policy."""
        return int(
            self.connection.execute(
                "SELECT value FROM settings WHERE key='retention_days'"
            ).fetchone()[0]
        )

    def set_retention_days(self, days: int) -> None:
        """Apply retention to future completions without unexpectedly deleting old work."""
        with self.connection:
            self.connection.execute(
                "UPDATE settings SET value=? WHERE key='retention_days'", (str(days),)
            )

    def create_job(
        self,
        job_id: str,
        owner: str,
        payload: dict[str, Any],
        channel: dict[str, Any],
        total: int,
    ) -> dict[str, Any]:
        """Commit a task only after its input files have been written successfully."""
        now = time.time()
        with self.connection:
            self.connection.execute(
                "INSERT INTO jobs (id,owner,status,created_at,updated_at,"
                "payload,channel,total_segments) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    owner,
                    "queued",
                    now,
                    now,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(channel, ensure_ascii=False),
                    total,
                ),
            )
        result = self.job(job_id)
        assert result is not None
        return result

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("payload", "channel", "segments"):
            result[key] = json.loads(result[key])
        return result

    def job(self, job_id: str, owner: str | None = None) -> dict[str, Any] | None:
        """Look up a task with optional ownership enforcement in the same query."""
        if owner is None:
            row = self.connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)
            ).fetchone()
        return self._job(row) if row else None

    def jobs(self, owner: str, offset: int = 0) -> list[dict[str, Any]]:
        """Paginate each visitor's history without revealing other visitors' jobs."""
        rows = self.connection.execute(
            "SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT 50 OFFSET ?",
            (owner, offset),
        )
        return [self._job(row) for row in rows]

    def pending_count(self) -> int:
        """Bound waiting work by server capacity, without per-user quotas."""
        return int(
            self.connection.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running','merging')"
            ).fetchone()[0]
        )

    def update_job(self, job_id: str, **values: Any) -> None:
        """Persist progress after each durable segment."""
        allowed = {
            "status",
            "segments",
            "error",
            "audio_bytes",
            "duration_ms",
            "expires_at",
        }
        if set(values) - allowed:
            raise ValueError("Unknown job metadata field")
        if "segments" in values:
            values["segments"] = json.dumps(values["segments"], ensure_ascii=False)
        if values.get("status") in TERMINAL_STATES:
            values["expires_at"] = time.time() + self.retention_days() * 86400
        values["updated_at"] = time.time()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connection:
            self.connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?", (*values.values(), job_id)
            )

    def recover(self) -> None:
        """Require explicit retry after restart, avoiding unrequested duplicate synthesis."""
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status='interrupted', error=?, updated_at=?, expires_at=? "
                "WHERE status IN ('queued','running','merging')",
                (
                    "服务已重启，已完成的片段已保存，可继续生成。",
                    time.time(),
                    time.time() + self.retention_days() * 86400,
                ),
            )

    def expired_jobs(self) -> list[str]:
        """Return only finished jobs whose announced retention period has elapsed."""
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT id FROM jobs WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (time.time(),),
            )
        ]

    def delete_job(self, job_id: str) -> None:
        """Remove metadata after associated files have been removed."""
        with self.connection:
            self.connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
