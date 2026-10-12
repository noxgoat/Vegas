"""Municípios do Estado de São Paulo (lista oficial do IBGE) e utilitários de cidade."""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "municipios_sp.json"


def fold(s: str) -> str:
    """Minúsculas, sem acentos, hífens/apóstrofos como espaço e espaços simples."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[-'’`´]", " ", s)
    return " ".join(s.split())


@lru_cache(maxsize=1)
def municipalities() -> dict[str, str]:
    """{nome_dobrado: nome_oficial}"""
    names = json.loads(DATA.read_text(encoding="utf-8"))["municipios"]
    return {fold(n): n for n in names}


def find_city(text: str) -> str | None:
    """Nome oficial do município se `text` for exatamente um município de SP (sem diferenciar acentos)."""
    t = fold(text).strip(" .,;:!?")
    t = re.sub(r"^(cidade|municipio|comarca) de ", "", t)
    if t.endswith(" sp"):
        t = t[:-3].strip(" /-,")
    return municipalities().get(t)


def suggest(text: str, n: int = 3) -> list[str]:
    m = municipalities()
    return [m[k] for k in difflib.get_close_matches(fold(text), list(m), n=n, cutoff=0.75)]


def city_in_text(text: str | None) -> str | None:
    """Município de SP citado em um nome de órgão (ex.: "1ª Vara Cível de Campinas").

    Procura o nome após "de/da/do/dos/das" (a forma usual em nomes de varas e foros) e
    escolhe a correspondência mais longa, para não confundir "São José" com
    "São José dos Campos".
    """
    if not text:
        return None
    t = " " + fold(text) + " "
    best = None
    for key, name in municipalities().items():
        for prep in (" de ", " da ", " do ", " dos ", " das "):
            if f"{prep}{key} " in t or t.endswith(f"{prep}{key} "):
                if best is None or len(key) > len(fold(best)):
                    best = name
                break
    return best
