"""Conversa (interpretação), municípios de SP e filtro por cidade."""
import httpx
import pytest

from app.cities import city_in_text, find_city
from app.collector import run_collection
from app.connectors import djen, tjsp_cjpg
from app.interpret import InterpretError, interpret
from app.query_parser import EXACT, SUBSTRING, QuerySyntaxError
from app.search import SearchParams, run_search_sync

from conftest import add_doc


def terms(it):
    return [(t.raw, t.mode) for t in it.parsed.terms]


def test_conversa_frase_e_cidade():
    it = interpret('procure "defiro o pedido" em Campinas')
    assert terms(it) == [("defiro o pedido", EXACT)] and it.city == "Campinas"
    assert "procure" in it.ignored


def test_conversa_varios_termos_e_comarca():
    it = interpret("busque decisões com “tutela de urgência” e alvará na comarca de Santos?")
    assert terms(it) == [("tutela de urgência", EXACT), ("alvará", SUBSTRING)] and it.city == "Santos"


def test_cidade_sem_acento_e_composta():
    assert interpret("deferimento em sao jose do rio preto").city == "São José do Rio Preto"
    assert interpret("penhora cidade: Mogi-Guaçu").city == "Mogi Guaçu"


def test_cidade_inexistente_gera_erro_com_sugestao():
    with pytest.raises(InterpretError, match="Campinas"):
        interpret("procure alvará em Campinsa")


def test_aspas_protegem_em():
    it = interpret('busque "dano em ricochete"')
    assert terms(it) == [("dano em ricochete", EXACT)] and it.city is None


def test_modo_estrito_inalterado():
    it = interpret('busque: "defiro o pedido" defer')
    assert it.mode == "estrito" and terms(it) == [("defiro o pedido", EXACT), ("defer", SUBSTRING)]
    with pytest.raises(QuerySyntaxError):
        interpret('busque "defiro o pedido')


def test_termo_unico_comum_nao_e_descartado():
    assert terms(interpret("sentença")) == [("sentença", SUBSTRING)]


def test_municipios_ibge():
    assert find_city("CAMPINAS") == "Campinas" and find_city("Rio de Janeiro") is None
    assert city_in_text("1ª Vara Cível do Foro de São José dos Campos") == "São José dos Campos"
    assert city_in_text("Foro Regional de Santo Amaro") is None


def test_pesquisa_restrita_a_cidade(conn, upload_source):
    add_doc(conn, upload_source, "c", "Defiro o pedido.", city="Campinas")
    add_doc(conn, upload_source, "s", "Defiro o pedido.  ", city="Santos")
    add_doc(conn, upload_source, "x", "Defiro o pedido. Sem cidade.")
    r = run_search_sync(conn, 'busque: "defiro o pedido"', SearchParams(city="Campinas"))
    assert r["stats"]["matched_documents"] == 1
    r = run_search_sync(conn, 'busque: "defiro o pedido"')
    assert r["stats"]["matched_documents"] == 3


def test_djen_cidade_pelo_orgao():
    _, _, meta = djen.item_to_doc({"id": 1, "texto": "x", "nomeOrgao": "2ª Vara do Trabalho de Jundiaí"})
    assert meta.city == "Jundiaí"


TREE = """<div id="_treeSelectContainer"><ul>
<li><span class="node" value="10">Campinas</span><ul>
  <li><span class="node" value="84">Foro de Campinas</span><ul>
    <li class="leafItem"><span class="node selectable" value="84-1">1ª Vara Cível</span></li>
    <li class="leafItem"><span class="node selectable" value="84-2">2ª Vara Cível</span></li></ul></li></ul></li>
<li><span class="node" value="20">Santos</span><ul>
  <li class="leafItem"><span class="node selectable" value="562-1">1ª Vara</span></li></ul></li>
</ul></div>"""

RESULT = """<html><div>Resultados 1 a 1 de 1</div><table><tr class="fundocinza1"><td><table>
<tr><td><a name="P-84--55"><span class="fonteNegrito">1000000-00.2026.8.26.0114</span></a></td></tr>
<tr><td><strong>Comarca:</strong> Campinas</td></tr>
<tr><td><div align="justify" style="display: none;"><span>Defiro o pedido.</span></div></td></tr>
</table></td></tr></table></html>"""


