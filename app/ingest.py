"""Gravação de documentos: deduplicação, versões e índice."""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field

from .config import settings
from .db import now_iso
from .extract import Extraction, sha256
from .normalize import normalize, nfc

META_FIELDS = ["title", "url_publication", "url_original", "court", "organ", "act_type", "process_number",
               "judge", "date_available", "date_publication", "date_decision", "city", "act_kind"]


@dataclass
class DocMeta:
    title: str | None = None
    url_publication: str | None = None
    url_original: str | None = None
    court: str | None = None
    organ: str | None = None
    act_type: str | None = None          # somente quando informado pela própria fonte
    process_number: str | None = None
    judge: str | None = None
    date_available: str | None = None    # AAAA-MM-DD
    date_publication: str | None = None
    date_decision: str | None = None
    extra: dict = field(default_factory=dict)
    text_scope: str = "integral"         # integral | parcial (ex.: somente ementa)
    city: str | None = None              # município de SP (nome oficial do IBGE), se a fonte informar
    act_kind: str | None = None          # calculado a partir de act_type (ver app/acts.py)


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(nfc(text).split()).encode("utf-8")).hexdigest()


def store_blob(data: bytes) -> str:
    h = sha256(data)
    d = settings.blob_dir / h[:2]
    d.mkdir(parents=True, exist_ok=True)
    p = d / h
    if not p.exists():
        p.write_bytes(data)
    return h


def _index(conn: sqlite3.Connection, doc_id: int, text: str) -> None:
    conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (doc_id,))
    conn.execute("INSERT INTO doc_fts (rowid, norm, folded) VALUES (?,?,?)",
                 (doc_id, normalize(text).text, normalize(text, fold_accents=True).text))


def _canonical_for(conn: sqlite3.Connection, chash: str, exclude_id: int | None) -> int | None:
    row = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ? AND dup_of IS NULL AND id != ? ORDER BY id LIMIT 1",
        (chash, exclude_id or -1)).fetchone()
    return row[0] if row else None


def save_document(conn: sqlite3.Connection, source_id: int, external_id: str, ext: Extraction,
                  meta: DocMeta, blob: bytes | None = None, run_id: int | None = None) -> str:
    """Grava ou atualiza um documento. Retorna 'novo', 'atualizado' ou 'inalterado'."""
    from .acts import classify_act
    meta.act_kind = classify_act(meta.act_type)
    text = nfc(ext.text)
    chash = content_hash(text)
    blob_hash = store_blob(blob) if blob else None
    ts = now_iso()
    meta_values = {f: getattr(meta, f) for f in META_FIELDS}
    extra = json.dumps(meta.extra or {}, ensure_ascii=False, sort_keys=True)
    notes = "; ".join(ext.notes) if ext.notes else None
    pages = json.dumps(ext.pages) if ext.pages else None
    # Documento parcialmente extraído nunca é marcado como integral.
    scope = meta.text_scope if ext.status == "completo" else "parcial"

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM documents WHERE source_id = ? AND external_id = ?",
                           (source_id, external_id)).fetchone()
        if row is None:
            cols = ["source_id", "external_id", *META_FIELDS, "metadata_json", "text", "pages_json",
                    "content_hash", "blob_sha256", "extraction_method", "extraction_status",
                    "extraction_notes", "text_scope", "ocr_confidence", "version", "dup_of",
                    "first_seen_at", "last_collected_at", "collection_run_id"]
            vals = [source_id, external_id, *meta_values.values(), extra, text, pages, chash, blob_hash,
                    ext.method, ext.status, notes, scope, ext.ocr_confidence, 1,
                    _canonical_for(conn, chash, None), ts, ts, run_id]
            cur = conn.execute(f"INSERT INTO documents ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            doc_id = cur.lastrowid
            conn.execute("INSERT INTO document_versions (document_id, version, content_hash, text, metadata_json, "
                         "collected_at, diff_summary) VALUES (?,?,?,?,?,?,?)",
                         (doc_id, 1, chash, text, extra, ts, "versão inicial"))
            _index(conn, doc_id, text)
            result = "novo"
        else:
            doc_id = row["id"]
            text_changed = row["content_hash"] != chash or row["text"] != text
            meta_changed = any((row[f] or None) != (v or None) for f, v in meta_values.items()) \
                or row["metadata_json"] != extra or row["extraction_status"] != ext.status
            if text_changed:
                version = row["version"] + 1
                ratio = difflib.SequenceMatcher(None, row["text"][:20000], text[:20000]).quick_ratio()
                summary = (f"texto alterado: {len(row['text'])} -> {len(text)} caracteres; "
                           f"similaridade aproximada {ratio:.0%}")
                if row["content_hash"] == chash:
                    summary = "somente espaçamento/quebras de linha alterados (mesmo conteúdo textual)"
                conn.execute("INSERT INTO document_versions (document_id, version, content_hash, text, "
                             "metadata_json, collected_at, diff_summary) VALUES (?,?,?,?,?,?,?)",
                             (doc_id, version, chash, text, extra, ts, summary))
                conn.execute("UPDATE documents SET dup_of = NULL WHERE dup_of = ?", (doc_id,))
                dup = _canonical_for(conn, chash, doc_id)
                conn.execute(
                    f"UPDATE documents SET {', '.join(f + ' = ?' for f in META_FIELDS)}, metadata_json = ?, "
                    "text = ?, pages_json = ?, content_hash = ?, blob_sha256 = COALESCE(?, blob_sha256), "
                    "extraction_method = ?, extraction_status = ?, extraction_notes = ?, text_scope = ?, "
                    "ocr_confidence = ?, version = ?, dup_of = ?, last_collected_at = ?, collection_run_id = ? "
                    "WHERE id = ?",
                    [*meta_values.values(), extra, text, pages, chash, blob_hash, ext.method, ext.status, notes,
                     scope, ext.ocr_confidence, version, dup, ts, run_id, doc_id])
                _index(conn, doc_id, text)
                result = "atualizado"
            else:
                conn.execute(
                    f"UPDATE documents SET {', '.join(f + ' = ?' for f in META_FIELDS)}, metadata_json = ?, "
                    "extraction_status = ?, extraction_notes = ?, text_scope = ?, last_collected_at = ?, "
                    "blob_sha256 = COALESCE(?, blob_sha256) WHERE id = ?",
                    [*meta_values.values(), extra, ext.status, notes, scope, ts, blob_hash, doc_id])
                result = "atualizado" if meta_changed else "inalterado"
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return result


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.execute("COMMIT")
