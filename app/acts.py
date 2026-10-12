"""Classificação do tipo de ato a partir do que a FONTE declara (nunca do texto do documento).

Escopo do sistema (JURIS_SOMENTE_DESPACHO_DECISAO=1, padrão): somente despachos e decisões
(interlocutórias e monocráticas) proferidos por magistrado. Sentenças, acórdãos, intimações
de cartório, editais, certidões, atos ordinatórios e documentos sem tipo declarado ficam fora.
"""
from __future__ import annotations

from .cities import fold

IN_SCOPE = ("despacho", "decisao")
LABELS = {"despacho": "Despacho", "decisao": "Decisão", "sentenca": "Sentença", "acordao": "Acórdão",
          "outro": "Outro tipo de ato"}


def classify_act(declared: str | None) -> str | None:
    """'despacho' | 'decisao' | 'sentenca' | 'acordao' | 'outro' | None (tipo não informado)."""
    t = fold(declared or "")
    if not t:
        return None
    if t.startswith("despacho"):
        return "despacho"
    if t.startswith("decisao"):            # decisão, decisão interlocutória, decisão monocrática
        return "decisao"
    if t.startswith("sentenca"):
        return "sentenca"
    if t.startswith(("acordao", "ementa")):
        return "acordao"
    return "outro"
