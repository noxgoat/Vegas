"""TJSP — Diário da Justiça Eletrônico (cdje), acervo histórico.

Estrutura verificada em 12/10/2026:
- GET  https://dje.tjsp.jus.br/cdje/index.do                      (sessão)
- POST https://dje.tjsp.jus.br/cdje/consultaAvancada.do            (dadosConsulta.pesquisaLivre,
       dadosConsulta.dtInicio/dtFim dd/mm/aaaa, dadosConsulta.cdCaderno (-11 = todos), pagina)
- POST https://dje.tjsp.jus.br/cdje/trocaDePagina.do  pagina=N     (paginação, 10 por página)
- Cada resultado (<tr class="ementaClass">) aponta para uma PÁGINA do diário:
  consultaSimples.do?cdVolume=..&nuDiario=..&cdCaderno=..&nuSeqpagina=..
- PDF da página: getPaginaDoDiario.do?cdVolume=..&nuDiario=..&cdCaderno=..&nuSeqpagina=..
A última edição disponível é de 22/07/2025 (o portal limita as datas a esse dia).
O PDF de cada página é baixado e extraído INTEGRALMENTE (texto digital ou OCR).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import parse_qs

from bs4 import BeautifulSoup

from ..extract import pdf_to_text
from ..http import AccessError, ensure_ok
from ..ingest import DocMeta
from .base import Connector, Truncated, iso_to_br

BASE = "https://dje.tjsp.jus.br/cdje/"
TOTAL_RE = re.compile(r"Resultados\s+\d+\s+a\s+\d+\s+de\s+(\d+)")
POPUP_RE = re.compile(r"consultaSimples\.do\?([^'\"]+)")
DATE_RE = re.compile(r"\w{3}\s+(\w{3})\s+(\d{1,2})\s+[\d:]+\s+\S+\s+(\d{4})")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                      "Nov", "Dec"], start=1)}
LAST_EDITION = "2025-07-22"


def parse_results(html: str | bytes) -> tuple[int | None, list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    m = TOTAL_RE.search(text)
    total = int(m.group(1)) if m else None
    items, seen = [], set()
    for tr in soup.select("tr.ementaClass"):
        a = tr.find("a", onclick=POPUP_RE)
        if not a:
            continue
        qs = POPUP_RE.search(a["onclick"]).group(1).replace("&amp;", "&")
        q = {k: v[0] for k, v in parse_qs(qs).items()}
        key = "-".join(q.get(k, "") for k in ("cdVolume", "nuDiario", "cdCaderno", "nuSeqpagina"))
        if key in seen:
            continue
        seen.add(key)
        label = a.get_text(" ", strip=True)
        dm = DATE_RE.search(label)
        d = None
        if dm and dm.group(1) in MONTHS:
            d = date(int(dm.group(3)), MONTHS[dm.group(1)], int(dm.group(2))).isoformat()
        caderno = label.split(" - ", 1)[1] if " - " in label else label
        items.append({"key": key, "query": q, "label": label, "date": d, "caderno": caderno})
    return total, items


class TjspDje(Connector):
    supports_query_discovery = True

    def _form(self, query: str, params: dict, window: tuple[str, str]) -> dict:
        return {"dadosConsulta.pesquisaLivre": query, "dadosConsulta.dtInicio": iso_to_br(window[0]),
                "dadosConsulta.dtFim": iso_to_br(window[1]),
                "dadosConsulta.cdCaderno": str(params.get("caderno") or self.config.get("caderno") or "-11"),
                "pagina": ""}

    def windows(self, params: dict) -> list[tuple[str, str]]:
        """Divide o período em janelas de até 365 dias (mais recentes primeiro).

        Verificado em 12/10/2026: para períodos longos (ex.: 2008–2025) o portal responde
        "Não foi encontrado nenhum registro" mesmo havendo resultados — um falso negativo.
        """
        df = date.fromisoformat(min(params.get("data_fim") or self.config.get("data_fim") or LAST_EDITION,
                                    LAST_EDITION))
        di_s = params.get("data_inicio") or self.config.get("data_inicio")
        di = date.fromisoformat(di_s) if di_s else df - timedelta(days=364)
        out = []
        end = df
        while end >= di:
            start = max(di, end - timedelta(days=364))
            out.append((start.isoformat(), end.isoformat()))
            end = start - timedelta(days=1)
        return out

    def _search(self, query: str, params: dict, window: tuple[str, str]):
        http = self.ctx.http
        ensure_ok(http.get(BASE + "index.do"), "Abertura do DJe")
        return ensure_ok(http.post(BASE + "consultaAvancada.do", data=self._form(query, params, window)),
                         "Pesquisa no DJe")

    def _page(self, page: int) -> list[dict]:
        r = ensure_ok(self.ctx.http.post(BASE + "trocaDePagina.do", data={"pagina": str(page)},
                                         headers={"X-Requested-With": "XMLHttpRequest"}), f"Página {page}")
        return parse_results(r.text)[1]

    def _save_item(self, it: dict) -> None:
        q = it["query"]
        pdf_url = BASE + "getPaginaDoDiario.do?" + "&".join(
            f"{k}={q.get(k, '')}" for k in ("cdVolume", "nuDiario", "cdCaderno", "nuSeqpagina"))
        view_url = BASE + "consultaSimples.do?" + "&".join(
            f"{k}={q.get(k, '')}" for k in ("cdVolume", "nuDiario", "cdCaderno", "nuSeqpagina"))
        try:
            r = ensure_ok(self.ctx.http.get(pdf_url), "PDF da página do diário")
        except AccessError as e:
            self.ctx.fail_document(it["key"], str(e), pdf_url)
            return
        if not r.content.startswith(b"%PDF"):
            self.ctx.fail_document(it["key"], "Resposta não é um PDF", pdf_url)
            return
        ext = pdf_to_text(r.content)
        meta = DocMeta(
            title=f"DJe TJSP — {it['caderno']}", url_publication=view_url, url_original=pdf_url, court="TJSP",
            act_type=None, date_available=it["date"],
            extra={"caderno_pagina": it["caderno"], "nuDiario": q.get("nuDiario"), "cdCaderno": q.get("cdCaderno"),
                   "nuSeqpagina": q.get("nuSeqpagina"),
                   "observacao": "Página do diário: pode conter várias publicações; a data é a da edição."})
        self.ctx.save(it["key"], ext, meta, blob=r.content)

    def collect(self, params: dict) -> None:
        query = (params.get("query") or "").strip()
        if not query:
            raise ValueError("O DJe do TJSP exige um termo de pesquisa (parâmetro 'query').")
        max_pages = int(params.get("max_paginas") or self.config.get("max_paginas") or 10)
        cp = self.ctx.get_checkpoint() or {}
        wins = self.windows(params)
        self.ctx.log("info", f"Período dividido em {len(wins)} janela(s) de até 365 dias: "
                             f"{wins[-1][0]} a {wins[0][1]}.")
        done = 0
        grand_total = 0
        for win in wins:
            resume = cp.get("query") == query and cp.get("window")
            if resume and tuple(cp["window"]) != win and win[1] > cp["window"][1]:
                continue                       # janela já concluída antes da interrupção
            start_page = int(cp.get("page", 0)) + 1 if resume and tuple(cp["window"]) == win else 1
            r = self._search(query, params, win)
            total, items = parse_results(r.text)
            if total is None and not items:
                txt = BeautifulSoup(r.text, "lxml").get_text(" ", strip=True)
                if re.search(r"[Nn]ão foi encontrado nenhum registro|[Nn]enhum (resultado|registro)", txt):
                    self.ctx.log("info", f"Janela {win[0]} a {win[1]}: o portal informou nenhum resultado.")
                    self.ctx.page_examined(f"janela {win[0]}–{win[1]} (sem resultados)")
                    continue
                raise AccessError("Resposta inesperada do DJe (sem resultados e sem aviso de 'nenhum resultado').")
            grand_total += total or 0
            self.ctx.set_total(grand_total)
            pages_total = ((total or 0) + 9) // 10
            page = start_page
            if start_page > 1:
                items = self._page(page)
            while True:
                self.ctx.page_examined(f"janela {win[0]}–{win[1]}, resultados página {page}")
                for it in items:
                    self._save_item(it)
                self.ctx.checkpoint({"query": query, "window": list(win), "page": page})
                done += 1
                if page >= pages_total or not items:
                    break
                if done >= max_pages:
                    raise Truncated(f"Limite de {max_pages} página(s) de resultados atingido na janela "
                                    f"{win[0]} a {win[1]} (página {page} de {pages_total}; {total} página(s) do "
                                    "diário com ocorrência nessa janela). Use 'Retomar' para continuar.")
                page += 1
                items = self._page(page)

    def test_connection(self) -> dict:
        r = self._search('"defiro o pedido"', {}, (LAST_EDITION, LAST_EDITION))
        total, items = parse_results(r.text)
        return {"ok": bool(items), "http_status": r.status_code, "total_informado": total,
                "amostra": [{"pagina": i["label"]} for i in items[:3]]}
