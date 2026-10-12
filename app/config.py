"""Configuração lida de variáveis de ambiente (ver config.example.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Na Vercel (funções serverless) o disco do projeto é somente leitura e /tmp é temporário.
ON_VERCEL = bool(os.environ.get("VERCEL"))


def _env_list(name: str, default: str) -> list[str]:
    return [x.strip().lower() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("JURIS_DATA_DIR", "/tmp/jurisbusca" if ON_VERCEL else BASE_DIR / "data")))
    host: str = os.environ.get("JURIS_HOST", "127.0.0.1")
    port: int = int(os.environ.get("JURIS_PORT", "8000"))
    # Se definido, todas as rotas /api exigem o cabeçalho X-App-Token.
    app_token: str | None = os.environ.get("JURIS_APP_TOKEN") or None
    # Domínios que os conectores podem acessar (escopo: São Paulo – TJSP e TRT-15).
    allowed_domains: list[str] = field(default_factory=lambda: _env_list(
        "JURIS_ALLOWED_DOMAINS",
        "tjsp.jus.br,trt15.jus.br,jt.jus.br,comunicaapi.pje.jus.br,comunica.pje.jus.br"))
    user_agent: str = os.environ.get(
        "JURIS_USER_AGENT", "JurisBusca/1.0 (pesquisa juridica pessoal; uso nao comercial)")
    max_download_mb: int = int(os.environ.get("JURIS_MAX_DOWNLOAD_MB", "50"))
    # Proxy HTTP opcional para as requisições às fontes (ex.: saída por um IP no Brasil).
    # Formato: http://host:porta  ou  http://usuario:senha@host:porta. Configure no ambiente, nunca no código.
    proxy_url: str | None = os.environ.get("JURIS_PROXY") or None
    ocr_enabled: bool = os.environ.get("JURIS_OCR", "1") == "1"
    ocr_lang: str = os.environ.get("JURIS_OCR_LANG", "por")
    ocr_min_confidence: float = float(os.environ.get("JURIS_OCR_MIN_CONF", "60"))
    retention_days_searches: int = int(os.environ.get("JURIS_RETENCAO_PESQUISAS_DIAS", "180"))
    retention_days_logs: int = int(os.environ.get("JURIS_RETENCAO_LOGS_DIAS", "365"))
    scheduler_enabled: bool = os.environ.get("JURIS_AGENDADOR", "0" if ON_VERCEL else "1") == "1"
    # Modo temporário (Vercel): acervo em /tmp, sem tarefas em segundo plano, coletas curtas.
    temporary_mode: bool = ON_VERCEL
    # Escopo: somente despachos e decisões de magistrado (tipo declarado pela fonte).
    only_judge_acts: bool = os.environ.get("JURIS_SOMENTE_DESPACHO_DECISAO", "1") == "1"
    temporary_max_pages: int = int(os.environ.get("JURIS_MAX_PAGINAS_TEMPORARIO", "2"))
    snippet_context: int = int(os.environ.get("JURIS_CONTEXTO_TRECHO", "220"))
    anthropic_model: str = os.environ.get("JURIS_MODELO_IA", "claude-opus-5-5")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "juris.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "originais"


settings = Settings()
