"""Os 15 testes obrigatórios da especificação (seção 21), com documentos sintéticos."""
import io

import httpx
import pytest

from app.collector import run_collection
from app.extract import pdf_to_text
from app.normalize import normalize
from app.query_parser import EXACT, SUBSTRING, QuerySyntaxError, parse
from app.search import SearchParams, find_matches, get_results, run_search_sync

from conftest import add_doc


def matches(text, term, mode):
    return find_matches(normalize(text).text, normalize(term).text.strip(), mode)


# Teste 1
def test_01_frase_exata_corresponde_sequencia_consecutiva():
    assert len(matches("Vistos. Defiro o pedido de tutela.", "defiro o pedido", EXACT)) == 1


# Teste 2
def test_02_frase_exata_nao_aceita_palavras_intermediarias():
    for t in ["Defiro integralmente o pedido.", "Defiro, após análise, o pedido.", "O pedido foi deferido.",
              "Indefiro o pedido."]:
        assert matches(t, "defiro o pedido", EXACT) == [], t
    # ...mas corresponde se a sequência exata aparecer em outra parte do documento
    assert len(matches("Defiro integralmente o pedido. Ao final, defiro o pedido.", "defiro o pedido", EXACT)) == 1


# Teste 3
def test_03_substring_defer_em_deferimento():
    assert len(matches("Houve o deferimento.", "defer", SUBSTRING)) == 1


# Teste 4
def test_04_substring_defer_em_indeferimento():
    assert len(matches("Houve o indeferimento.", "defer", SUBSTRING)) == 1


# Teste 5
def test_05_substring_inicio_meio_e_final_de_palavras():
    text = "deferido; indeferido; aquidefer"
    found = matches(text, "defer", SUBSTRING)
    assert [text[a:b] for a, b in found] == ["defer", "defer", "defer"]
    starts = [a for a, _ in found]
    assert starts == [0, text.index("indeferido") + 2, text.index("aquidefer") + 4]
    # todas as ocorrências, não só a primeira
    assert len(matches("defer defer defer", "defer", SUBSTRING)) == 3


# Teste 6
def test_06_dois_termos_geram_duas_pesquisas_independentes():
    pq = parse("busque: defer indefer")
    assert [(t.raw, t.mode) for t in pq.terms] == [("defer", SUBSTRING), ("indefer", SUBSTRING)]
    pq = parse('busque: "defiro o pedido" indeferimento sentença')
    assert [(t.raw, t.mode) for t in pq.terms] == [
        ("defiro o pedido", EXACT), ("indeferimento", SUBSTRING), ("sentença", SUBSTRING)]


# Teste 7
def test_07_documento_em_dois_grupos_conta_uma_vez(conn, upload_source):
    add_doc(conn, upload_source, "a", "Defiro o pedido. Ademais, o indeferimento anterior fica revogado.")
    add_doc(conn, upload_source, "b", "Nada relevante aqui.")
    r = run_search_sync(conn, 'busque: "defiro o pedido" indeferimento')
    st = r["stats"]
    assert [t["documents"] for t in st["per_term"]] == [1, 1]
    assert st["total_results"] == 2
    assert st["unique_documents"] == 1
    assert st["total_occurrences"] == 2


# Teste 8
def test_08_palavra_somente_no_corpo(conn, upload_source):
    corpo = "Cabeçalho\n" + ("texto de preenchimento " * 400) + "\nA expressão levantamento de valores aparece só aqui."
    add_doc(conn, upload_source, "x", corpo, title="Despacho sem a palavra no título")
    r = run_search_sync(conn, 'busque: "levantamento de valores"')
    assert r["stats"]["per_term"][0]["documents"] == 1


# Teste 9
def test_09_pdf_digitalizado_processado_por_ocr():
    pytest.importorskip("pytesseract")
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont
    from app.extract import _tesseract_available
    if not _tesseract_available():
        pytest.skip("Tesseract não instalado")
    img = Image.new("RGB", (1700, 500), "white")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
    d = ImageDraw.Draw(img)
    d.text((60, 80), "DESPACHO", fill="black", font=font)
    d.text((60, 200), "Defiro o pedido de expedição de alvará.", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=850, height=250)
    page.insert_image(page.rect, stream=buf.getvalue())   # PDF só com imagem (sem camada de texto)
    pdf = doc.tobytes()
    assert pymupdf.open(stream=pdf, filetype="pdf")[0].get_text().strip() == ""
    ext = pdf_to_text(pdf)
    assert ext.method == "ocr"
    assert ext.pages == [[1, 0, len(ext.text)]]
    assert "defiro o pedido" in normalize(ext.text).text
    assert ext.status in ("completo", "parcial")


