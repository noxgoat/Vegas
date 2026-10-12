"""Motor de busca determinístico (independente de IA generativa).

Fluxo de cada termo:
1. Pré-filtro de candidatos pelo índice de trigramas (FTS5) sobre o texto
   normalizado — ou varredura com instr() para termos com menos de 3 caracteres.
2. Confirmação no texto ARMAZENADO, com a mesma normalização, localizando
   TODAS as ocorrências (não sobrepostas) e mapeando cada uma para a posição
   exata no texto original.
3. Somente ocorrências confirmadas são gravadas e exibidas.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .config import settings
from .db import now_iso
from .normalize import Normalized, normalize, is_word_char
from .query_parser import EXACT, SUBSTRING, MODE_LABEL, ParsedQuery, parse

NAO_LOCALIZADO = "Não localizado na fonte consultada"


def find_matches(norm_text: str, term: str, mode: str) -> list[tuple[int, int]]:
    """Todas as ocorrências não sobrepostas de `term` em `norm_text` (ambos normalizados)."""
    out: list[tuple[int, int]] = []
    if not term:
        return out
    check_left = mode == EXACT and is_word_char(term[0])
    check_right = mode == EXACT and is_word_char(term[-1])
    n, L = len(norm_text), len(term)
    i = norm_text.find(term)
    while i != -1:
        j = i + L
        ok = True
        if check_left and i > 0 and is_word_char(norm_text[i - 1]):
            ok = False
        if ok and check_right and j < n and is_word_char(norm_text[j]):
            ok = False
        if ok:
            out.append((i, j))
            i = norm_text.find(term, j)
        else:
            i = norm_text.find(term, i + 1)
    return out


def _trim_before(s: str) -> str:
    if len(s) < settings.snippet_context:
        return s
    k = s.find(" ")
    return s[k + 1:] if 0 <= k < 40 else s


def _trim_after(s: str) -> str:
    if len(s) < settings.snippet_context:
        return s
    k = s.rfind(" ")
    return s[:k] if k > len(s) - 40 else s


def _page_for(pages: list | None, pos: int) -> int | None:
    if not pages:
        return None
    for num, a, b in pages:
        if a <= pos < b or (pos == b and a == b):
            return num
    return None


@dataclass
class SearchParams:
    fold_accents: bool = False
    source_ids: list[int] | None = None
    date_from: str | None = None
    date_to: str | None = None
    process_number: str | None = None
    online: bool = False
    online_max_pages: int | None = None
    city: str | None = None              # município de SP (nome oficial): restringe a pesquisa
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "SearchParams":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


def _filters_sql(p: SearchParams) -> tuple[str, list]:
    where = ["s.enabled = 1"]
    args: list = []
    if p.source_ids:
        where.append(f"d.source_id IN ({','.join('?' * len(p.source_ids))})")
        args += [int(x) for x in p.source_ids]
    date_expr = "COALESCE(d.date_publication, d.date_available, d.date_decision)"
    if p.date_from:
        where.append(f"{date_expr} >= ?")
        args.append(p.date_from)
    if p.date_to:
        where.append(f"{date_expr} <= ?")
        args.append(p.date_to)
    if settings.only_judge_acts:
        where.append("d.act_kind IN ('despacho', 'decisao')")
    if p.city:
        where.append("d.city = ?")
        args.append(p.city)
    if p.process_number:
        digits = "".join(c for c in p.process_number if c.isdigit())
        where.append("replace(replace(d.process_number,'.',''),'-','') LIKE ?")
        args.append(f"%{digits}%")
    return " AND ".join(where), args


def candidate_ids(conn: sqlite3.Connection, term: str, fold: bool, p: SearchParams) -> list[int]:
    col = "folded" if fold else "norm"
    fsql, fargs = _filters_sql(p)
    base = ("SELECT d.id FROM doc_fts f JOIN documents d ON d.id = f.rowid "
            "JOIN sources s ON s.id = d.source_id WHERE ")
    if len(term) >= 3:
        expr = f'{col} : "{term.replace(chr(34), chr(34) * 2)}"'
        rows = conn.execute(base + f"doc_fts MATCH ? AND {fsql} ORDER BY d.id", [expr, *fargs])
    else:
        rows = conn.execute(base + f"instr(f.{col}, ?) > 0 AND {fsql} ORDER BY d.id", [term, *fargs])
    return [r[0] for r in rows]


def create_run(conn: sqlite3.Connection, pq: ParsedQuery, params: SearchParams) -> int:
    cur = conn.execute(
        "INSERT INTO search_runs (command, terms_json, params_json, status, warnings_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (pq.command, json.dumps([t.to_dict() for t in pq.terms], ensure_ascii=False),
         json.dumps(params.to_dict(), ensure_ascii=False), "em_andamento",
         json.dumps(pq.warnings, ensure_ascii=False), now_iso()))
    return cur.lastrowid


def execute_local(conn: sqlite3.Connection, run_id: int, pq: ParsedQuery, params: SearchParams,
                  online_report: dict | None = None) -> dict:
    """Executa a busca no acervo armazenado e grava ocorrências/resultados."""
    fold = bool(params.fold_accents)
    conn.execute("DELETE FROM occurrences WHERE search_run_id = ?", (run_id,))
    conn.execute("DELETE FROM search_results WHERE search_run_id = ?", (run_id,))
    cands: dict[int, list] = {}
    for t in pq.terms:
        for doc_id in candidate_ids(conn, t.normalized, fold, params):
            cands.setdefault(doc_id, []).append(t)
    ctx = settings.snippet_context
    conn.execute("BEGIN")
    try:
        for doc_id, terms in sorted(cands.items()):
            d = conn.execute("SELECT text, version, pages_json FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if d is None:
                continue
            original = d["text"]
            pages = json.loads(d["pages_json"]) if d["pages_json"] else None
            norm: Normalized = normalize(original, fold_accents=fold)
            for t in terms:
                matches = find_matches(norm.text, t.normalized, t.mode)
                if not matches:
                    continue  # falso positivo do pré-filtro: descartado
                rows = []
                for k, (a, b) in enumerate(matches, start=1):
                    s, e = norm.original_span(a, b)
                    rows.append((run_id, t.index, t.raw, t.mode, doc_id, d["version"], k, s, e,
                                 _page_for(pages, s), _trim_before(original[max(0, s - ctx):s]),
                                 original[s:e], _trim_after(original[e:e + ctx])))
                conn.executemany(
                    "INSERT INTO occurrences (search_run_id, term_index, term, match_mode, document_id, "
                    "document_version, ordinal, start_pos, end_pos, page, snippet_before, match_text, "
                    "snippet_after) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                conn.execute(
                    "INSERT INTO search_results (search_run_id, term_index, document_id, document_version, "
                    "occurrence_count, first_position) VALUES (?,?,?,?,?,?)",
                    (run_id, t.index, doc_id, d["version"], len(rows), rows[0][7]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    stats = compute_stats(conn, run_id, pq)
    coverage = compute_coverage(conn, run_id, params, online_report)
    conn.execute("UPDATE search_runs SET status = 'concluida', stats_json = ?, coverage_json = ?, finished_at = ? "
                 "WHERE id = ?", (json.dumps(stats, ensure_ascii=False), json.dumps(coverage, ensure_ascii=False),
                                  now_iso(), run_id))
    return {"run_id": run_id, "stats": stats, "coverage": coverage}


def compute_stats(conn: sqlite3.Connection, run_id: int, pq: ParsedQuery) -> dict:
    per_term = []
    for t in pq.terms:
        r = conn.execute(
            "SELECT COUNT(*) AS docs, COALESCE(SUM(occurrence_count),0) AS occ, "
            "COUNT(DISTINCT COALESCE(d.dup_of, d.id)) AS uniq "
            "FROM search_results sr JOIN documents d ON d.id = sr.document_id "
            "WHERE sr.search_run_id = ? AND sr.term_index = ?", (run_id, t.index)).fetchone()
        per_term.append({"term_index": t.index, "term": t.raw, "mode": t.mode, "mode_label": MODE_LABEL[t.mode],
                         "occurrences": r["occ"], "documents": r["docs"], "unique_documents": r["uniq"]})
    tot = conn.execute(
        "SELECT COALESCE(SUM(occurrence_count),0) AS occ, COUNT(*) AS results, "
        "COUNT(DISTINCT sr.document_id) AS docs, COUNT(DISTINCT COALESCE(d.dup_of, d.id)) AS uniq, "
        "COUNT(DISTINCT d.source_id) AS sources "
        "FROM search_results sr JOIN documents d ON d.id = sr.document_id WHERE sr.search_run_id = ?",
        (run_id,)).fetchone()
    return {"per_term": per_term, "total_occurrences": tot["occ"], "total_results": tot["results"],
            "matched_documents": tot["docs"], "unique_documents": tot["uniq"], "sources_with_results": tot["sources"]}


STATUS_LABEL = {
    "ativa": "Ativa", "nao_configurada": "Não configurada", "falha_acesso": "Falha de acesso",
    "desativada": "Desativada", "nao_integravel": "Não integrável (CAPTCHA/controle de acesso)",
    "nao_validada": "Implementada — não validada",
}


def compute_coverage(conn: sqlite3.Connection, run_id: int, params: SearchParams,
                     online_report: dict | None = None) -> list[dict]:
    online_report = online_report or {}
    out = []
    for s in conn.execute("SELECT * FROM sources ORDER BY id"):
        sid = s["id"]
        counts = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(extraction_status='completo' AND text_scope='integral') AS integral, "
            "SUM(extraction_status='parcial' OR (extraction_status='completo' AND text_scope='parcial')) AS parcial, "
            "SUM(extraction_status='falha') AS falha, MAX(last_collected_at) AS ultima "
            "FROM documents WHERE source_id = ?", (sid,)).fetchone()
        last_run = conn.execute("SELECT status, started_at, finished_at, error FROM collection_runs "
                                "WHERE source_id = ? AND status NOT IN ('em_andamento', 'nao_aplicavel') "
                                "ORDER BY id DESC LIMIT 1",
                                (sid,)).fetchone()
        matched = conn.execute(
            "SELECT COUNT(DISTINCT sr.document_id) FROM search_results sr JOIN documents d ON d.id = sr.document_id "
            "WHERE sr.search_run_id = ? AND d.source_id = ?", (run_id, sid)).fetchone()[0]
        in_filter = not params.source_ids or sid in [int(x) for x in params.source_ids]
        in_city = None
        if params.city:
            in_city = conn.execute("SELECT COUNT(*) FROM documents WHERE source_id = ? AND city = ?",
                                   (sid, params.city)).fetchone()[0]
        in_scope = None
        if settings.only_judge_acts:
            q = "SELECT COUNT(*) FROM documents WHERE source_id = ? AND act_kind IN ('despacho', 'decisao')"
            a = [sid]
            if params.city:
                q += " AND city = ?"
                a.append(params.city)
            in_scope = conn.execute(q, a).fetchone()[0]
        searched = (bool(s["enabled"]) and in_filter and (counts["total"] or 0) > 0 and in_city != 0
                    and in_scope != 0)
        warnings = []
        if s["status"] in ("nao_integravel", "nao_configurada"):
            msg = f"Não pesquisada — {STATUS_LABEL[s['status']]}."
        elif not s["enabled"]:
            msg = "Desativada — não pesquisada."
        elif not in_filter:
            msg = "Excluída pelo filtro desta pesquisa."
        elif not counts["total"]:
            msg = ("Acervo vazio: nenhum documento coletado. Ausência de resultados aqui NÃO significa "
                   "ausência de publicações na fonte.")
        elif in_scope == 0 and not (params.city and not in_city):
            msg = ("Nenhum documento desta fonte no acervo é despacho ou decisão segundo o tipo declarado pela "
                   "fonte (ex.: sentenças, intimações, sem tipo informado) — fora do escopo, não pesquisado.")
        elif params.city and not in_city:
            msg = (f"Nenhum documento desta fonte no acervo é de {params.city} (ou a fonte não informa a cidade). "
                   "Ausência de resultados aqui NÃO significa ausência de publicações.")
        elif params.city:
            msg = (f"Pesquisada no acervo local, somente {params.city}: "
                   f"{in_scope if in_scope is not None else in_city} despacho(s)/decisão(ões) de "
                   f"{counts['total']} documento(s).")
        elif in_scope is not None:
            msg = (f"Pesquisada no acervo local: {in_scope} despacho(s)/decisão(ões) de {counts['total']} "
                   f"documento(s); última coleta de documento em {counts['ultima']}.")
        else:
            msg = (f"Pesquisada no acervo local: {counts['total']} documento(s), última coleta de documento em "
                   f"{counts['ultima']}.")
        if last_run and last_run["status"] == "falha":
            warnings.append(f"A última coleta FALHOU ({last_run['finished_at']}): {last_run['error']}. "
                            "O acervo pode estar incompleto; ausência de correspondências não comprova inexistência.")
        if s["status"] == "falha_acesso":
            warnings.append(f"Fonte em estado 'Falha de acesso': {s['last_error'] or ''}")
        if counts["parcial"]:
            warnings.append(f"{counts['parcial']} documento(s) parcialmente processado(s) nesta fonte.")
        if counts["falha"]:
            warnings.append(f"{counts['falha']} documento(s) sem texto extraído nesta fonte (não pesquisáveis).")
        online = online_report.get(str(sid)) or online_report.get(sid)
        if online:
            if online.get("status") == "falha":
                warnings.append(f"Consulta online falhou: {online.get('error')}. Resultados desta fonte refletem "
                                "apenas o acervo já armazenado.")
        out.append({
            "source_id": sid, "name": s["name"], "court": s["court"], "status": s["status"],
            "status_label": STATUS_LABEL.get(s["status"], s["status"]), "enabled": bool(s["enabled"]),
            "cadastrada": True,
            "acessivel": (None if not last_run else last_run["status"] in ("sucesso", "parcial")),
            "consultada_online": online, "pesquisada_no_acervo": searched,
            "documentos_no_acervo": counts["total"] or 0, "documentos_integrais": counts["integral"] or 0,
            "documentos_parciais": counts["parcial"] or 0, "documentos_sem_texto": counts["falha"] or 0,
            "documentos_com_correspondencia": matched, "despachos_e_decisoes": in_scope, "ultima_coleta_bem_sucedida": s["last_success_at"],
            "ultima_coleta": dict(last_run) if last_run else None, "mensagem": msg, "avisos": warnings,
            "restricoes": s["known_restrictions"],
        })
    return out


SORTS = {
    "data": "COALESCE(d.date_publication, d.date_available, d.date_decision) IS NULL, "
            "COALESCE(d.date_publication, d.date_available, d.date_decision) DESC, d.id",
    "data_asc": "COALESCE(d.date_publication, d.date_available, d.date_decision) IS NULL, "
                "COALESCE(d.date_publication, d.date_available, d.date_decision) ASC, d.id",
    "fonte": "s.name, d.id",
    "relevancia": "sr.occurrence_count DESC, d.id",
}


def doc_public(d: sqlite3.Row | dict) -> dict:
    d = dict(d)
    meta = {}
    for f in ["title", "url_publication", "url_original", "court", "organ", "act_type", "process_number",
              "judge", "date_available", "date_publication", "date_decision", "city"]:
        meta[f] = d.get(f) or None
    verified = d.get("extraction_status") == "completo" and d.get("text_scope") == "integral"
    return {
        "id": d["id"], "source_id": d["source_id"], "source_name": d.get("source_name"), **meta,
        "extra": json.loads(d.get("metadata_json") or "{}"),
        "extraction_method": d.get("extraction_method"), "extraction_status": d.get("extraction_status"),
        "extraction_notes": d.get("extraction_notes"), "text_scope": d.get("text_scope"),
        "ocr_confidence": d.get("ocr_confidence"),
        "verification_status": "Integralmente verificado" if verified else "Parcial",
        "act_kind": d.get("act_kind"),
        "version": d.get("version"), "dup_of": d.get("dup_of"),
        "last_collected_at": d.get("last_collected_at"), "first_seen_at": d.get("first_seen_at"),
    }


def get_results(conn: sqlite3.Connection, run_id: int, term_index: int, page: int = 1, page_size: int = 20,
                sort: str = "relevancia", source_id: int | None = None, max_occ: int = 200) -> dict:
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), 100)
    order = SORTS.get(sort, SORTS["relevancia"])
    where = "sr.search_run_id = ? AND sr.term_index = ?"
    args: list = [run_id, term_index]
    if source_id:
        where += " AND d.source_id = ?"
        args.append(int(source_id))
    total = conn.execute(f"SELECT COUNT(*) FROM search_results sr JOIN documents d ON d.id = sr.document_id "
                         f"WHERE {where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT sr.occurrence_count, sr.document_version, d.*, s.name AS source_name "
        f"FROM search_results sr JOIN documents d ON d.id = sr.document_id JOIN sources s ON s.id = d.source_id "
        f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", [*args, page_size, (page - 1) * page_size]).fetchall()
    items = []
    for r in rows:
        occ = conn.execute(
            "SELECT ordinal, start_pos, end_pos, page, snippet_before, match_text, snippet_after "
            "FROM occurrences WHERE search_run_id = ? AND term_index = ? AND document_id = ? "
            "ORDER BY ordinal LIMIT ?", (run_id, term_index, r["id"], max_occ)).fetchall()
        item = doc_public(r)
        item["occurrence_count"] = r["occurrence_count"]
        item["searched_version"] = r["document_version"]
        item["version_changed_since_search"] = r["document_version"] != r["version"]
        item["occurrences"] = [dict(o) for o in occ]
        items.append(item)
    return {"run_id": run_id, "term_index": term_index, "page": page, "page_size": page_size, "total": total,
            "pages": (total + page_size - 1) // page_size, "sort": sort, "items": items}


def run_search_sync(conn: sqlite3.Connection, command: str, params: SearchParams | None = None) -> dict:
    """Atalho síncrono (sem consulta online): interpretar + executar no acervo."""
    params = params or SearchParams()
    pq = parse(command, fold_accents=params.fold_accents)
    run_id = create_run(conn, pq, params)
    res = execute_local(conn, run_id, pq, params)
    res["parsed"] = pq.to_dict()
    return res
