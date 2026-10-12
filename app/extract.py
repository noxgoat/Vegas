"""Extração de texto de HTML, PDF (texto digital) e PDF digitalizado (OCR em português)."""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .config import settings
from .normalize import nfc

BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table",
              "section", "article", "blockquote", "pre", "td", "th", "dd", "dt", "hr"}


@dataclass
class Extraction:
    text: str
    method: str                      # html | pdf_texto | ocr | pdf_misto | texto | api_texto
    status: str                      # completo | parcial | falha
    notes: list[str] = field(default_factory=list)
    pages: list[list[int]] | None = None   # [[numero_pagina, inicio, fim], ...]
    ocr_confidence: float | None = None
    links: list[str] = field(default_factory=list)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def html_to_text(html: str | bytes, base_url: str | None = None) -> Extraction:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        links.append(a["href"])
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    text = clean_text(text)
    status = "completo" if text.strip() else "falha"
    return Extraction(text=text, method="html", status=status, links=links,
                      notes=[] if text.strip() else ["Nenhum texto encontrado no HTML"])


def clean_text(text: str) -> str:
    """Arrumação leve e documentada: NFC, CRLF->LF, espaços nas bordas das linhas e no máximo 2 quebras."""
    text = nfc(text).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip(" \t ") for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fragment_html_to_text(fragment: str) -> str:
    """Converte trechos com <br> e marcação simples (ex.: textos de APIs) em texto."""
    if "<" not in fragment:
        return clean_text(fragment)
    return html_to_text(f"<div>{fragment}</div>").text


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_image(img, lang: str | None = None) -> tuple[str, float | None]:
    import pytesseract
    lang = lang or settings.ocr_lang
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    text = pytesseract.image_to_string(img, lang=lang)
    conf = sum(confs) / len(confs) if confs else None
    return text, conf


def pdf_to_text(data: bytes, min_chars_per_page: int = 25, ocr: bool | None = None) -> Extraction:
    import pymupdf as fitz

    ocr = settings.ocr_enabled if ocr is None else ocr
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # PDF corrompido
        return Extraction(text="", method="pdf_texto", status="falha", notes=[f"PDF ilegível: {e}"])
    parts: list[str] = []
    pages: list[list[int]] = []
    notes: list[str] = []
    methods: set[str] = set()
    confs: list[float] = []
    partial = False
    pos = 0
    tess = None
    for i, page in enumerate(doc):
        txt = page.get_text("text") or ""
        method = "pdf_texto"
        if len(txt.strip()) < min_chars_per_page:
            has_images = bool(page.get_images(full=False))
            if has_images or not txt.strip():
                if tess is None:
                    tess = ocr and _tesseract_available()
                if tess:
                    try:
                        from PIL import Image
                        pix = page.get_pixmap(dpi=300)
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        o_txt, conf = ocr_image(img)
                        method = "ocr"
                        if conf is not None:
                            confs.append(conf)
                        if not o_txt.strip():
                            partial = True
                            notes.append(f"Página {i + 1}: OCR não reconheceu texto")
                        elif conf is not None and conf < settings.ocr_min_confidence:
                            partial = True
                            notes.append(f"Página {i + 1}: OCR com baixa confiança ({conf:.0f}%)")
                        txt = o_txt
                    except Exception as e:
                        partial = True
                        notes.append(f"Página {i + 1}: falha no OCR ({e})")
                else:
                    partial = True
                    notes.append(f"Página {i + 1}: sem camada de texto e OCR indisponível")
        methods.add(method)
        txt = clean_text(txt)
        if parts:
            parts.append("\n\n")
            pos += 2
        start = pos
        parts.append(txt)
        pos += len(txt)
        pages.append([i + 1, start, pos])
    text = "".join(parts)
    if methods == {"ocr"}:
        m = "ocr"
    elif "ocr" in methods:
        m = "pdf_misto"
    else:
        m = "pdf_texto"
    status = "falha" if not text.strip() else ("parcial" if partial else "completo")
    return Extraction(text=text, method=m, status=status, notes=notes, pages=pages,
                      ocr_confidence=(sum(confs) / len(confs)) if confs else None)


def extract_bytes(data: bytes, content_type: str = "", filename: str = "") -> Extraction:
    ct = (content_type or "").lower()
    fn = filename.lower()
    if data[:5] == b"%PDF-" or "pdf" in ct or fn.endswith(".pdf"):
        return pdf_to_text(data)
    if "html" in ct or fn.endswith((".html", ".htm")) or data.lstrip()[:1] == b"<":
        return html_to_text(_decode(data, ct))
    text = clean_text(_decode(data, ct))
    return Extraction(text=text, method="texto", status="completo" if text else "falha")


def _decode(data: bytes, ct: str) -> str:
    m = re.search(r"charset=([\w-]+)", ct or "")
    for enc in ([m.group(1)] if m else []) + ["utf-8", "cp1252", "latin-1"]:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")
