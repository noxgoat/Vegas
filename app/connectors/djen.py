"""DJEN — Diário de Justiça Eletrônico Nacional (CNJ), API pública de comunicações.

Endpoint e parâmetros conforme documentação pública e projetos que usam a API:
  GET https://comunicaapi.pje.jus.br/api/v1/comunicacao
      siglaTribunal, dataDisponibilizacaoInicio, dataDisponibilizacaoFim (AAAA-MM-DD),
      pagina, itensPorPagina (5 ou 100), numeroProcesso, numeroOab, ufOab
  Resposta: {"status", "message", "count", "items": [...]}
  Certidão: GET /api/v1/comunicacao/{hash}/certidao

ATENÇÃO: a API recusa acessos de fora do Brasil (CloudFront 403). Este conector NÃO pôde ser
validado no ambiente de desenvolvimento. Os nomes dos campos de cada item são lidos de forma
tolerante (snake_case e camelCase) e o item completo é preservado nos metadados, para que
qualquer divergência seja visível. Use "Testar conexão" a partir de uma rede brasileira.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from ..extract import Extraction, fragment_html_to_text
from ..http import AccessError, ensure_ok
from ..ingest import DocMeta
from ..cities import city_in_text, find_city
from .base import Connector, Truncated, br_date_to_iso

API = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
ALLOWED_TRIBUNAIS = {"TJSP", "TRT15"}   # escopo do projeto: Estado de São Paulo


def pick(d: dict, *names):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None


def item_to_doc(item: dict) -> tuple[str, Extraction, DocMeta]:
    ident = pick(item, "id", "idComunicacao")
    hsh = pick(item, "hash")
    external_id = str(ident or hsh)
    raw_text = pick(item, "texto", "conteudo") or ""
    text = fragment_html_to_text(str(raw_text))
    status = "completo" if text else "falha"
    notes = [] if text else ["Item sem campo de texto na resposta da API"]
    numero = pick(item, "numeroprocessocommascara", "numeroProcessoComMascara", "numero_processo", "numeroProcesso")
    tipo_doc = pick(item, "tipoDocumento", "tipo_documento")
    tipo_com = pick(item, "tipoComunicacao", "tipo_comunicacao")
    date_av = br_date_to_iso(str(pick(item, "data_disponibilizacao", "dataDisponibilizacao",
                                      "datadisponibilizacao") or ""))
    extra = {k: v for k, v in item.items() if k not in ("texto", "conteudo")}
    meta = DocMeta(
        title=" — ".join(str(x) for x in [tipo_doc or tipo_com, numero] if x) or f"Comunicação {external_id}",
        url_publication=pick(item, "link"),
        url_original=f"{API}/{hsh}/certidao" if hsh else None,
        court=pick(item, "siglaTribunal", "sigla_tribunal"),
        organ=pick(item, "nomeOrgao", "nome_orgao"),
        act_type=tipo_doc,            # tipo informado pela própria fonte
        process_number=str(numero) if numero else None,
        date_available=date_av,
        date_publication=None,        # a API informa a disponibilização; publicação não é inferida
        extra=extra,
        city=find_city(str(pick(item, "nomeComarca", "comarca") or "")) or city_in_text(pick(item, "nomeOrgao", "nome_orgao")),
    )
    return external_id, Extraction(text=text, method="api_texto", status=status, notes=notes), meta


class Djen(Connector):
    supports_incremental = True

    def _base_params(self, params: dict) -> dict:
        sigla = (params.get("sigla_tribunal") or self.config.get("sigla_tribunal") or "").upper()
        if sigla not in ALLOWED_TRIBUNAIS:
            raise ValueError(f"Tribunal fora do escopo configurado: {sigla!r} (permitidos: TJSP, TRT15)")
        p = {"siglaTribunal": sigla}
        for key, api in (("numero_processo", "numeroProcesso"), ("numero_oab", "numeroOab"),
                         ("uf_oab", "ufOab"), ("texto", "texto")):
            v = params.get(key) or self.config.get(key)
            if key == "texto" and getattr(self, "_texto_recusado", False):
                continue
            if v:
                p[api] = v
        return p

    def _days(self, params: dict) -> list[str]:
        di, df = params.get("data_inicio"), params.get("data_fim")
        if not di:
            # Pesquisa por termo sem data: olha os últimos 30 dias (antes: só 1 dia, por isso quase nada aparecia).
            default = 30 if params.get("texto") else 1
            back = int(params.get("dias_retroativos") or (default if params.get("texto") else
                                                         self.config.get("dias_retroativos")) or default)
            di = (date.today() - timedelta(days=back)).isoformat()
        if not df:
            df = date.today().isoformat()
        d0, d1 = date.fromisoformat(di), date.fromisoformat(df)
        if (d1 - d0).days > 366:
            raise ValueError("Período máximo por coleta: 366 dias.")
        return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    def _get(self, params: dict):
        r = self.ctx.http.get(API, params=params, headers={"Accept": "application/json"})
        if r.status_code in (400, 422) and "texto" in params:
            # O parâmetro "texto" não é confirmado na documentação pública: se a API o recusar,
            # a coleta segue pelo período (a confirmação dos termos é sempre local).
            self.ctx.log("aviso", f"A API recusou o parâmetro 'texto' (HTTP {r.status_code}); coletando sem ele.")
            params.pop("texto")
            self.config["texto"] = ""
            self._texto_recusado = True
            r = self.ctx.http.get(API, params=params, headers={"Accept": "application/json"})
        ensure_ok(r, "API DJEN")
        remaining = r.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.isdigit() and int(remaining) <= 1:
            self.ctx.log("info", "Limite de requisições da API quase esgotado; aguardando 60 s.")
            time.sleep(60)
        try:
            data = r.json()
        except ValueError:
            raise AccessError("A API DJEN não retornou JSON (possível bloqueio ou mudança de formato).")
        if not isinstance(data, dict) or "items" not in data:
            raise AccessError(f"Formato inesperado da API DJEN: chaves {list(data)[:10] if isinstance(data, dict) else type(data)}")
        return data

    def collect(self, params: dict) -> None:
        base = self._base_params(params)
        days = self._days(params)
        per_page = 100
        max_pages = int(params.get("max_paginas") or self.config.get("max_paginas") or 50)
        cp = self.ctx.get_checkpoint() or {}
        same = cp.get("base") == base and cp.get("days") == [days[0], days[-1]]
        done_pages = 0
        total_all = 0
        if base.get("texto"):
            # Com termo, o período inteiro vai num pedido só (a API aceita intervalo), em vez de dia a dia.
            days_iter = [(days[0], days[-1])]
        else:
            days_iter = [(d, d) for d in days]
        for day, day_end in days_iter:
            if same and day < cp.get("day", ""):
                continue
            page = int(cp.get("page", 0)) + 1 if same and day == cp.get("day") else 1
            while True:
                q = {**base, "dataDisponibilizacaoInicio": day, "dataDisponibilizacaoFim": day_end,
                     "pagina": page, "itensPorPagina": per_page}
                data = self._get(q)
                items = data.get("items") or []
                count = int(data.get("count") or 0)
                if page == 1:
                    total_all += count
                    self.ctx.set_total(total_all)
                self.ctx.page_examined(f"{base['siglaTribunal']} {day} página {page}")
                for it in items:
                    ext_id, ext, meta = item_to_doc(it)
                    self.ctx.save(ext_id, ext, meta)
                self.ctx.checkpoint({"base": base, "days": [days[0], days[-1]], "day": day, "page": page})
                done_pages += 1
                if not items or page * per_page >= count:
                    break
                if done_pages >= max_pages:
                    raise Truncated(f"Limite de {max_pages} página(s) atingido em {day} (página {page} de "
                                    f"{(count + per_page - 1) // per_page}). Use 'Retomar' para continuar.")
                page += 1

    def test_connection(self) -> dict:
        base = self._base_params({})
        day = (date.today() - timedelta(days=1)).isoformat()
        data = self._get({**base, "dataDisponibilizacaoInicio": day, "dataDisponibilizacaoFim": day,
                          "pagina": 1, "itensPorPagina": 5})
        items = data.get("items") or []
        return {"ok": True, "count": data.get("count"), "campos_do_item": sorted(items[0].keys()) if items else [],
                "amostra": [{"id": pick(i, "id"), "tipoDocumento": pick(i, "tipoDocumento"),
                             "tem_texto": bool(pick(i, "texto"))} for i in items[:3]]}
