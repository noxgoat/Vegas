"""Interface comum dos conectores de fontes."""
from __future__ import annotations

import re
from datetime import datetime

from ..query_parser import EXACT, Term


class NotApplicable(Exception):
    """A coleta não se aplica aos parâmetros (ex.: município que não é sede de comarca)."""


class Truncated(Exception):
    """Sinaliza que a coleta parou por limite configurado (não por fim dos resultados)."""


class Connector:
    #: o conector consegue descobrir documentos a partir de um termo (usa a busca do portal)
    supports_query_discovery = False
    #: o conector consegue atualizar o acervo por período (coleta incremental)
    supports_incremental = False

    def __init__(self, source: dict, config: dict, ctx):
        self.source = source
        self.config = config
        self.ctx = ctx

    def collect(self, params: dict) -> None:
        raise NotImplementedError

    def test_connection(self) -> dict:
        raise NotImplementedError

    def portal_query(self, term: Term) -> str:
        """Tradução do termo para a sintaxe do portal (apenas para DESCOBRIR candidatos).

        A confirmação da correspondência é sempre feita localmente pelo motor de busca.
        """
        if term.mode == EXACT:
            return f'"{term.raw.strip()}"'
        return term.raw.strip()


def br_date_to_iso(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    return None


def iso_to_br(s: str | None) -> str:
    if not s:
        return ""
    return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
