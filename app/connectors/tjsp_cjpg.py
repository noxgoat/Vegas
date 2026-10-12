"""TJSP — Consulta de Julgados de 1º Grau (e-SAJ / CJPG).

Estrutura verificada em 12/10/2026:
- GET  https://esaj.tjsp.jus.br/cjpg/                   (abre sessão; cookies JSESSIONID)
- POST https://esaj.tjsp.jus.br/cjpg/pesquisar.do        (dadosConsulta.pesquisaLivre, tipoNumero=UNIFICADO,
                                                           dadosConsulta.dtInicio/dtFim dd/mm/aaaa,
                                                           dadosConsulta.ordenacao=DESC)
- GET  https://esaj.tjsp.jus.br/cjpg/trocarDePagina.do?pagina=N&conversationId=  (cabeçalho X-Requested-With)
- Cada resultado é <tr class="fundocinza1">; o inteiro teor completo está em um <div style="display: none;">.
- Documento original: obterArquivo.do?cdProcesso=..&cdForo=..&nmAlias=..&cdDocumento=.. (atributo name="a-b-c-d").
- Filtro por comarca: varasTreeSelection.values = códigos "foro-vara" (lista separada por vírgulas)
  obtidos em GET /cjpg/varasTreeSelect.do (árvore Comarca > Foro > Vara). Verificado: com as varas de
  Campinas, todos os resultados retornados são da comarca de Campinas.
Sem CAPTCHA na verificação. O portal pesquisa por palavras (aspas = frase, * = prefixo).
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..extract import Extraction, clean_text
from ..http import AccessError, ensure_ok
from ..ingest import DocMeta
from ..query_parser import EXACT, Term
from ..cities import find_city, fold
from .base import Connector, NotApplicable, Truncated, br_date_to_iso, iso_to_br

_TREE_CACHE: dict[str, list[str]] | None = None
NO_RESULTS = "Não foi encontrado nenhum resultado"
FILTER_RETRIES = 3
RETRY_DELAY = 2.0


class NoComarca(NotApplicable):
    pass


def parse_vara_tree(html: str | bytes) -> dict[str, list[str]]:
    """{comarca_dobrada: [códigos foro-vara]} a partir de varasTreeSelect.do."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, list[str]] = {}
    for li in soup.find_all("li"):
        if li.find_parent("li") is not None:
            continue
        node = li.find("span", class_="node", recursive=False)
        if node is None:
            continue
        leaves = [s["value"] for s in li.select("span.node.selectable") if s.get("value")]
        if leaves:
            out.setdefault(fold(node.get_text(strip=True)), []).extend(leaves)
    return out

BASE = "https://esaj.tjsp.jus.br/cjpg/"
TOTAL_RE = re.compile(r"Resultados\s+\d+\s+a\s+\d+\s+de\s+(\d+)")


