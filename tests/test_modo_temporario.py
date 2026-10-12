"""Modo temporário (hospedagem na Vercel)."""
from fastapi.testclient import TestClient

from app import jobs
from app.config import settings


def test_sem_token_bloqueia_e_status_informa(conn, monkeypatch):
    from app.api import app
    monkeypatch.setattr(settings, "temporary_mode", True)
    monkeypatch.setattr(settings, "app_token", None)
    with TestClient(app) as c:
        st = c.get("/api/status").json()
        assert st["modo_temporario"] is True and st["token_ausente"] is True
        r = c.post("/api/searches", json={"command": "defer"})
        assert r.status_code == 503 and "JURIS_APP_TOKEN" in r.json()["detail"]


def test_pesquisa_sincrona_respeita_limite_de_tempo(conn, monkeypatch):
    from app.api import app
    monkeypatch.setattr(settings, "temporary_mode", True)
    monkeypatch.setattr(settings, "app_token", "t")
    monkeypatch.setattr(jobs, "TEMPORARY_TIME_BUDGET", -1)   # tempo já esgotado: nenhuma consulta online
    with TestClient(app) as c:
        h = {"X-App-Token": "t"}
        r = c.post("/api/searches", json={"command": '"defiro o pedido" defer', "online": True,
                                          "online_max_pages": 50}, headers=h)
        run = c.get(f"/api/searches/{r.json()['run_id']}", headers=h).json()
        assert run["status"] == "concluida"            # concluída na própria requisição
        assert run["params"]["online_max_pages"] == settings.temporary_max_pages
        cjpg = next(x for x in run["coverage"] if x["name"].startswith("TJSP — Consulta de Julgados"))
        online = cjpg["consultada_online"]
        assert online["status"] == "parcial" and online["paginas_examinadas"] == 0
        assert any("limite de tempo" in m for m in online["mensagens"])