# Teste 10
def test_10_falha_de_coleta_nao_vira_ausencia_de_resultados(conn):
    sid = conn.execute("SELECT id FROM sources WHERE slug = 'djen-tjsp'").fetchone()[0]
    transport = httpx.MockTransport(lambda req: httpx.Response(403, text="The Amazon CloudFront distribution is "
                                                                         "configured to block access from your country"))
    res = run_collection(sid, params={"data_inicio": "2026-10-01", "data_fim": "2026-10-01"}, transport=transport)
    assert res["status"] == "falha"
    assert "403" in res["error"]
    src = conn.execute("SELECT status, last_success_at FROM sources WHERE id = ?", (sid,)).fetchone()
    assert src["status"] == "falha_acesso" and src["last_success_at"] is None
    r = run_search_sync(conn, "busque: deferimento")
    cov = next(c for c in r["coverage"] if c["source_id"] == sid)
    assert cov["pesquisada_no_acervo"] is False
    assert any("FALHOU" in a for a in cov["avisos"])
    assert "NÃO significa ausência" in cov["mensagem"]


# Teste 11
def test_11_documento_parcial_nao_e_integralmente_verificado(conn, upload_source):
    add_doc(conn, upload_source, "p", "Defiro o pedido.", status="parcial", notes=["OCR com baixa confiança"])
    add_doc(conn, upload_source, "q", "Defiro o pedido. Só a ementa.", text_scope="parcial")
    add_doc(conn, upload_source, "i", "Defiro o pedido. Inteiro teor.")
    r = run_search_sync(conn, 'busque: "defiro o pedido"')
    items = {i["id"]: i for i in get_results(conn, r["run_id"], 0)["items"]}
    status = sorted(i["verification_status"] for i in items.values())
    assert status == ["Integralmente verificado", "Parcial", "Parcial"]


# Teste 12
def test_12_sem_resultados_informa_fontes_pesquisadas(conn, upload_source):
    add_doc(conn, upload_source, "a", "Texto qualquer.")
    r = run_search_sync(conn, 'busque: "expressão inexistente"')
    assert r["stats"]["total_occurrences"] == 0
    cov = {c["source_id"]: c for c in r["coverage"]}
    assert cov[upload_source]["pesquisada_no_acervo"] is True
    assert cov[upload_source]["documentos_no_acervo"] == 1
    cjsg = next(c for c in r["coverage"] if c["name"].startswith("TJSP — Jurisprudência de 2º Grau"))
    assert cjsg["pesquisada_no_acervo"] is False and "Não pesquisada" in cjsg["mensagem"]


# Teste 13
@pytest.mark.parametrize("cmd", ['busque: "defiro o pedido', 'busque: defer "tutela', "busque: “aberta", 'busque: ""'])
def test_13_aspas_nao_fechadas_geram_erro(cmd):
    with pytest.raises(QuerySyntaxError):
        parse(cmd)


# Teste 14
def test_14_posicoes_coerentes_com_texto_original(conn, upload_source):
    text = ("Vistos.\r\nDEFIRO   o\n PEDIDO de tutela; o indefe-\nrimento anterior fica revogado. "
            "Defiro o pedido.")
    add_doc(conn, upload_source, "pos", text)
    stored = conn.execute("SELECT text FROM documents").fetchone()[0]
    r = run_search_sync(conn, 'busque: "defiro o pedido" deferimento')
    for term_index, term in [(0, "defiro o pedido"), (1, "deferimento")]:
        items = get_results(conn, r["run_id"], term_index)["items"]
        assert items
        for o in items[0]["occurrences"]:
            frag = stored[o["start_pos"]:o["end_pos"]]
            assert frag == o["match_text"]
            assert normalize(frag).text == term
    occ = get_results(conn, r["run_id"], 0)["items"][0]["occurrences"]
    assert [o["match_text"] for o in occ] == ["DEFIRO   o\n PEDIDO", "Defiro o pedido"]
    hyph = get_results(conn, r["run_id"], 1)["items"][0]["occurrences"][0]["match_text"]
    assert hyph == "defe-\nrimento"


# Teste 15
@pytest.mark.parametrize("sort", ["relevancia", "data", "data_asc", "fonte"])
def test_15_paginacao_sem_duplicacoes(conn, upload_source, sort):
    for i in range(47):
        add_doc(conn, upload_source, f"d{i}", "defer " * (i % 5 + 1), date_publication=f"2026-01-{i % 28 + 1:02d}")
    r = run_search_sync(conn, "busque: defer")
    seen = []
    page = 1
    while True:
        res = get_results(conn, r["run_id"], 0, page=page, page_size=10, sort=sort)
        seen += [i["id"] for i in res["items"]]
        if page >= res["pages"]:
            break
        page += 1
    assert len(seen) == 47 and len(set(seen)) == 47
    assert res["total"] == 47
