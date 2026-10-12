"""Acesso ao SQLite (WAL), migrações e utilitários."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_local = threading.local()
_init_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = str(path or settings.db_path)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_conn() -> sqlite3.Connection:
    """Uma conexão por thread, para o banco configurado."""
    key = str(settings.db_path)
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    if key not in conns:
        conns[key] = connect(key)
    return conns[key]


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    done = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.name in applied:
            continue
        conn.execute("BEGIN")
        try:
            for stmt in _split_sql(f.read_text(encoding="utf-8")):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (f.name, now_iso()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        done.append(f.name)
    return done


def _split_sql(script: str) -> list[str]:
    stmts, buf = [], []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            s = "\n".join(buf).strip()
            if s and sqlite3.complete_statement(s):
                stmts.append(s)
                buf = []
    rest = "\n".join(buf).strip()
    if rest:
        stmts.append(rest)
    return stmts


def init_db() -> sqlite3.Connection:
    with _init_lock:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.blob_dir.mkdir(parents=True, exist_ok=True)
        conn = get_conn()
        applied = migrate(conn)
        if "002_cidade.sql" in applied:
            backfill_city(conn)
        if "003_tipo_de_ato.sql" in applied:
            from .acts import classify_act
            for r in conn.execute("SELECT id, act_type FROM documents").fetchall():
                conn.execute("UPDATE documents SET act_kind = ? WHERE id = ?", (classify_act(r["act_type"]), r["id"]))
        from .sources_seed import seed_sources
        seed_sources(conn)
        return conn


def derive_city(meta_json: str | None, organ: str | None) -> str | None:
    from .cities import city_in_text, find_city
    try:
        extra = json.loads(meta_json or "{}")
    except ValueError:
        extra = {}
    for key in ("comarca", "cidade", "municipio", "nomeComarca"):
        if isinstance(extra.get(key), str) and find_city(extra[key]):
            return find_city(extra[key])
    return city_in_text(organ)


def backfill_city(conn: sqlite3.Connection) -> int:
    n = 0
    for r in conn.execute("SELECT id, metadata_json, organ FROM documents WHERE city IS NULL").fetchall():
        c = derive_city(r["metadata_json"], r["organ"])
        if c:
            conn.execute("UPDATE documents SET city = ? WHERE id = ?", (c, r["id"]))
            n += 1
    return n


def audit(conn: sqlite3.Connection, action: str, detail: dict | str | None = None, actor: str = "usuario") -> None:
    if isinstance(detail, dict):
        detail = json.dumps(detail, ensure_ascii=False)
    conn.execute("INSERT INTO audit_log (at, actor, action, detail) VALUES (?,?,?,?)",
                 (now_iso(), actor, action, detail))


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def apply_retention(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM search_runs WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)",
                 (f"-{settings.retention_days_searches} days",))
    conn.execute("DELETE FROM collection_logs WHERE at < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)",
                 (f"-{settings.retention_days_logs} days",))