def test_cjpg_envia_varas_da_comarca_e_repete_resposta_instavel(conn, monkeypatch):
    monkeypatch.setattr(tjsp_cjpg, "_TREE_CACHE", None)
    monkeypatch.setattr(tjsp_cjpg, "RETRY_DELAY", 0)
    sid = conn.execute("SELECT id FROM sources WHERE slug = 'tjsp-cjpg'").fetchone()[0]
    posts = []

    def handler(req):
        if req.url.path.endswith("varasTreeSelect.do"):
            return httpx.Response(200, text=TREE)
        if req.url.path.endswith("pesquisar.do"):
            form = dict(httpx.QueryParams(req.content.decode()))
            posts.append(form)
            if len(posts) == 1:     # 1ª resposta: falso "nenhum resultado" (comportamento real verificado)
                return httpx.Response(200, text="<html>Não foi encontrado nenhum resultado</html>")
            return httpx.Response(200, text=RESULT)
        return httpx.Response(200, text="<html>form</html>")

    res = run_collection(sid, params={"query": '"defiro o pedido"', "cidade": "Campinas"},
                         transport=httpx.MockTransport(handler))
    assert res["status"] == "sucesso" and res["docs_new"] == 1
    assert posts[0]["varasTreeSelection.values"] == "84-1,84-2" and len(posts) == 2
    assert conn.execute("SELECT city FROM documents").fetchone()[0] == "Campinas"


def test_cjpg_vazio_persistente_com_cidade_nao_vira_zero(conn, monkeypatch):
    monkeypatch.setattr(tjsp_cjpg, "_TREE_CACHE", None)
    monkeypatch.setattr(tjsp_cjpg, "RETRY_DELAY", 0)
    sid = conn.execute("SELECT id FROM sources WHERE slug = 'tjsp-cjpg'").fetchone()[0]

    def handler(req):
        if req.url.path.endswith("varasTreeSelect.do"):
            return httpx.Response(200, text=TREE)
        if req.url.path.endswith("pesquisar.do"):
            return httpx.Response(200, text="<html>Não foi encontrado nenhum resultado</html>")
        return httpx.Response(200, text="<html>form</html>")

    res = run_collection(sid, params={"query": "xyz*", "cidade": "Santos"}, transport=httpx.MockTransport(handler))
    assert res["status"] == "parcial" and "NÃO foi confirmada" in res["message"]


def test_cjpg_cidade_sem_comarca_nao_aplicavel(conn, monkeypatch):
    monkeypatch.setattr(tjsp_cjpg, "_TREE_CACHE", None)
    sid = conn.execute("SELECT id FROM sources WHERE slug = 'tjsp-cjpg'").fetchone()[0]
    t = httpx.MockTransport(lambda req: httpx.Response(200, text=TREE if "varasTree" in req.url.path else "<html/>"))
    res = run_collection(sid, params={"query": "x*", "cidade": "Valinhos"}, transport=t)
    assert res["status"] == "nao_aplicavel" and "não é sede de comarca" in res["message"]
    assert conn.execute("SELECT status FROM sources WHERE id = ?", (sid,)).fetchone()[0] == "ativa"


def test_api_ask(conn):
    from fastapi.testclient import TestClient
    from app.api import app
    with TestClient(app) as c:
        r = c.post("/api/ask", json={"message": "procure alvará em Campinsa"})
        assert r.status_code == 400 and "Campinas" in r.json()["detail"]["erro"]
        r = c.post("/api/ask", json={"message": 'procure "defiro o pedido" em Campinas', "online": False})
        assert r.status_code == 200 and r.json()["interpretation"]["cidade"] == "Campinas"
        run = c.get(f"/api/searches/{r.json()['run_id']}").json()
        assert run["params"]["city"] == "Campinas" and run["status"] == "concluida"


def test_data_na_pergunta_intervalo():
    from app.interpret import interpret
    it = interpret('procure "defiro o pedido" de 01/09/2026 a 30/09/2026 em Campinas')
    assert (it.date_from, it.date_to, it.city) == ("2026-09-01", "2026-09-30", "Campinas")


def test_data_dentro_de_aspas_nao_e_filtro():
    from app.interpret import interpret
    it = interpret('"prazo 10/10/2026" em 05/10/2026')
    assert (it.date_from, it.date_to) == ("2026-10-05", "2026-10-05")
    assert it.parsed.terms[0].raw == "prazo 10/10/2026"


def test_lista_de_municipios_existe():
    from app.cities import municipalities
    assert len(municipalities()) == 645