def parse_results(html: str | bytes) -> tuple[int | None, list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    m = TOTAL_RE.search(text)
    total = int(m.group(1)) if m else None
    items = []
    for tr in soup.select("tr.fundocinza1"):
        anchor = tr.find("a", attrs={"name": True})
        name = anchor["name"] if anchor else None
        num = tr.select_one("span.fonteNegrito")
        labels = {}
        for td in tr.find_all("td"):
            strong = td.find("strong")
            if strong and strong.get_text(strip=True).endswith(":"):
                key = strong.get_text(strip=True).rstrip(":").strip()
                val = td.get_text(" ", strip=True)[len(strong.get_text(" ", strip=True)):].strip()
                labels[key] = val
        full = None
        visible = None
        for div in tr.find_all("div", attrs={"align": "justify"}):
            style = (div.get("style") or "").replace(" ", "").lower()
            if "display:none" in style:
                full = div
            elif visible is None:
                visible = div
        body = full or visible
        body_text = clean_text(body.get_text()) if body else ""  # <em> = destaque do portal
        items.append({
            "name": name, "processo": num.get_text(strip=True) if num else None, "labels": labels,
            "texto": body_text, "texto_integral": full is not None,
        })
    return total, items


def document_url(name: str | None) -> str | None:
    if not name:
        return None
    parts = name.split("-")
    if len(parts) < 4:
        return None
    q = urlencode({"cdProcesso": parts[0], "cdForo": parts[1], "nmAlias": parts[2], "cdDocumento": parts[3]})
    return f"{BASE}obterArquivo.do?{q}"


def item_to_doc(item: dict) -> tuple[str, Extraction, DocMeta]:
    lab = item["labels"]
    name = item["name"] or ""
    parts = name.split("-")
    external_id = parts[3] if len(parts) >= 4 and parts[3] else name or item["processo"]
    status = "completo" if item["texto"] else "falha"
    notes = [] if item["texto_integral"] else ["Inteiro teor completo não localizado; armazenado o trecho visível"]
    ext = Extraction(text=item["texto"], method="html", status=status, notes=notes)
    vara, foro = lab.get("Vara"), lab.get("Foro")
    meta = DocMeta(
        title=f"Sentença — Processo {item['processo']}" if item["processo"] else None,
        url_publication=None, url_original=document_url(name), court="TJSP",
        organ=" — ".join(x for x in [vara, foro] if x) or None,
        act_type="Sentença (Base de Julgados de 1º grau do TJSP)",
        process_number=item["processo"], judge=lab.get("Magistrado"),
        date_available=br_date_to_iso(lab.get("Data de Disponibilização")),
        extra={k: v for k, v in {"classe": lab.get("Classe"), "assunto": lab.get("Assunto"),
                                 "comarca": lab.get("Comarca"), "foro": foro, "vara": vara,
                                 "codigo_documento_esaj": name}.items() if v},
        text_scope="integral" if item["texto_integral"] else "parcial",
        city=find_city(lab.get("Comarca") or ""),
    )
    return external_id, ext, meta


class TjspCjpg(Connector):
    supports_query_discovery = True

    def portal_query(self, term: Term) -> str:
        if term.mode == EXACT:
            return f'"{term.raw.strip()}"'
        # Busca do portal é por palavras: a substring é enviada como prefixo.
        return term.raw.strip().rstrip("*") + "*"

    def _search_params(self, query: str, params: dict, varas: list[str] | None = None) -> dict:
        p = {"dadosConsulta.pesquisaLivre": query, "tipoNumero": "UNIFICADO", "dadosConsulta.ordenacao": "DESC",
             "dadosConsulta.dtInicio": iso_to_br(params.get("data_inicio")),
             "dadosConsulta.dtFim": iso_to_br(params.get("data_fim"))}
        if varas:
            p["varasTreeSelection.values"] = ",".join(varas)
        return p

    def _open(self, query: str, params: dict):
        http = self.ctx.http
        ensure_ok(http.get(BASE), "Abertura do formulário")
        varas = None
        if params.get("cidade"):
            varas = self.varas_da_comarca(params["cidade"])
            self.ctx.log("info", f"Filtro por comarca {params['cidade']}: {len(varas)} vara(s) selecionada(s) no portal.")
        # POST: a lista de varas de uma comarca grande não cabe numa URL (verificado em 12/10/2026).
        self._last_form = self._search_params(query, params, varas)
        return ensure_ok(http.post(BASE + "pesquisar.do", data=self._last_form), "Pesquisa")

    def varas_da_comarca(self, cidade: str) -> list[str]:
        """Códigos "foro-vara" de todas as varas da comarca, lidos da árvore do próprio portal."""
        global _TREE_CACHE
        if _TREE_CACHE is None:
            r = ensure_ok(self.ctx.http.get(BASE + "varasTreeSelect.do"), "Árvore de comarcas/varas")
            _TREE_CACHE = parse_vara_tree(r.text)
        varas = _TREE_CACHE.get(fold(cidade))
        if not varas:
            raise NoComarca(f"{cidade} não é sede de comarca na Consulta de Julgados do TJSP (o município "
                            "pertence a outra comarca); a busca desta fonte não foi feita.")
        return varas

    def collect(self, params: dict) -> None:
        query = (params.get("query") or "").strip()
        if not query:
            raise ValueError("A Consulta de Julgados do TJSP exige um termo de pesquisa (parâmetro 'query').")
        max_pages = int(params.get("max_paginas") or self.config.get("max_paginas") or 20)
        cp = self.ctx.get_checkpoint() or {}
        start_page = int(cp.get("page", 0)) + 1 if cp.get("query") == query else 1
        r = self._open(query, params)
        html = r.text
        total, items = parse_results(html)
        if total is None and not items and params.get("cidade") and NO_RESULTS in html:
            # Verificado em 12/10/2026: com filtro de varas, o portal às vezes responde "nenhum resultado"
            # por engano (geralmente na 1ª consulta da sessão); repetindo na mesma sessão, os resultados vêm.
            for attempt in range(1, FILTER_RETRIES + 1):
                self.ctx.log("aviso", f"Portal respondeu 'nenhum resultado' com filtro de comarca; repetindo "
                                      f"({attempt}/{FILTER_RETRIES}) porque essa resposta é instável.")
                time.sleep(RETRY_DELAY)
                r = ensure_ok(self.ctx.http.post(BASE + "pesquisar.do", data=self._last_form), "Pesquisa")
                html = r.text
                total, items = parse_results(html)
                if items or NO_RESULTS not in html:
                    break
            else:
                raise Truncated(f"O portal respondeu 'nenhum resultado' para {params['cidade']} em "
                                f"{FILTER_RETRIES + 1} tentativas. Como essa resposta do portal é instável com "
                                "filtro de comarca, a ausência de resultados NÃO foi confirmada.")
        if total is None and not items:
            if NO_RESULTS in html:
                self.ctx.log("info", "O portal informou: nenhum resultado para a consulta.")
                self.ctx.set_total(0)
                return
            aviso = BeautifulSoup(html, "lxml").select_one("div.aviso, #mensagemRetorno")
            msg = aviso.get_text(" ", strip=True) if aviso else "sem lista de resultados e sem aviso de 'nenhum resultado'"
            raise AccessError(f"Resposta inesperada do portal: {msg}")
        self.ctx.set_total(total)
        pages_total = ((total or 0) + 9) // 10
        page = start_page
        if start_page > 1:
            self.ctx.log("info", f"Retomando a partir da página {start_page} (checkpoint).")
            items = self._page(page)
        done = 0
        while True:
            self.ctx.page_examined(f"resultados página {page}")
            for it in items:
                ext_id, ext, meta = item_to_doc(it)
                self.ctx.save(ext_id, ext, meta)
            self.ctx.checkpoint({"query": query, "page": page})
            done += 1
            if page >= pages_total or not items:
                break
            if done >= max_pages:
                raise Truncated(f"Limite de {max_pages} página(s) atingido: {page} de {pages_total} página(s) "
                                f"examinadas ({total} resultado(s) no portal). Use 'Retomar' para continuar.")
            page += 1
            items = self._page(page)

    def _page(self, page: int) -> list[dict]:
        r = ensure_ok(self.ctx.http.get(BASE + "trocarDePagina.do", params={"pagina": page, "conversationId": ""},
                                        headers={"X-Requested-With": "XMLHttpRequest",
                                                 "Referer": BASE + "pesquisar.do"}), f"Página {page}")
        return parse_results(r.text)[1]

    def test_connection(self) -> dict:
        r = self._open('"defiro o pedido"', {})
        total, items = parse_results(r.text)
        return {"ok": bool(items), "http_status": r.status_code, "total_informado": total,
                "amostra": [{"processo": i["processo"], "texto_integral": i["texto_integral"],
                             "caracteres": len(i["texto"])} for i in items[:3]]}
