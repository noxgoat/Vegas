from __future__ import annotations

from .djen import Djen
from .generic_web import GenericWeb
from .tjsp_cjpg import TjspCjpg
from .tjsp_dje import TjspDje

CONNECTORS = {
    "tjsp_cjpg": TjspCjpg,
    "tjsp_dje": TjspDje,
    "djen": Djen,
    "generic_web": GenericWeb,
}

# Tipos cadastráveis sem coleta automática (sem conector de rede).
PASSIVE = {"upload": "Arquivos enviados manualmente", "nenhum": "Sem conector (não configurada/não integrável)"}
