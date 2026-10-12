"""Interpretação determinística das mensagens da conversa.

Exemplos aceitos:
    procure "defiro o pedido" em Campinas
    busque decisões com "tutela de urgência" e alvará na comarca de Santos
    "levantamento de valores" indeferimento cidade: São José dos Campos
    busque: "defiro o pedido" defer            (modo estrito: idêntico ao comando original)

Regras (sempre exibidas ao usuário junto com o resultado):
- Texto entre aspas = frase exata; palavra sem aspas = substring (como no comando original).
- Modo conversa: palavras de comando no início ("procure", "busque decisões com", ...) e
  conectivos soltos ("e", "ou", "com") são ignorados e listados como ignorados.
- Cidade: "em <município>", "na cidade de", "no município de", "na comarca de" ao final, ou
  "cidade: <município>". O município precisa existir na lista oficial do IBGE para SP.
- Data: "em 05/10/2026", "de 01/09/2026 a 30/09/2026", "desde 01/09/2026", "até 30/09/2026".
- Se algo não puder ser entendido com segurança, a mensagem é recusada com uma explicação —
  nada é alterado em silêncio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cities import find_city, fold, suggest
from .query_parser import PREFIX_RE, QUOTE_CHARS, ParsedQuery, QuerySyntaxError, parse

VERBS = ["por favor", "me mostre", "me mostra", "me traga", "me de", "me dê", "gostaria de ver", "quero ver",
         "quero", "busque", "buscar", "busca", "procure", "procurar", "procura", "pesquise", "pesquisar",
         "pesquisa", "encontre", "encontrar", "ache", "achar", "mostre", "mostrar", "liste", "listar", "traga"]
FILLER = {"me", "as", "os", "a", "o", "todas", "todos", "decisoes", "decisao", "despachos", "despacho",
          "sentencas", "acordaos", "publicacoes", "processos", "intimacoes", "que", "contenham", "contem",
          "contendo", "com", "por", "pelo", "pela", "sobre", "termo", "termos", "palavra", "palavras", "expressao",
          "expressoes", "de", "do", "da", "dos", "das", "e", "onde", "apareca", "aparecam", "aparece", "mencionem",
          "citem", "falem", "tenham", "tem", "no", "na", "nos", "nas", "estado", "sp", "sao", "paulo"}
CONNECTORS = {"e", "ou", "com", "mais", "tambem", "a", "o", "os", "as"}
CITY_MARKERS = ["na cidade de", "no municipio de", "na comarca de", "da comarca de", "na cidade", "em"]


class InterpretError(ValueError):
    pass


@dataclass
class Interpretation:
    mode: str                                  # conversa | estrito
    parsed: ParsedQuery
    city: str | None = None
    ignored: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None

    def to_dict(self) -> dict:
        d = self.parsed.to_dict()
        d.update({"modo": self.mode, "cidade": self.city, "ignoradas": self.ignored,
                  "data_inicial": self.date_from, "data_final": self.date_to})
        return d


def _tokens(text: str) -> list[tuple[str, bool]]:
    """[(texto, entre_aspas)] — aspas não fechadas geram erro."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in QUOTE_CHARS:
            j = i + 1
            while j < n and text[j] not in QUOTE_CHARS:
                j += 1
            if j >= n:
                raise QuerySyntaxError(f"Aspas não fechadas na posição {i + 1}. Feche a expressão com aspas "
                                       "para pesquisar a frase exata.", i)
            out.append((text[i + 1:j], True))
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in QUOTE_CHARS:
            j += 1
        out.append((text[i:j], False))
        i = j
    return out


def _join(tokens: list[tuple[str, bool]]) -> str:
    return " ".join(f'"{t}"' if q else t for t, q in tokens)


def _extract_city(tokens, natural: bool):
    """Remove a indicação de cidade do final da mensagem. Retorna (tokens, cidade, texto_ignorado)."""
    # cidade: <município>
    for k, (t, q) in enumerate(tokens):
        if not q and fold(t).startswith("cidade:"):
            rest = [t[len("cidade:"):]] if len(t) > len("cidade:") else []
            rest += [x for x, _ in tokens[k + 1:]]
            name = " ".join(x for x in rest if x).strip()
            city = find_city(name)
            if not city:
                hint = suggest(name)
                raise InterpretError(f"Não encontrei o município {name!r} no Estado de São Paulo."
                                     + (f" Você quis dizer: {', '.join(hint)}?" if hint else ""))
            return tokens[:k], city, f"cidade: {name}"
    if not natural:
        return tokens, None, None
    # ... em <município>  (somente palavras sem aspas no final)
    tail_start = len(tokens)
    while tail_start > 0 and not tokens[tail_start - 1][1]:
        tail_start -= 1
    words = [t for t, _ in tokens[tail_start:]]
    folded = [fold(w) for w in words]
    for k in range(len(words)):
        for marker in CITY_MARKERS:
            mk = marker.split()
            if folded[k:k + len(mk)] == mk and k + len(mk) < len(words):
                name_words = words[k + len(mk):]
                if len(name_words) > 6:
                    continue
                name = " ".join(name_words).strip(" ,.;")
                city = find_city(name)
                if city:
                    return tokens[:tail_start + k], city, " ".join(words[k:])
                if marker == "em":
                    hint = suggest(name)
                    raise InterpretError(
                        f"Entendi \"em {name}\" como cidade, mas não encontrei esse município no Estado de São Paulo."
                        + (f" Você quis dizer: {', '.join(hint)}?" if hint else "")
                        + f" Se \"em {name}\" faz parte do que deve ser pesquisado, coloque a expressão entre aspas.")
    return tokens, None, None


