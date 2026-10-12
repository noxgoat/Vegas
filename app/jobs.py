"""Execução de pesquisas (com consulta opcional às fontes online antes da busca local)."""
from __future__ import annotations

import json
import time
import traceback

from .config import settings
from .collector import executor, is_running, run_collection, source_dict
from .connectors.registry import CONNECTORS
from .db import audit, get_conn, now_iso
from .query_parser import ParsedQuery, Term
from .search import SearchParams, compute_coverage, create_run, execute_local


def _set_progress(conn, run_id: int, report: dict, phase: str) -> None:
    conn.execute("UPDATE search_runs SET progress_json = ? WHERE id = ?",
                 (json.dumps({"fase": phase, "fontes": report}, ensure_ascii=False), run_id))


# Na Vercel a requisição inteira tem no máximo 300 s: novas consultas não começam após este tempo.
TEMPORARY_TIME_BUDGET = 180.0


def online_discovery(conn, run_id: int, pq: ParsedQuery, params: SearchParams) -> dict:
    report: dict = {}
    deadline = time.monotonic() + TEMPORARY_TIME_BUDGET if settings.temporary_mode else None
    rows = conn.execute("SELECT id FROM sources WHERE enabled = 1 ORDER BY id").fetchall()
    targets = [r[0] for r in rows if not params.source_ids or r[0] in [int(x) for x in params.source_ids]]
    for sid in targets:
        s = source_dict(conn, sid)
        cls = CONNECTORS.get(s["connector"])
        entry = {"nome": s["name"], "status": "em_andamento", "coletas": [], "paginas_examinadas": 0,
                 "documentos_recuperados": 0, "documentos_novos": 0, "documentos_parciais": 0,
                 "documentos_com_falha": 0, "total_informado_pela_fonte": None, "mensagens": []}
        report[str(sid)] = entry
        if cls is None:
            entry["status"] = "nao_consultada"
            entry["mensagens"].append("Fonte sem conector de rede (pesquisada somente no acervo local).")
            continue
        if is_running(sid):
            entry["status"] = "nao_consultada"
            entry["mensagens"].append("Já havia uma coleta em andamento para esta fonte.")
            continue
        jobs: list[dict] = []
        if params.city and s["connector"] in ("tjsp_dje", "generic_web"):
            entry["status"] = "nao_consultada"
            entry["mensagens"].append(f"Esta fonte não informa a cidade dos documentos; não entra na pesquisa "
                                      f"restrita a {params.city}.")
            continue
        if cls.supports_query_discovery:
            inst = cls(s, s["config"], None)
            for t in pq.terms:
                jobs.append({"query": inst.portal_query(t), "_termo": t.raw,
                             "max_paginas": params.online_max_pages or s["config"].get("max_paginas"),
                             "data_inicio": params.date_from, "data_fim": params.date_to,
                             "cidade": params.city})
        elif cls.supports_incremental and s["connector"] == "djen":
            # Um pedido por termo, com o termo no parâmetro "texto" da API (se a API o ignorar ou recusar,
            # a coleta é feita pelo período e a confirmação continua local).
            for t in pq.terms:
                jobs.append({"_termo": t.raw, "texto": t.raw, "data_inicio": params.date_from,
                             "data_fim": params.date_to, "max_paginas": params.online_max_pages})
        else:
            entry["status"] = "nao_consultada"
            entry["mensagens"].append("A fonte não oferece pesquisa por termo; pesquisada no acervo local.")
            continue
        _set_progress(conn, run_id, report, "consulta_online")
        statuses = []
        for job in jobs:
            termo = job.pop("_termo")
            if deadline is not None and time.monotonic() > deadline:
                statuses.append("parcial")
                entry["mensagens"].append(f"Termo {termo!r}: não consultado — limite de tempo da hospedagem "
                                          "temporária atingido. Resultados refletem só o acervo já armazenado.")
                continue
            job = {k: v for k, v in job.items() if v not in (None, "")}
            try:
                res = run_collection(sid, trigger="pesquisa", params=job, search_run_id=run_id)
            except RuntimeError as e:
                statuses.append("falha")
                entry["mensagens"].append(str(e))
                continue
            statuses.append(res["status"])
            entry["coletas"].append(res["id"])
            entry["paginas_examinadas"] += res["pages_examined"]
            entry["documentos_recuperados"] += res["docs_found"]
            entry["documentos_novos"] += res["docs_new"]
            entry["documentos_parciais"] += res["docs_partial"]
            entry["documentos_com_falha"] += res["docs_failed"]
            if res["total_reported"] is not None:
                entry["total_informado_pela_fonte"] = (entry["total_informado_pela_fonte"] or 0) + res["total_reported"]
            label = f"Termo {termo!r}: " if termo else ""
            if res["status"] == "nao_aplicavel":
                entry["mensagens"].append(res["message"])
                if res["message"] in entry["mensagens"][:-1]:
                    entry["mensagens"].pop()
                continue
            if res["status"] == "falha":
                entry["mensagens"].append(f"{label}falha — {res['error']}")
                entry["error"] = res["error"]
            elif res["status"] == "parcial":
                entry["mensagens"].append(f"{label}parcial — {res['message']}")
            else:
                entry["mensagens"].append(f"{label}{res['docs_found']} documento(s) recuperado(s) "
                                          f"em {res['pages_examined']} página(s).")
            _set_progress(conn, run_id, report, "consulta_online")
        if statuses and all(x == "nao_aplicavel" for x in statuses):
            entry["status"] = "nao_consultada"
        elif statuses and all(x == "falha" for x in statuses):
            entry["status"] = "falha"
        elif any(x in ("falha", "parcial", "cancelada") for x in statuses):
            entry["status"] = "parcial"
        else:
            entry["status"] = "consultada"
    return report


def run_search_job(run_id: int, pq: ParsedQuery, params: SearchParams) -> None:
    conn = get_conn()
    try:
        report = {}
        if params.online:
            report = online_discovery(conn, run_id, pq, params)
        _set_progress(conn, run_id, report, "busca_local")
        execute_local(conn, run_id, pq, params, report)
        _set_progress(conn, run_id, report, "concluida")
    except Exception as e:
        traceback.print_exc()
        conn.execute("UPDATE search_runs SET status = 'erro', error = ?, finished_at = ? WHERE id = ?",
                     (f"{type(e).__name__}: {e}", now_iso(), run_id))


def start_search(pq: ParsedQuery, params: SearchParams, background: bool = True) -> int:
    conn = get_conn()
    run_id = create_run(conn, pq, params)
    audit(conn, "pesquisa", {"pesquisa": run_id, "comando": pq.command, "online": params.online})
    if background and params.online:
        executor.submit(run_search_job, run_id, pq, params)
    else:
        run_search_job(run_id, pq, params)
    return run_id


def parsed_from_run(row) -> ParsedQuery:
    terms = [Term(index=t["index"], raw=t["raw"], mode=t["mode"], normalized=t["normalized"])
             for t in json.loads(row["terms_json"])]
    return ParsedQuery(command=row["command"], terms=terms, warnings=json.loads(row["warnings_json"] or "[]"))


__all__ = ["start_search", "run_search_job", "parsed_from_run", "compute_coverage"]
