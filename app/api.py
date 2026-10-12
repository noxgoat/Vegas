"""API e servidor web do JurisBusca SP."""
from __future__ import annotations

import hmac
import json
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import collector, summarize
from .cities import city_in_text, find_city
from .config import settings
from .connectors.registry import CONNECTORS, PASSIVE
from .db import apply_retention, audit, get_conn, init_db, now_iso
from .export import to_csv, to_xlsx
from .extract import _tesseract_available, extract_bytes, sha256
from .http import domain_allowed, proxy_label
from .ingest import DocMeta, delete_document, save_document
from .jobs import parsed_from_run, start_search
from .interpret import InterpretError, interpret
from .query_parser import QuerySyntaxError, parse
from .search import NAO_LOCALIZADO, SearchParams, doc_public, get_results

STATIC = Path(__file__).parent / "static"
UPLOAD_EXT = {".pdf", ".html", ".htm", ".txt"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db()
    apply_retention(conn)
    # Coletas interrompidas por reinicialização não são consideradas concluídas.
    conn.execute("UPDATE collection_runs SET status = 'falha', finished_at = ?, error = "
                 "'Interrompida (servidor reiniciado). Use Retomar para continuar do checkpoint.' "
                 "WHERE status = 'em_andamento'", (now_iso(),))
    conn.execute("UPDATE search_runs SET status = 'erro', error = 'Interrompida (servidor reiniciado).' "
                 "WHERE status = 'em_andamento'")
    collector.start_scheduler()
    yield


app = FastAPI(title="JurisBusca SP", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def security(request: Request, call_next):
    if (settings.temporary_mode and not settings.app_token and request.url.path.startswith("/api/")
            and request.url.path != "/api/status"):
        return JSONResponse({"detail": "Defina a variável de ambiente JURIS_APP_TOKEN no painel da Vercel "
                                       "(Settings → Environment Variables) e faça um novo deploy."},
                            status_code=503)
    if settings.app_token and request.url.path.startswith("/api/") and request.url.path != "/api/status":
        token = request.headers.get("x-app-token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(token, settings.app_token):
            return JSONResponse({"detail": "Não autorizado. Informe o token de acesso."}, status_code=401)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/api/docs"):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return resp


def db():
    return get_conn()


# ------------------------------------------------------------------ status
@app.get("/api/status")
def status(request: Request):
    if settings.app_token and not hmac.compare_digest(request.headers.get("x-app-token") or "", settings.app_token):
        # Sem token: só o necessário para a interface pedir o token ou exibir o aviso de configuração.
        return {"token_exigido": True, "autenticado": False, "modo_temporario": settings.temporary_mode,
                "token_ausente": False}
    conn = db()
    return {
        "autenticado": True,
        "escopo": "Estado de São Paulo — TJSP e TRT-15",
        "documentos": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "documentos_unicos": conn.execute("SELECT COUNT(DISTINCT COALESCE(dup_of, id)) FROM documents").fetchone()[0],
        "fontes": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "ia_disponivel": summarize.available(), "ocr_disponivel": _tesseract_available(),
        "dominios_permitidos": settings.allowed_domains, "token_exigido": bool(settings.app_token),
        "proxy": proxy_label(settings.proxy_url),
        "modo_temporario": settings.temporary_mode,
        "token_ausente": settings.temporary_mode and not settings.app_token,
        "nao_localizado": NAO_LOCALIZADO,
    }


# ------------------------------------------------------------------ pesquisa
class SearchIn(BaseModel):
    command: str = Field(max_length=2000)
    fold_accents: bool = False
    source_ids: list[int] | None = None
    date_from: str | None = None
    date_to: str | None = None
    process_number: str | None = Field(default=None, max_length=40)
    online: bool = False
    online_max_pages: int | None = Field(default=None, ge=1, le=200)

    @field_validator("date_from", "date_to")
    @classmethod
    def _date(cls, v):
        if v in (None, ""):
            return None
        if not DATE_RE.match(v):
            raise ValueError("Data deve estar no formato AAAA-MM-DD")
        return v


@app.post("/api/parse")
def parse_command(body: SearchIn):
    try:
        return parse(body.command, body.fold_accents).to_dict()
    except QuerySyntaxError as e:
        raise HTTPException(400, {"erro": str(e), "posicao": e.position})


class AskIn(BaseModel):
    message: str = Field(max_length=2000)
    fold_accents: bool = False
    online: bool = True                      # padrão: consultar todas as fontes de SP antes de responder
    online_max_pages: int | None = Field(default=None, ge=1, le=200)
    date_from: str | None = None
    date_to: str | None = None
    source_ids: list[int] | None = None

    @field_validator("date_from", "date_to")
    @classmethod
    def _date(cls, v):
        if v in (None, ""):
            return None
        if not DATE_RE.match(v):
            raise ValueError("Data deve estar no formato AAAA-MM-DD")
        return v


@app.post("/api/interpret")
def interpret_message(body: AskIn):
    try:
        return interpret(body.message, body.fold_accents).to_dict()
    except (QuerySyntaxError, InterpretError) as e:
        raise HTTPException(400, {"erro": str(e)})


@app.post("/api/ask")
def ask(body: AskIn):
    """Pergunta em linguagem de conversa -> interpretação determinística -> pesquisa."""
    try:
        it = interpret(body.message, body.fold_accents)
    except (QuerySyntaxError, InterpretError) as e:
        raise HTTPException(400, {"erro": str(e)})
    params = SearchParams(fold_accents=body.fold_accents, source_ids=body.source_ids or None,
                          date_from=body.date_from or it.date_from, date_to=body.date_to or it.date_to,
                          online=body.online, online_max_pages=body.online_max_pages or 3, city=it.city,
                          extra={"mensagem": body.message, "modo": it.mode, "ignoradas": it.ignored})
    if settings.temporary_mode and params.online:
        params.online_max_pages = min(params.online_max_pages, settings.temporary_max_pages)
    run_id = start_search(it.parsed, params, background=not settings.temporary_mode)
    return {"run_id": run_id, "interpretation": it.to_dict()}


@app.post("/api/searches")
def create_search(body: SearchIn):
    try:
        pq = parse(body.command, body.fold_accents)
    except QuerySyntaxError as e:
        raise HTTPException(400, {"erro": str(e), "posicao": e.position})
    params = SearchParams(fold_accents=body.fold_accents, source_ids=body.source_ids or None,
                          date_from=body.date_from, date_to=body.date_to,
                          process_number=body.process_number or None, online=body.online,
                          online_max_pages=body.online_max_pages)
    if settings.temporary_mode and params.online:
        params.online_max_pages = min(params.online_max_pages or settings.temporary_max_pages,
                                      settings.temporary_max_pages)
    run_id = start_search(pq, params, background=not settings.temporary_mode)
    return {"run_id": run_id, "parsed": pq.to_dict()}


def _run_out(row) -> dict:
    d = dict(row)
    for k in ("terms_json", "params_json", "progress_json", "stats_json", "coverage_json", "warnings_json"):
        d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
    return d


@app.get("/api/searches")
def list_searches(limit: int = Query(30, ge=1, le=200)):
    rows = db().execute("SELECT id, command, status, created_at, finished_at, stats_json FROM search_runs "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        st = json.loads(r["stats_json"] or "null")
        out.append({"id": r["id"], "command": r["command"], "status": r["status"], "created_at": r["created_at"],
                    "total_occurrences": st and st["total_occurrences"],
                    "unique_documents": st and st["unique_documents"]})
    return out


@app.get("/api/searches/{run_id}")
def get_search(run_id: int):
    row = db().execute("SELECT * FROM search_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Pesquisa não encontrada")
    return _run_out(row)


@app.get("/api/searches/{run_id}/results")
def search_results(run_id: int, term: int = 0, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   sort: str = "relevancia", source_id: int | None = None):
    return get_results(db(), run_id, term, page, page_size, sort, source_id)


@app.get("/api/searches/{run_id}/documents/{doc_id}/occurrences")
def doc_occurrences(run_id: int, doc_id: int, term: int = 0):
    rows = db().execute("SELECT ordinal, start_pos, end_pos, page, match_text FROM occurrences WHERE search_run_id = ? "
                        "AND term_index = ? AND document_id = ? ORDER BY ordinal", (run_id, term, doc_id)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/searches/{run_id}/export.{fmt}")
def export(run_id: int, fmt: str, term: int | None = None, source_id: int | None = None):
    conn = db()
    if fmt == "csv":
        data, media = to_csv(conn, run_id, term, source_id), "text/csv; charset=utf-8"
    elif fmt == "xlsx":
        data = to_xlsx(conn, run_id, term, source_id)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise HTTPException(400, "Formato não suportado (csv ou xlsx)")
    suffix = "completo" if term is None and source_id is None else "selecao"
    audit(conn, "exportacao", {"pesquisa": run_id, "formato": fmt, "termo": term, "fonte": source_id})
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="pesquisa-{run_id}-{suffix}.{fmt}"'})


# ------------------------------------------------------------------ fontes
class SourceIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,60}$")
    name: str = Field(min_length=2, max_length=200)
    court: str | None = Field(default=None, max_length=20)
    base_url: str = Field(max_length=500)
    category: str = Field(max_length=40)
    connector: str
    access_method: str | None = None
    search_mechanism: str | None = None
    pagination: str | None = None
    document_method: str | None = None
    formats: str | None = None
    update_policy: str | None = None
    schedule_minutes: int = Field(default=0, ge=0, le=60 * 24 * 31)
    retry_minutes: int = Field(default=60, ge=1, le=60 * 24 * 7)
    max_retries: int = Field(default=3, ge=0, le=10)
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    status: str | None = None
    known_restrictions: str | None = None

    @field_validator("connector")
    @classmethod
    def _conn(cls, v):
        if v not in CONNECTORS and v not in PASSIVE:
            raise ValueError(f"Conector desconhecido. Opções: {sorted([*CONNECTORS, *PASSIVE])}")
        return v

    @field_validator("base_url")
    @classmethod
    def _url(cls, v):
        p = urlsplit(v)
        if p.scheme == "local":
            return v
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("URL deve começar com http:// ou https://")
        if not domain_allowed(p.hostname):
            raise ValueError(f"Domínio fora do escopo permitido ({', '.join(settings.allowed_domains)})")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v and v not in ("ativa", "nao_configurada", "falha_acesso", "desativada", "nao_integravel", "nao_validada"):
            raise ValueError("Estado inválido")
        return v


def _source_out(r) -> dict:
    d = dict(r)
    d["config"] = json.loads(d.pop("config_json") or "{}")
    d["enabled"] = bool(d["enabled"])
    d["running_collection"] = collector.is_running(d["id"])
    cls = CONNECTORS.get(d["connector"])
    d["pesquisa_por_termo"] = bool(cls and cls.supports_query_discovery)
    d["coleta_por_periodo"] = bool(cls and cls.supports_incremental)
    conn = db()
    d["documentos"] = conn.execute("SELECT COUNT(*) FROM documents WHERE source_id = ?", (d["id"],)).fetchone()[0]
    last = conn.execute("SELECT id, status, started_at, finished_at, error, message FROM collection_runs "
                        "WHERE source_id = ? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
    d["ultima_coleta"] = dict(last) if last else None
    return d


@app.get("/api/sources")
def list_sources():
    return [_source_out(r) for r in db().execute("SELECT * FROM sources ORDER BY id")]


def _validate_config(body: SourceIn):
    for u in body.config.get("urls", []) or []:
        p = urlsplit(u)
        if p.scheme not in ("http", "https") or not domain_allowed(p.hostname or ""):
            raise HTTPException(400, f"URL fora do escopo permitido: {u}")
    if body.config.get("seguir_links_regex"):
        try:
            re.compile(body.config["seguir_links_regex"])
        except re.error as e:
            raise HTTPException(400, f"Expressão regular inválida: {e}")


@app.post("/api/sources")
def create_source(body: SourceIn):
    _validate_config(body)
    conn = db()
    if conn.execute("SELECT 1 FROM sources WHERE slug = ?", (body.slug,)).fetchone():
        raise HTTPException(409, "Já existe uma fonte com este identificador")
    status_ = body.status or ("nao_configurada" if body.connector == "nenhum" else "nao_validada")
    cur = conn.execute(
        "INSERT INTO sources (slug, name, court, base_url, category, connector, access_method, search_mechanism, "
        "pagination, document_method, formats, update_policy, schedule_minutes, retry_minutes, max_retries, "
        "config_json, enabled, status, known_restrictions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.slug, body.name, body.court, body.base_url, body.category, body.connector, body.access_method,
         body.search_mechanism, body.pagination, body.document_method, body.formats, body.update_policy,
         body.schedule_minutes, body.retry_minutes, body.max_retries, json.dumps(body.config, ensure_ascii=False),
         int(body.enabled), status_, body.known_restrictions))
    audit(conn, "fonte_criada", {"slug": body.slug})
    return _source_out(conn.execute("SELECT * FROM sources WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.put("/api/sources/{source_id}")
def update_source(source_id: int, body: SourceIn):
    _validate_config(body)
    conn = db()
    old = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if not old:
        raise HTTPException(404, "Fonte não encontrada")
    status_ = body.status or old["status"]
    if not body.enabled:
        status_ = "desativada"
    elif old["status"] == "desativada" and status_ == "desativada":
        status_ = "nao_validada" if body.connector in CONNECTORS else "ativa"
    conn.execute(
        "UPDATE sources SET slug=?, name=?, court=?, base_url=?, category=?, connector=?, access_method=?, "
        "search_mechanism=?, pagination=?, document_method=?, formats=?, update_policy=?, schedule_minutes=?, "
        "retry_minutes=?, max_retries=?, config_json=?, enabled=?, status=?, known_restrictions=?, updated_at=? "
        "WHERE id=?",
        (body.slug, body.name, body.court, body.base_url, body.category, body.connector, body.access_method,
         body.search_mechanism, body.pagination, body.document_method, body.formats, body.update_policy,
         body.schedule_minutes, body.retry_minutes, body.max_retries, json.dumps(body.config, ensure_ascii=False),
         int(body.enabled), status_, body.known_restrictions, now_iso(), source_id))
    audit(conn, "fonte_editada", {"id": source_id, "slug": body.slug})
    return _source_out(conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone())


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int):
    conn = db()
    if collector.is_running(source_id):
        raise HTTPException(409, "Há uma coleta em andamento para esta fonte")
    row = conn.execute("SELECT slug FROM sources WHERE id = ?", (source_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Fonte não encontrada")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM doc_fts WHERE rowid IN (SELECT id FROM documents WHERE source_id = ?)", (source_id,))
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.execute("COMMIT")
    audit(conn, "fonte_removida", {"id": source_id, "slug": row["slug"]})
    return {"ok": True}


class CollectIn(BaseModel):
    params: dict = Field(default_factory=dict)


@app.post("/api/sources/{source_id}/collect")
def collect(source_id: int, body: CollectIn):
    conn = db()
    s = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Fonte não encontrada")
    if s["connector"] not in CONNECTORS:
        raise HTTPException(400, "Esta fonte não possui conector de coleta automática")
    if collector.is_running(source_id):
        raise HTTPException(409, "Já existe uma coleta em andamento para esta fonte")
    allowed = {"query", "max_paginas", "data_inicio", "data_fim", "dias_retroativos", "numero_processo",
               "numero_oab", "uf_oab", "texto", "caderno", "urls", "seguir_links_regex"}
    params = {k: v for k, v in body.params.items() if k in allowed and v not in (None, "")}
    for k in ("data_inicio", "data_fim"):
        if k in params and not DATE_RE.match(str(params[k])):
            raise HTTPException(400, f"{k} deve estar no formato AAAA-MM-DD")
    if settings.temporary_mode:
        # Sem segundo plano na Vercel: a coleta roda dentro da requisição, com limite de páginas.
        params["max_paginas"] = min(int(params.get("max_paginas") or settings.temporary_max_pages),
                                    settings.temporary_max_pages)
        res = collector.run_collection(source_id, trigger="manual", params=params)
        audit(conn, "coleta_solicitada", {"fonte": s["slug"], "params": params})
        return {"ok": True, "coleta": res}
    collector.submit_collection(source_id, trigger="manual", params=params)
    audit(conn, "coleta_solicitada", {"fonte": s["slug"], "params": params})
    return {"ok": True}


@app.post("/api/sources/{source_id}/test")
def test_source(source_id: int):
    return collector.test_source(source_id)


@app.post("/api/sources/{source_id}/cancel")
def cancel(source_id: int):
    return {"ok": collector.cancel_collection(source_id)}


@app.get("/api/collections")
def list_collections(source_id: int | None = None, limit: int = Query(50, ge=1, le=500)):
    q = "SELECT c.*, s.name AS source_name FROM collection_runs c JOIN sources s ON s.id = c.source_id"
    args: list = []
    if source_id:
        q += " WHERE c.source_id = ?"
        args.append(source_id)
    q += " ORDER BY c.id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in db().execute(q, args)]


@app.get("/api/collections/{cid}")
def get_collection(cid: int):
    conn = db()
    r = conn.execute("SELECT c.*, s.name AS source_name FROM collection_runs c JOIN sources s ON s.id = c.source_id "
                     "WHERE c.id = ?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Coleta não encontrada")
    logs = conn.execute("SELECT at, level, message, url FROM collection_logs WHERE collection_run_id = ? "
                        "ORDER BY id DESC LIMIT 500", (cid,)).fetchall()
    return {**dict(r), "logs": [dict(x) for x in logs]}


@app.post("/api/collections/{cid}/resume")
def resume(cid: int):
    conn = db()
    r = conn.execute("SELECT source_id, status, checkpoint_json FROM collection_runs WHERE id = ?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Coleta não encontrada")
    if r["status"] == "sucesso":
        raise HTTPException(400, "Coleta já concluída com sucesso")
    if collector.is_running(r["source_id"]):
        raise HTTPException(409, "Já existe uma coleta em andamento para esta fonte")
    if settings.temporary_mode:
        res = collector.run_collection(r["source_id"], trigger="retomada", resume_from=cid)
        return {"ok": True, "coleta": res}
    collector.submit_collection(r["source_id"], trigger="retomada", resume_from=cid)
    return {"ok": True}


# ------------------------------------------------------------------ documentos
@app.get("/api/documents")
def list_documents(source_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
                   status: str | None = None):
    where, args = ["1=1"], []
    if source_id:
        where.append("d.source_id = ?")
        args.append(source_id)
    if status:
        where.append("d.extraction_status = ?")
        args.append(status)
    w = " AND ".join(where)
    conn = db()
    total = conn.execute(f"SELECT COUNT(*) FROM documents d WHERE {w}", args).fetchone()[0]
    rows = conn.execute(f"SELECT d.*, s.name AS source_name FROM documents d JOIN sources s ON s.id = d.source_id "
                        f"WHERE {w} ORDER BY d.id DESC LIMIT ? OFFSET ?", [*args, page_size, (page - 1) * page_size])
    return {"total": total, "page": page, "page_size": page_size, "items": [doc_public(r) for r in rows]}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int, version: int | None = None):
    conn = db()
    r = conn.execute("SELECT d.*, s.name AS source_name FROM documents d JOIN sources s ON s.id = d.source_id "
                     "WHERE d.id = ?", (doc_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Documento não encontrado")
    out = doc_public(r)
    out["text"] = r["text"]
    out["pages"] = json.loads(r["pages_json"]) if r["pages_json"] else None
    out["has_original"] = bool(r["blob_sha256"])
    out["versions"] = [dict(v) for v in conn.execute(
        "SELECT version, content_hash, collected_at, diff_summary FROM document_versions WHERE document_id = ? "
        "ORDER BY version", (doc_id,))]
    out["duplicates"] = [dict(x) for x in conn.execute(
        "SELECT d.id, s.name AS source_name, d.url_publication FROM documents d JOIN sources s ON s.id = d.source_id "
        "WHERE d.content_hash = ? AND d.id != ? ORDER BY d.id", (r["content_hash"], doc_id))]
    if version and version != r["version"]:
        v = conn.execute("SELECT text FROM document_versions WHERE document_id = ? AND version = ?",
                         (doc_id, version)).fetchone()
        if not v:
            raise HTTPException(404, "Versão não encontrada")
        out["text"] = v["text"]
        out["viewing_version"] = version
    return out


@app.get("/api/documents/{doc_id}/original")
def document_original(doc_id: int):
    r = db().execute("SELECT blob_sha256, title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not r or not r["blob_sha256"]:
        raise HTTPException(404, "Arquivo original não armazenado para este documento")
    path = settings.blob_dir / r["blob_sha256"][:2] / r["blob_sha256"]
    data = path.read_bytes()
    is_pdf = data[:5] == b"%PDF-"
    return Response(data, media_type="application/pdf" if is_pdf else "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="documento-{doc_id}.{"pdf" if is_pdf else "bin"}"'})


@app.delete("/api/documents/{doc_id}")
def remove_document(doc_id: int):
    conn = db()
    delete_document(conn, doc_id)
    audit(conn, "documento_removido", {"id": doc_id})
    return {"ok": True}


@app.post("/api/documents/{doc_id}/summary")
def document_summary(doc_id: int):
    conn = db()
    r = conn.execute("SELECT text, title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Documento não encontrado")
    audit(conn, "resumo_ia", {"documento": doc_id})
    return summarize.summarize(r["text"], r["title"])


@app.post("/api/uploads")
async def upload(files: list[UploadFile] = File(...), processo: str = Form(""), tribunal: str = Form(""),
                 data_disponibilizacao: str = Form(""), data_publicacao: str = Form(""),
                 data_decisao: str = Form(""), tipo_ato: str = Form(""), orgao: str = Form(""),
                 url_publicacao: str = Form(""), cidade: str = Form("")):
    conn = db()
    src = conn.execute("SELECT id FROM sources WHERE slug = 'arquivos-enviados'").fetchone()
    if not src:
        raise HTTPException(500, "Fonte 'arquivos-enviados' ausente")
    for d in (data_disponibilizacao, data_publicacao, data_decisao):
        if d and not DATE_RE.match(d):
            raise HTTPException(400, "Datas devem estar no formato AAAA-MM-DD")
    if tribunal and tribunal not in ("TJSP", "TRT15"):
        raise HTTPException(400, "Tribunal fora do escopo (TJSP ou TRT15)")
    city = None
    if cidade:
        city = find_city(cidade)
        if not city:
            raise HTTPException(400, f"Município não encontrado no Estado de São Paulo: {cidade}")
    results = []
    limit = settings.max_download_mb * 1024 * 1024
    for f in files[:50]:
        name = Path(f.filename or "arquivo").name
        if Path(name).suffix.lower() not in UPLOAD_EXT:
            results.append({"arquivo": name, "erro": "Extensão não suportada (PDF, HTML, TXT)"})
            continue
        data = await f.read(limit + 1)
        if len(data) > limit:
            results.append({"arquivo": name, "erro": f"Arquivo maior que {settings.max_download_mb} MB"})
            continue
        ext = extract_bytes(data, f.content_type or "", name)
        meta = DocMeta(title=name, court=tribunal or None, process_number=processo or None,
                       date_available=data_disponibilizacao or None, date_publication=data_publicacao or None,
                       date_decision=data_decisao or None, act_type=tipo_ato or None, organ=orgao or None,
                       url_publication=url_publicacao or None, city=city or city_in_text(orgao),
                       extra={"metadados_informados_pelo_usuario": True, "arquivo": name})
        res = save_document(conn, src["id"], sha256(data), ext, meta, blob=data)
        results.append({"arquivo": name, "resultado": res, "extracao": ext.method, "estado": ext.status,
                        "observacoes": ext.notes, "caracteres": len(ext.text)})
    audit(conn, "upload", {"arquivos": [r["arquivo"] for r in results]})
    return results


@app.get("/api/audit")
def audit_log(limit: int = Query(200, ge=1, le=1000)):
    return [dict(r) for r in db().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


# ------------------------------------------------------------------ front-end
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
mimetypes.add_type("text/javascript", ".js")
