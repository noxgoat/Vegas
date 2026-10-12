"""Cliente HTTP seguro para os conectores.

- Somente http/https e somente domínios permitidos (escopo SP).
- Proteção contra SSRF: o host é resolvido e IPs privados/locais são recusados.
- Respeita robots.txt (quando existe; 404 = sem restrições).
- Intervalo mínimo entre requisições por host, novas tentativas com recuo
  exponencial (429, 5xx e falhas de rede) e respeito a Retry-After.
- Limite de tamanho de download.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from .config import settings


class AccessError(Exception):
    """Falha de acesso à fonte (rede, bloqueio, HTTP de erro)."""

    def __init__(self, message: str, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


class PolicyError(AccessError):
    """Requisição recusada por política (domínio, robots.txt, SSRF)."""


_host_lock = threading.Lock()
_host_last: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def validated_proxy(url: str | None) -> str | None:
    """Aceita somente proxies http(s)://[usuario:senha@]host:porta."""
    if not url:
        return None
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or not p.port or p.path not in ("", "/"):
        raise PolicyError("JURIS_PROXY inválido: use o formato http://host:porta")
    return url.strip()


def proxy_label(url: str | None) -> str | None:
    """Identificação do proxy sem usuário/senha (para exibir na interface)."""
    if not url:
        return None
    p = urlsplit(url)
    return f"{p.hostname}:{p.port}"


def normalize_url(url: str) -> str:
    """URL canônica para deduplicação: esquema/host minúsculos, sem fragmento, query ordenada."""
    p = urlsplit(url.strip())
    query = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    path = p.path or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, query, ""))


def domain_allowed(host: str, allowed: list[str] | None = None) -> bool:
    host = (host or "").lower().rstrip(".")
    for d in (allowed if allowed is not None else settings.allowed_domains):
        if host == d or host.endswith("." + d):
            return True
    return False


def check_ip_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Sem DNS local (ex.: atrás de proxy): a restrição por domínio continua valendo.
        return
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise PolicyError(f"Endereço não público recusado (proteção SSRF): {host} -> {ip}")


class SafeHttp:
    def __init__(self, min_interval: float = 2.0, max_retries: int = 4, timeout: float = 45.0,
                 transport: httpx.BaseTransport | None = None, check_robots: bool = True,
                 allowed_domains: list[str] | None = None, check_dns: bool = True, log=None):
        self.min_interval = max(0.0, float(min_interval))
        self.max_retries = max_retries
        self.check_robots = check_robots and transport is None
        self.check_dns = check_dns and transport is None
        self.allowed = allowed_domains
        self.log = log or (lambda level, msg, url=None: None)
        proxy = None if transport is not None else validated_proxy(settings.proxy_url)
        self.client = httpx.Client(
            timeout=timeout, follow_redirects=False, transport=transport, proxy=proxy,
            headers={"User-Agent": settings.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"})
        self.max_bytes = settings.max_download_mb * 1024 * 1024

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- políticas -----------------------------------------------------
    def _check_url(self, url: str) -> None:
        p = urlsplit(url)
        if p.scheme not in ("http", "https"):
            raise PolicyError(f"Esquema não permitido: {p.scheme}", url=url)
        if not p.hostname or not domain_allowed(p.hostname, self.allowed):
            raise PolicyError(f"Domínio fora do escopo permitido: {p.hostname}", url=url)
        if self.check_dns:
            check_ip_public(p.hostname)
        if self.check_robots and not self._robots_ok(url):
            raise PolicyError("Acesso não permitido pelo robots.txt da fonte", url=url)

    def _robots_ok(self, url: str) -> bool:
        p = urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in _robots_cache:
            rp = None
            try:
                r = self.client.get(base + "/robots.txt", timeout=20)
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and "text/plain" in ctype:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            _robots_cache[base] = rp
        rp = _robots_cache[base]
        return True if rp is None else rp.can_fetch(settings.user_agent, url)

    def _throttle(self, host: str) -> None:
        with _host_lock:
            last = _host_last.get(host, 0.0)
            wait = last + self.min_interval - time.monotonic()
            _host_last[host] = max(last + self.min_interval, time.monotonic())
        if wait > 0:
            time.sleep(wait)

    # -- requisição ------------------------------------------------------
    def request(self, method: str, url: str, *, max_redirects: int = 5, **kw) -> httpx.Response:
        for _ in range(max_redirects + 1):
            resp = self._request_once(method, url, **kw)
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = str(resp.url.join(resp.headers["location"]))
                if resp.status_code == 303 or (resp.status_code in (301, 302) and method == "POST"):
                    method, kw = "GET", {k: v for k, v in kw.items() if k not in ("data", "json", "content")}
                continue
            return resp
        raise AccessError("Redirecionamentos em excesso", url=url)

    def _request_once(self, method: str, url: str, **kw) -> httpx.Response:
        self._check_url(url)
        host = urlsplit(url).hostname or ""
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                with self.client.stream(method, url, **kw) as resp:
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise AccessError(f"Resposta maior que o limite de {settings.max_download_mb} MB", url=url)
                        chunks.append(chunk)
                    resp._content = b"".join(chunks)  # noqa: SLF001 - conteúdo já lido
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = resp.headers.get("retry-after")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    self.log("aviso", f"HTTP {resp.status_code}; nova tentativa em {wait:.0f}s "
                                      f"({attempt + 1}/{self.max_retries})", url)
                    last_exc = AccessError(f"HTTP {resp.status_code}", status=resp.status_code, url=url)
                    if attempt < self.max_retries:
                        time.sleep(min(wait, 120))
                        delay *= 2
                        continue
                    raise last_exc
                return resp
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                self.log("aviso", f"Falha de rede: {e!s}; tentativa {attempt + 1}/{self.max_retries}", url)
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
        raise AccessError(f"Falha de acesso após {self.max_retries + 1} tentativas: {last_exc}", url=url)

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)


def ensure_ok(resp: httpx.Response, what: str = "requisição") -> httpx.Response:
    if resp.status_code >= 400:
        hint = ""
        body = resp.text[:2000].lower() if resp.content else ""
        if resp.status_code == 403 and "cloudfront" in body:
            hint = " (bloqueio geográfico/CloudFront: a fonte recusa acessos de fora do Brasil)"
        raise AccessError(f"{what}: HTTP {resp.status_code}{hint}", status=resp.status_code, url=str(resp.url))
    return resp
