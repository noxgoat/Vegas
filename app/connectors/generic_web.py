"""Páginas e PDFs por URL (restrito aos domínios permitidos).

Coleta as URLs cadastradas e, opcionalmente, segue links que casem com uma expressão
regular, até um limite de páginas. HTML e PDF (com OCR) são extraídos integralmente.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from ..extract import extract_bytes
from ..http import AccessError, PolicyError, domain_allowed, ensure_ok, normalize_url
from ..ingest import DocMeta
from .base import Connector, Truncated


class GenericWeb(Connector):
    supports_incremental = True

    def collect(self, params: dict) -> None:
        urls = params.get("urls") or self.config.get("urls") or []
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        if not urls:
            raise ValueError("Nenhuma URL cadastrada nesta fonte (configuração 'urls').")
        pattern = params.get("seguir_links_regex") or self.config.get("seguir_links_regex") or ""
        rx = re.compile(pattern) if pattern else None
        max_pages = int(params.get("max_paginas") or self.config.get("max_paginas") or 20)
        queue = [normalize_url(u) for u in urls]
        seen: set[str] = set()
        done = 0
        while queue:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if done >= max_pages:
                raise Truncated(f"Limite de {max_pages} página(s) atingido; {len(queue) + 1} URL(s) não visitadas.")
            done += 1
            try:
                r = ensure_ok(self.ctx.http.get(url), "Download")
            except PolicyError as e:
                self.ctx.fail_document(url, str(e), url)
                continue
            except AccessError as e:
                self.ctx.fail_document(url, str(e), url)
                continue
            self.ctx.page_examined(url)
            ctype = r.headers.get("content-type", "")
            ext = extract_bytes(r.content, ctype, urlsplit(url).path)
            title = None
            if ext.method == "html":
                m = re.search(rb"<title[^>]*>(.*?)</title>", r.content[:20000], re.I | re.S)
                if m:
                    title = m.group(1).decode("utf-8", "replace").strip()[:300]
            meta = DocMeta(title=title or url, url_publication=url, url_original=url,
                           extra={"content_type": ctype})
            self.ctx.save(url, ext, meta, blob=r.content)
            if rx and ext.links:
                for href in ext.links:
                    nxt = normalize_url(urljoin(url, href))
                    host = urlsplit(nxt).hostname or ""
                    if nxt not in seen and rx.search(nxt) and domain_allowed(host):
                        queue.append(nxt)

    def test_connection(self) -> dict:
        urls = self.config.get("urls") or []
        if not urls:
            return {"ok": False, "erro": "Nenhuma URL cadastrada."}
        r = self.ctx.http.get(urls[0])
        return {"ok": r.status_code < 400, "http_status": r.status_code,
                "content_type": r.headers.get("content-type")}