_D = r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"
_DATE_PATTERNS = [
    ("range", re.compile(r"\b(?:de|entre|do dia|desde)\s+" + _D + r"\s+(?:a|ate|até|e)\s+(?:o dia\s+)?" + _D, re.I)),
    ("from", re.compile(r"\b(?:desde|a partir de|apos|após|depois de)\s+(?:o dia\s+)?" + _D, re.I)),
    ("to", re.compile(r"\b(?:ate|até|antes de)\s+(?:o dia\s+)?" + _D, re.I)),
    ("day", re.compile(r"\b(?:em|no dia|do dia|dia|de|data:?)\s+" + _D, re.I)),
    ("day", re.compile(r"(?<![\w/])" + _D + r"(?![\w/])")),
]


def _iso(d: str, m: str, y: str) -> str:
    from datetime import date
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        raise InterpretError(f"Data inválida: {d}/{m}/{y}. Use o formato DD/MM/AAAA.")


def _extract_dates(text: str):
    """Remove datas da mensagem (fora de aspas). Retorna (texto, data_inicial, data_final, trechos)."""
    if any(c in text for c in QUOTE_CHARS):
        parts = re.split("([" + re.escape("".join(QUOTE_CHARS)) + "][^" + re.escape("".join(QUOTE_CHARS)) + "]*["
                         + re.escape("".join(QUOTE_CHARS)) + "])", text)
    else:
        parts = [text]
    df = dt = None
    found: list[str] = []
    out = []
    for part in parts:
        if part[:1] in QUOTE_CHARS:
            out.append(part)
            continue
        for kind, rx in _DATE_PATTERNS:
            while True:
                m = rx.search(part)
                if not m:
                    break
                g = m.groups()
                if kind == "range":
                    df, dt = _iso(*g[:3]), _iso(*g[3:])
                elif kind == "from":
                    df = _iso(*g)
                elif kind == "to":
                    dt = _iso(*g)
                else:
                    df = dt = _iso(*g)
                found.append(m.group(0))
                part = part[:m.start()] + " " + part[m.end():]
        out.append(part)
    if df and dt and df > dt:
        raise InterpretError("A data inicial é posterior à data final.")
    return " ".join("".join(out).split()), df, dt, found


def interpret(message: str, fold_accents: bool = False) -> Interpretation:
    if message is None or not message.strip():
        raise InterpretError("Escreva o que devo pesquisar. Exemplo: procure \"defiro o pedido\" em Campinas")
    if len(message) > 2000:
        raise InterpretError("Mensagem muito longa (máximo de 2000 caracteres).")
    text = message.strip()
    strict = bool(PREFIX_RE.match(text))
    if strict:
        text = text[PREFIX_RE.match(text).end():]
    else:
        text = re.sub(r"[?!.]+$", "", text).strip()
    text, date_from, date_to, date_text = _extract_dates(text)
    tokens = _tokens(text)
    tokens, city, city_text = _extract_city(tokens, natural=not strict)
    ignored: list[str] = []
    if not strict:
        # palavras de comando e de preenchimento no início
        lead = []
        k = 0
        joined = lambda a, b: fold(" ".join(t for t, _ in tokens[a:b]))  # noqa: E731
        changed = True
        while changed and k < len(tokens) and not tokens[k][1]:
            changed = False
            for v in VERBS:
                nv = len(v.split())
                if joined(k, k + nv) == fold(v):
                    lead.append(" ".join(t for t, _ in tokens[k:k + nv]))
                    k += nv
                    changed = True
                    break
        while k < len(tokens) and not tokens[k][1] and fold(tokens[k][0].rstrip(":,")) in FILLER:
            lead.append(tokens[k][0])
            k += 1
        rest = tokens[k:]
        if not rest:          # a mensagem era só um termo comum (ex.: "sentença"): não remove nada
            rest, lead = tokens, []
        ignored += lead
        kept = []
        for t, q in rest:
            if not q and fold(t.strip(",;")) in CONNECTORS:
                ignored.append(t)
                continue
            t2 = t.strip(",;") if not q else t
            if t2:
                kept.append((t2, q))
        tokens = kept
    if city_text:
        ignored.append(city_text)
    ignored += date_text
    if not tokens:
        raise InterpretError("Não encontrei termos para pesquisar. Escreva o termo (ou a frase entre aspas).")
    parsed = parse(_join(tokens), fold_accents=fold_accents)
    parsed.command = message
    return Interpretation(mode="estrito" if strict else "conversa", parsed=parsed, city=city, ignored=ignored,
                          date_from=date_from, date_to=date_to)
