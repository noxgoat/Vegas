"""Validação dos conectores contra as fontes REAIS (rede necessária).

Execute com:  JURIS_TESTES_ONLINE=1 python -m pytest tests/test_conectores_online.py -v
"""
import os

import pytest

from app.collector import run_collection
from app.search import get_results, run_search_sync

pytestmark = pytest.mark.skipif(os.environ.get("JURIS_TESTES_ONLINE") != "1",
                                reason="Testes online desativados (defina JURIS_TESTES_ONLINE=1)")


def _sid(conn, slug):
    return conn.execute("SELECT id FROM sources WHERE slug = ?", (slug,)).fetchone()[0]


def test_tjsp_cjpg_real(conn):
    res = run_collection(_sid(conn, "tjsp-cjpg"), params={"query": '"defiro o pedido"', "max_paginas": 1})
    assert res["status"] in ("sucesso", "parcial") and res["docs_found"] == 10, res
    r = run_search_sync(conn, 'busque: "defiro o pedido"')
    # a busca do portal usa a mesma frase: todo documento recuperado deve ter a frase confirmada localmente
    assert r["stats"]["per_term"][0]["documents"] >= 8


def test_tjsp_dje_real(conn):
    res = run_collection(_sid(conn, "tjsp-dje"), params={"query": '"expedição de alvará"', "max_paginas": 1,
                                                         "data_inicio": "2025-07-21", "data_fim": "2025-07-22"})
    assert res["status"] in ("sucesso", "parcial") and res["docs_found"] >= 1, res
    r = run_search_sync(conn, 'busque: "expedição de alvará"')
    item = get_results(conn, r["run_id"], 0)["items"][0]
    assert item["occurrences"][0]["page"] == 1


def test_djen_real_ou_falha_registrada(conn):
    res = run_collection(_sid(conn, "djen-tjsp"), params={"max_paginas": 1})
    # Fora do Brasil a API responde 403: a coleta precisa ser registrada como FALHA, nunca como sucesso vazio.
    if res["status"] == "falha":
        assert "403" in (res["error"] or "") or "HTTP" in (res["error"] or "")
    else:
        assert res["docs_found"] > 0
