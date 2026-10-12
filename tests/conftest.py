import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JURIS_AGENDADOR", "0")

from app import db as dbmod  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """Banco novo e isolado, SEM o filtro de escopo (testa o motor e os conectores em geral)."""
    settings.data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "only_judge_acts", False)
    c = dbmod.init_db()
    c.execute("UPDATE sources SET enabled = 1, status = 'ativa' WHERE slug IN ('tjsp-cjpg', 'tjsp-dje')")
    yield c


@pytest.fixture()
def conn_escopo(tmp_path, monkeypatch):
    """Banco novo com a configuração padrão: somente despachos e decisões."""
    settings.data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "only_judge_acts", True)
    yield dbmod.init_db()


@pytest.fixture()
def upload_source(conn):
    return conn.execute("SELECT id FROM sources WHERE slug = 'arquivos-enviados'").fetchone()[0]


def add_doc(conn, source_id, ext_id, text, **meta):
    from app.extract import Extraction
    from app.ingest import DocMeta, save_document
    status = meta.pop("status", "completo")
    scope = meta.pop("text_scope", "integral")
    notes = meta.pop("notes", [])
    return save_document(conn, source_id, ext_id, Extraction(text=text, method="texto", status=status, notes=notes),
                         DocMeta(text_scope=scope, **meta))
