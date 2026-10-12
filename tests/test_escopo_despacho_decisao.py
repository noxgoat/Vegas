"""Escopo padrão: somente despachos e decisões de magistrado (tipo declarado pela fonte)."""
import httpx

from app.acts import classify_act
from app.collector import run_collection
from app.connectors import djen
from app.search import SearchParams, get_results, run_search_sync

from conftest import add_doc


def test_classificacao_pelo_tipo_declarado():
    assert classify_act("Despacho") == "despacho"
    assert classify_act("DECISÃO INTERLOCUTÓRIA") == "decisao"
    assert classify_act("Decisão Monocrática") == "decisao"
    assert classify_act("Sentença") == "sentenca"
    assert classify_act("Acórdão") == "acordao"
    assert classify_act("Intimação") == "outro" and classify_act("Ato Ordinatório") == "outro"
    assert classify_act(None) is None


def test_somente_despachos_e_decisoes_aparecem(conn_escopo):
    conn = conn_escopo
    up = conn.execute("SELECT id FROM sources WHERE slug = 'arquivos-enviados'").fetchone()[0]
    add_doc(conn, up, "1", "Defiro o pedido. (despacho)", act_type="Despacho")
    add_doc(conn, up, "2", "Defiro o pedido. (decisão)", act_type="Decisão")
    add_doc(conn, up, "3", "Defiro o pedido. (sentença)", act_type="Sentença")
    add_doc(conn, up, "4", "Defiro o pedido. (intimação)", act_type="Intimação")
    add_doc(conn, up, "5", "Defiro o pedido. (sem tipo)")
    r = run_search_sync(conn, 'busque: "defiro o pedido"')
    kinds = sorted(i["act_kind"] for i in get_results(conn, r["run_id"], 0)["items"])
    assert kinds == ["decisao", "despacho"]
    cov = next(c for c in r["coverage"] if c["source_id"] == up)
    assert cov["despachos_e_decisoes"] == 2


def test_fontes_so_de_sentencas_ficam_desativadas(conn_escopo):
    rows = dict(conn_escopo.execute("SELECT slug, enabled FROM sources").fetchall())
    assert rows["tjsp-cjpg"] == 0 and rows["tjsp-dje"] == 0
    assert rows["djen-tjsp"] == 1 and rows["djen-trt15"] == 1


def test_fonte_sem_despachos_explica_no_relatorio(conn_escopo):
    conn = conn_escopo
    up = conn.execute("SELECT id FROM sources WHERE slug = 'arquivos-enviados'").fetchone()[0]
    add_doc(conn, up, "s", "Julgo procedente.", act_type="Sentença")
    r = run_search_sync(conn, "busque: procedente")
    cov = next(c for c in r["coverage"] if c["source_id"] == up)
    assert r["stats"]["total_occurrences"] == 0 and cov["pesquisada_no_acervo"] is False
    assert "fora do escopo" in cov["mensagem"]


def test_djen_coleta_tudo_mas_so_despacho_e_decisao_sao_pesquisados(conn_escopo):
    conn = conn_escopo
    sid = conn.execute("SELECT id FROM sources WHERE slug = 'djen-tjsp'").fetchone()[0]
    tipos = ["Despacho", "Decisão", "Sentença", "Intimação", "Edital"]
    items = [{"id": i, "texto": f"Defiro o pedido {i}.", "tipoDocumento": t, "siglaTribunal": "TJSP",
              "nomeOrgao": "1ª Vara Cível de Campinas", "data_disponibilizacao": "2026-10-09"}
             for i, t in enumerate(tipos)]
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        if "texto" in req.url.params:       # simula API que recusa o parâmetro não documentado
            return httpx.Response(400, json={"message": "parâmetro inválido"})
        return httpx.Response(200, json={"count": len(items), "items": items})

    res = run_collection(sid, params={"data_inicio": "2026-10-09", "data_fim": "2026-10-09", "texto": "defiro"},
                         transport=httpx.MockTransport(handler))
    assert res["status"] == "sucesso" and res["docs_new"] == 5
    assert "texto" in seen[0] and "texto" not in seen[-1]
    r = run_search_sync(conn, 'busque: "defiro o pedido"', SearchParams(city="Campinas"))
    assert r["stats"]["matched_documents"] == 2
