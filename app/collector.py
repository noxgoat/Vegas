"""Orquestração das coletas: execução, checkpoints, logs, estados e agendamento."""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .config import settings
from .connectors.base import NotApplicable, Truncated
from .connectors.registry import CONNECTORS
from .db import audit, get_conn, now_iso
from .http import AccessError, PolicyError, SafeHttp
from .ingest import save_document

executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="coleta")
_running: dict[int, int] = {}           # source_id -> collection_run_id
_running_lock = threading.Lock()
_cancel: dict[int, threading.Event] = {}


class Cancelled(Exception):
    pass


class CollectionContext:
    def __init__(self, conn, run_id: int, source: dict, http: SafeHttp, cancel: threading.Event):
        self.conn = conn
        self.run_id = run_id
        self.source = source
        self.http = http
        self.cancel = cancel
        self.counters = {"pages_examined": 0, "docs_found": 0, "docs_new": 0, "docs_updated": 0,
                         "docs_unchanged": 0, "docs_failed": 0, "docs_partial": 0}

    def log(self, level: str, message: str, url: str | None = None) -> None:
        self.conn.execute("INSERT INTO collection_logs (collection_run_id, at, level, message, url) VALUES (?,?,?,?,?)",
                          (self.run_id, now_iso(), level, message[:4000], url))

    def _flush(self) -> None:
        sets = ", ".join(f"{k} = ?" for k in self.counters)
        self.conn.execute(f"UPDATE collection_runs SET {sets} WHERE id = ?", [*self.counters.values(), self.run_id])

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("Coleta cancelada pelo usuário.")

    def page_examined(self, what: str) -> None:
        self._check_cancel()
        self.counters["pages_examined"] += 1
        self.log("info", f"Página examinada: {what}")
        self._flush()

    def set_total(self, total: int | None) -> None:
        self.conn.execute("UPDATE collection_runs SET total_reported = ? WHERE id = ?", (total, self.run_id))

    def save(self, external_id: str, extraction, meta, blob: bytes | None = None) -> str:
        self._check_cancel()
        self.counters["docs_found"] += 1
        res = save_document(self.conn, self.source["id"], str(external_id), extraction, meta, blob, self.run_id)
        key = {"novo": "docs_new", "atualizado": "docs_updated", "inalterado": "docs_unchanged"}[res]
        self.counters[key] += 1
        if extraction.status == "parcial" or meta.text_scope == "parcial":
            self.counters["docs_partial"] += 1
        if extraction.status == "falha":
            self.counters["docs_failed"] += 1
            self.log("aviso", f"Documento {external_id} sem texto extraído: {'; '.join(extraction.notes)}")
        self._flush()
        return res

    def fail_document(self, external_id: str, error: str, url: str | None = None) -> None:
        self.counters["docs_failed"] += 1
        self.log("erro", f"Documento {external_id} inacessível: {error}", url)
        self._flush()

    def get_checkpoint(self) -> dict | None:
        row = self.conn.execute("SELECT checkpoint_json FROM collection_runs WHERE id = ?", (self.run_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def checkpoint(self, data: dict) -> None:
        self.conn.execute("UPDATE collection_runs SET checkpoint_json = ? WHERE id = ?",
                          (json.dumps(data, ensure_ascii=False), self.run_id))


def source_dict(conn, source_id: int) -> dict:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise KeyError(f"Fonte {source_id} não encontrada")
    d = dict(row)
    d["config"] = json.loads(d.get("config_json") or "{}")
    return d


def is_running(source_id: int) -> int | None:
    with _running_lock:
        return _running.get(source_id)


def start_run_record(conn, source_id: int, trigger: str, params: dict, search_run_id: int | None = None,
                     resume_from: int | None = None) -> int:
    checkpoint = None
    if resume_from:
        r = conn.execute("SELECT checkpoint_json, params_json FROM collection_runs WHERE id = ? AND source_id = ?",
                         (resume_from, source_id)).fetchone()
        if r is None:
            raise KeyError("Coleta para retomada não encontrada")
        checkpoint = r["checkpoint_json"]
        params = json.loads(r["params_json"])
    cur = conn.execute(
        "INSERT INTO collection_runs (source_id, trigger, params_json, status, started_at, checkpoint_json, "
        "search_run_id) VALUES (?,?,?,?,?,?,?)",
        (source_id, trigger, json.dumps(params, ensure_ascii=False), "em_andamento", now_iso(), checkpoint,
         search_run_id))
    return cur.lastrowid


def run_collection(source_id: int, trigger: str = "manual", params: dict | None = None,
                   search_run_id: int | None = None, resume_from: int | None = None,
                   transport=None) -> dict:
    """Executa uma coleta de forma síncrona. Nunca considera falha como sucesso."""
    conn = get_conn()
    params = dict(params or {})
    source = source_dict(conn, source_id)
    with _running_lock:
        if source_id in _running:
            raise RuntimeError("Já existe uma coleta em andamento para esta fonte.")
        _running[source_id] = -1
    cancel = _cancel.setdefault(source_id, threading.Event())
    cancel.clear()
    run_id = None
    try:
        run_id = start_run_record(conn, source_id, trigger, params, search_run_id, resume_from)
        with _running_lock:
            _running[source_id] = run_id
        if resume_from:
            params = json.loads(conn.execute("SELECT params_json FROM collection_runs WHERE id = ?",
                                             (run_id,)).fetchone()[0])
        conn.execute("UPDATE sources SET last_attempt_at = ? WHERE id = ?", (now_iso(), source_id))
        cls = CONNECTORS.get(source["connector"])
        status, error, message = "sucesso", None, None
        http = SafeHttp(min_interval=float(source["config"].get("intervalo_seg", 2.0)),
                        max_retries=int(source["max_retries"] or 3), transport=transport)
        ctx = CollectionContext(conn, run_id, source, http, cancel)
        ctx.log("info", f"Coleta iniciada ({trigger}) com parâmetros {json.dumps(params, ensure_ascii=False)}")
        try:
            if cls is None:
                raise ValueError(f"A fonte não possui conector de coleta automática ({source['connector']}).")
            cls(source, source["config"], ctx).collect(params)
        except Truncated as e:
            status, message = "parcial", str(e)
            ctx.log("aviso", message)
        except NotApplicable as e:
            status, message = "nao_aplicavel", str(e)
            ctx.log("info", message)
        except Cancelled as e:
            status, error = "cancelada", str(e)
            ctx.log("aviso", error)
        except PolicyError as e:
            status, error = "falha", f"Recusado por política: {e}"
            ctx.log("erro", error, e.url)
        except AccessError as e:
            status, error = "falha", str(e)
            ctx.log("erro", error, e.url)
        except Exception as e:  # erro inesperado: registra e marca como falha
            status, error = "falha", f"{type(e).__name__}: {e}"
            ctx.log("erro", error + "\n" + traceback.format_exc()[-2000:])
        finally:
            http.close()
        if status == "sucesso" and (ctx.counters["docs_failed"] or ctx.counters["docs_partial"]):
            status = "parcial"
            message = (message or "") + (f" {ctx.counters['docs_failed']} documento(s) com falha e "
                                         f"{ctx.counters['docs_partial']} parcialmente processado(s).")
        ctx._flush()
        conn.execute("UPDATE collection_runs SET status = ?, finished_at = ?, error = ?, message = ? WHERE id = ?",
                     (status, now_iso(), error, (message or "").strip() or None, run_id))
        if status == "sucesso":
            conn.execute("UPDATE sources SET last_success_at = ?, last_error = NULL, status = 'ativa', "
                         "status_message = CASE WHEN status = 'nao_validada' THEN ? ELSE status_message END "
                         "WHERE id = ?", (now_iso(), f"Validada por coleta bem-sucedida em {now_iso()}", source_id))
        elif status == "parcial":
            conn.execute("UPDATE sources SET status_message = ?, status = 'ativa' WHERE id = ?",
                         (f"Última coleta parcial: {message}", source_id))
        elif status == "falha":
            access = error and not error.startswith(("ValueError", "Recusado"))
            conn.execute("UPDATE sources SET last_error = ?, status = CASE WHEN ? THEN 'falha_acesso' ELSE status END "
                         "WHERE id = ?", (error, 1 if access else 0, source_id))
        audit(conn, "coleta_finalizada", {"fonte": source["slug"], "coleta": run_id, "status": status}, "sistema")
        row = conn.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row)
    finally:
        with _running_lock:
            _running.pop(source_id, None)


def submit_collection(source_id: int, **kw):
    return executor.submit(run_collection, source_id, **kw)


def cancel_collection(source_id: int) -> bool:
    ev = _cancel.get(source_id)
    if ev and is_running(source_id):
        ev.set()
        return True
    return False


def test_source(source_id: int) -> dict:
    conn = get_conn()
    source = source_dict(conn, source_id)
    cls = CONNECTORS.get(source["connector"])
    if cls is None:
        return {"ok": False, "erro": "Fonte sem conector de rede."}
    http = SafeHttp(min_interval=1.0, max_retries=1)
    ctx = type("Ctx", (), {"http": http, "log": lambda *a, **k: None})()
    try:
        res = cls(source, source["config"], ctx).test_connection()
    except Exception as e:
        res = {"ok": False, "erro": str(e)}
    finally:
        http.close()
    audit(conn, "teste_conexao", {"fonte": source["slug"], "ok": res.get("ok")})
    return res


# ---------------------------------------------------------------- agendador
class Scheduler(threading.Thread):
    """Dispara coletas periódicas conforme schedule_minutes de cada fonte."""

    def __init__(self, interval: int = 60):
        super().__init__(daemon=True, name="agendador")
        self.interval = interval
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.tick()
            except Exception:
                traceback.print_exc()

    def tick(self) -> list[int]:
        conn = get_conn()
        started = []
        now = datetime.now(timezone.utc)
        for s in conn.execute("SELECT * FROM sources WHERE enabled = 1 AND schedule_minutes > 0"):
            if s["connector"] not in CONNECTORS or is_running(s["id"]):
                continue
            last = conn.execute("SELECT status, started_at FROM collection_runs WHERE source_id = ? AND trigger = "
                                "'agendada' ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()
            wait = s["schedule_minutes"]
            if last and last["status"] == "falha":
                wait = min(wait, s["retry_minutes"] or wait)   # política de nova tentativa
            if last:
                elapsed = (now - datetime.strptime(last["started_at"], "%Y-%m-%dT%H:%M:%SZ")
                           .replace(tzinfo=timezone.utc)).total_seconds() / 60
                if elapsed < wait:
                    continue
            cfg = json.loads(s["config_json"] or "{}")
            terms = cfg.get("termos_agendados") or []
            if s["connector"] in ("tjsp_cjpg", "tjsp_dje"):
                if not terms:
                    continue
                params = {"query": terms[0]}   # um termo por ciclo, em rodízio
                idx = conn.execute("SELECT COUNT(*) FROM collection_runs WHERE source_id = ? AND trigger='agendada'",
                                   (s["id"],)).fetchone()[0]
                params["query"] = terms[idx % len(terms)]
            else:
                params = {}
            submit_collection(s["id"], trigger="agendada", params=params)
            started.append(s["id"])
        return started


scheduler: Scheduler | None = None


def start_scheduler() -> None:
    global scheduler
    if settings.scheduler_enabled and scheduler is None:
        scheduler = Scheduler()
        scheduler.start()


def wait_idle(timeout: float = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with _running_lock:
            if not _running:
                return
        time.sleep(0.2)
