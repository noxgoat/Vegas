"""Normalização textual documentada e determinística.

A MESMA função é aplicada ao texto armazenado e aos termos da consulta.
Cada caractere do texto normalizado guarda o intervalo [início, fim) do
caractere de origem no texto armazenado, o que permite destacar exatamente o
trecho original que comprova a correspondência.

Política (ver docs/NORMALIZACAO.md):

1. O texto armazenado está em Unicode NFC (aplicado na extração).
2. Caracteres invisíveis são descartados: hífen condicional (U+00AD),
   espaços de largura zero (U+200B–U+200D), U+2060 e BOM (U+FEFF).
3. Hifenização de fim de linha: "defe-\\nrimento" -> "deferimento", somente
   quando o hífen está entre uma letra e uma quebra de linha e a próxima
   linha começa com letra minúscula.
4. Qualquer sequência de espaços em branco (espaço, tab, NBSP, quebras de
   linha, \\f) vira um único espaço.
5. Maiúsculas/minúsculas: comparação com str.lower().
6. Variantes tipográficas: aspas curvas -> aspas retas; travessões e hífens
   Unicode (U+2010–U+2015, U+2212) -> "-".
7. Acentos são PRESERVADOS. Somente no modo opcional "ignorar acentos" os
   diacríticos são removidos (NFD + remoção de marcas combinantes; ç -> c).
8. A pontuação é preservada. Nenhuma palavra é removida ou reordenada.
"""
from __future__ import annotations

import unicodedata
from array import array

INVISIBLE = {"­", "​", "‌", "‍", "⁠", "﻿"}
HYPHENS = {"-", "‐", "‑"}
CHAR_MAP = {
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'",
    "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "‐": "-", "‑": "-",
}


class Normalized:
    """Texto normalizado + mapeamento para o texto original."""

    __slots__ = ("text", "starts", "ends", "original")

    def __init__(self, text: str, starts: array, ends: array, original: str):
        self.text = text
        self.starts = starts
        self.ends = ends
        self.original = original

    def original_span(self, a: int, b: int) -> tuple[int, int]:
        """Converte [a, b) do texto normalizado em [início, fim) do original."""
        if b <= a:
            raise ValueError("intervalo vazio")
        return self.starts[a], self.ends[b - 1]


def _fold_char(ch: str) -> str:
    decomposed = unicodedata.normalize("NFD", ch)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _is_line_break(ch: str) -> bool:
    return ch in "\n\r  \x0b\x0c\x85"


def normalize(original: str, fold_accents: bool = False) -> Normalized:
    out: list[str] = []
    starts = array("q")
    ends = array("q")
    n = len(original)
    i = 0

    def emit(s: str, a: int, b: int) -> None:
        for c in s:
            out.append(c)
            starts.append(a)
            ends.append(b)

    while i < n:
        ch = original[i]
        if ch in INVISIBLE:
            i += 1
            continue
        # Regra 3: hifenização em fim de linha.
        if ch in HYPHENS and out and out[-1].isalpha():
            j = i + 1
            while j < n and original[j] in " \t":
                j += 1
            if j < n and _is_line_break(original[j]):
                k = j
                while k < n and original[k].isspace():
                    k += 1
                if k < n and original[k].isalpha() and original[k].islower():
                    i = k
                    continue
        # Regra 4: espaços em branco.
        if ch.isspace():
            j = i
            while j < n and (original[j].isspace() or original[j] in INVISIBLE):
                j += 1
            if out and out[-1] == " ":
                ends[-1] = j
            else:
                emit(" ", i, j)
            i = j
            continue
        c = CHAR_MAP.get(ch, ch).lower()
        if fold_accents:
            c = _fold_char(c)
        if c:
            emit(c, i, i + 1)
        i += 1
    return Normalized("".join(out), starts, ends, original)


def normalize_term(term: str, fold_accents: bool = False) -> str:
    """Normaliza um termo de consulta (sem mapeamento) e remove espaços nas bordas."""
    return normalize(term, fold_accents).text.strip(" ")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"
