"""Interpretador determinístico do comando de pesquisa.

Regras:
- Prefixo opcional "busque:" (qualquer combinação de maiúsculas/minúsculas).
- Texto entre aspas (" ou “ ”) => FRASE EXATA.
- Palavra sem aspas => SUBSTRING (correspondência parcial).
- Cada termo gera uma pesquisa independente (não há AND/OR implícito).
- Aspas não fechadas ou vazias => erro de sintaxe (nada é alterado em silêncio).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .normalize import normalize_term

QUOTE_CHARS = {'"', "“", "”", "„"}
PREFIX_RE = re.compile(r"^\s*busque\s*:", re.IGNORECASE)

EXACT = "frase_exata"
SUBSTRING = "substring"
MODE_LABEL = {EXACT: "Frase exata", SUBSTRING: "Correspondência parcial (substring)"}


class QuerySyntaxError(ValueError):
    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.position = position


@dataclass
class Term:
    index: int
    raw: str
    mode: str
    normalized: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode_label"] = MODE_LABEL[self.mode]
        return d


@dataclass
class ParsedQuery:
    command: str
    terms: list[Term]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"command": self.command, "terms": [t.to_dict() for t in self.terms],
                "warnings": self.warnings}


def parse(command: str, fold_accents: bool = False) -> ParsedQuery:
    if command is None:
        raise QuerySyntaxError("Comando vazio.")
    if len(command) > 2000:
        raise QuerySyntaxError("Comando muito longo (máximo de 2000 caracteres).")
    body = command
    m = PREFIX_RE.match(body)
    offset = 0
    if m:
        offset = m.end()
        body = body[offset:]
    if not body.strip():
        raise QuerySyntaxError("Informe ao menos um termo. Exemplo: busque: \"defiro o pedido\" deferimento")

    raw_terms: list[tuple[str, str]] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch.isspace():
            i += 1
            continue
        if ch in QUOTE_CHARS:
            start = i
            j = i + 1
            while j < n and body[j] not in QUOTE_CHARS:
                j += 1
            if j >= n:
                raise QuerySyntaxError(
                    f"Aspas não fechadas na posição {offset + start + 1}. "
                    "Feche a expressão com aspas para pesquisar a frase exata.",
                    offset + start)
            phrase = body[i + 1:j]
            if not phrase.strip():
                raise QuerySyntaxError(
                    f"Aspas vazias na posição {offset + start + 1}. Remova-as ou escreva uma expressão entre elas.",
                    offset + start)
            raw_terms.append((phrase, EXACT))
            i = j + 1
            continue
        j = i
        while j < n and not body[j].isspace() and body[j] not in QUOTE_CHARS:
            j += 1
        raw_terms.append((body[i:j], SUBSTRING))
        i = j

    terms: list[Term] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw, mode in raw_terms:
        norm = normalize_term(raw, fold_accents)
        if not norm:
            raise QuerySyntaxError(f"O termo {raw!r} fica vazio após a normalização.")
        key = (mode, norm)
        if key in seen:
            warnings.append(f"O termo {raw!r} foi repetido; a pesquisa duplicada foi executada uma única vez.")
            continue
        seen.add(key)
        if mode == SUBSTRING and len(norm) < 3:
            warnings.append(
                f"O termo {raw!r} tem menos de 3 caracteres; como a busca é por substring, "
                "ele pode corresponder a um grande volume de texto.")
        terms.append(Term(index=len(terms), raw=raw, mode=mode, normalized=norm))
    if len(terms) > 20:
        raise QuerySyntaxError("Máximo de 20 termos por pesquisa.")
    return ParsedQuery(command=command, terms=terms, warnings=warnings)
