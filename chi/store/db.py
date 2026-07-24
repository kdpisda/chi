"""SQLite store: one database per run, WAL mode, JSONL mirrors alongside."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    """UTC ISO-8601 timestamp with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Store:
    """Per-run SQLite store. Source of truth; mirrors are write-only copies."""

    def __init__(self, run_dir: Path, conn: sqlite3.Connection) -> None:
        self.run_dir = run_dir
        self.conn = conn

    @classmethod
    def open(cls, run_dir: Path) -> "Store":
        """Create/open the run store, applying schema and pragmas."""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "mirror").mkdir(exist_ok=True)
        conn = sqlite3.connect(run_dir / "chi.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA.read_text())
        conn.commit()
        return cls(run_dir, conn)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single statement and commit."""
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Read-only fetch."""
        return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()
