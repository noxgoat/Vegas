"""Fontes pré-cadastradas (escopo: Estado de São Paulo — TJSP e TRT-15).

Cada estado reflete a verificação real feita durante o desenvolvimento
(ver docs/FONTES.md). O cadastro só insere fontes ausentes; edições feitas
pelo administrador nunca são sobrescritas.
"""
from __future__ import annotations

import json
import sqlite3

SEED = [
    dict(
        slug="tjsp-cjpg", name="TJSP — Consulta de Julgados de 1º Grau (sentenças)", court="TJSP",
        base_url="https://esaj.tjsp.jus.br/cjpg/", category="jurisprudencia", connector="tjsp_cjpg",
        access_method="Formulário público e-SAJ (HTTP GET pesquisar.do; sem login; sem CAPTCHA na verificação)",
        search_mechanism="Pesquisa livre do portal (por palavras; aspas = frase; * = prefixo)",
        pagination="trocarDePagina.do?pagina=N (10 resultados por página, sessão por cookie)",
        document_method="Inteiro teor exibido na própria página de resultados; link obterArquivo.do para o PDF",
        formats="HTML (inteiro teor); PDF no visualizador do e-SAJ",
        update_policy="Descoberta por termo (na pesquisa) ou coleta agendada com termos fixos",
        schedule_minutes=0, status="desativada",
        status_message='Desativada: contém apenas sentenças, fora do escopo "somente despachos e decisões".',
        config={"max_paginas": 20, "intervalo_seg": 2.0, "termos_agendados": []},
        known_restrictions=("A busca do portal é por palavras: termos sem aspas são enviados como prefixo (termo*). "
                            "Ocorrências no meio de palavras (ex.: 'indeferimento' para 'defer') só são "
                            "encontradas em documentos já presentes no acervo. Cobre sentenças da base de julgados, "
                            "não todos os despachos."),
    ),
    dict(
        slug="tjsp-dje", name="TJSP — Diário da Justiça Eletrônico (acervo histórico)", court="TJSP",
        base_url="https://dje.tjsp.jus.br/cdje/index.do", category="diario", connector="tjsp_dje",
        access_method="Consulta avançada pública (POST consultaAvancada.do)",
        search_mechanism="Pesquisa livre do portal por cadernos e período",
        pagination="Campo 'pagina' do formulário (10 resultados por página)",
        document_method="PDF de cada página do diário via getPaginaDoDiario.do",
        formats="PDF",
        update_policy="Acervo encerrado: a última edição disponível é de 22/07/2025",
        schedule_minutes=0, status="desativada",
        status_message=('Desativada: páginas do diário misturam vários atos sem tipo declarado pela fonte; '
                        'fora do escopo "somente despachos e decisões".'),
        config={"max_paginas": 10, "intervalo_seg": 2.0, "data_inicio": "", "data_fim": "", "caderno": "-11"},
        known_restrictions=("O DJe próprio do TJSP não recebe novas edições desde 22/07/2025 (publicações migraram "
                            "para o DJEN/CNJ). Use a fonte DJEN — TJSP para publicações atuais."),
    ),
    dict(
        slug="djen-tjsp", name="DJEN (CNJ) — Comunicações do TJSP", court="TJSP",
        base_url="https://comunicaapi.pje.jus.br/api/v1/comunicacao", category="comunicacoes", connector="djen",
        access_method="API pública REST do CNJ (GET, JSON)",
        search_mechanism="Filtros siglaTribunal, período de disponibilização, número do processo, OAB",
        pagination="pagina / itensPorPagina (5 ou 100)",
        document_method="Texto integral da comunicação no campo 'texto' da resposta",
        formats="JSON (texto em HTML)",
        update_policy="Coleta diária por período de disponibilização",
        schedule_minutes=0, status="nao_validada",
        config={"sigla_tribunal": "TJSP", "dias_retroativos": 1, "max_paginas": 50, "intervalo_seg": 3.5,
                "numero_processo": "", "numero_oab": "", "uf_oab": "", "texto": ""},
        known_restrictions=("A API bloqueia acessos de fora do Brasil (CloudFront). Implementada conforme "
                            "documentação pública; NÃO validada no ambiente de desenvolvimento. Limite de "
                            "requisições informado pela própria API (cabeçalhos X-RateLimit-*). Use "
                            "'Testar conexão' a partir de uma rede no Brasil."),
    ),
    dict(
        slug="djen-trt15", name="DJEN (CNJ) — Comunicações do TRT-15", court="TRT15",
        base_url="https://comunicaapi.pje.jus.br/api/v1/comunicacao", category="comunicacoes", connector="djen",
        access_method="API pública REST do CNJ (GET, JSON)",
        search_mechanism="Filtros siglaTribunal, período de disponibilização, número do processo, OAB",
        pagination="pagina / itensPorPagina (5 ou 100)",
        document_method="Texto integral da comunicação no campo 'texto' da resposta",
        formats="JSON (texto em HTML)",
        update_policy="Coleta diária por período de disponibilização",
        schedule_minutes=0, status="nao_validada",
        config={"sigla_tribunal": "TRT15", "dias_retroativos": 1, "max_paginas": 50, "intervalo_seg": 3.5,
                "numero_processo": "", "numero_oab": "", "uf_oab": "", "texto": ""},
        known_restrictions=("Mesmas restrições da API DJEN (bloqueio geográfico fora do Brasil; não validada "
                            "no ambiente de desenvolvimento)."),
    ),
    dict(
        slug="tjsp-cjsg", name="TJSP — Jurisprudência de 2º Grau (CJSG)", court="TJSP",
        base_url="https://esaj.tjsp.jus.br/cjsg/consultaCompleta.do", category="jurisprudencia", connector="nenhum",
        access_method="Formulário protegido por reCAPTCHA", search_mechanism="—", pagination="—",
        document_method="—", formats="HTML/PDF", update_policy="—",
        schedule_minutes=0, status="nao_integravel",
        config={},
        known_restrictions=("O formulário exige reCAPTCHA; a pesquisa sem o token retorna ao formulário. "
                            "Por regra do projeto, CAPTCHA não é contornado. Acórdãos podem ser enviados "
                            "manualmente em 'Acervo > Enviar arquivos'."),
    ),
    dict(
        slug="trt15-dejt", name="TRT-15 — Diário Eletrônico da Justiça do Trabalho (DEJT)", court="TRT15",
        base_url="https://dejt.jt.jus.br/dejt", category="diario", connector="nenhum",
        access_method="Não verificado", search_mechanism="Não verificado", pagination="Não verificado",
        document_method="Não verificado", formats="PDF", update_policy="—",
        schedule_minutes=0, status="nao_configurada",
        config={},
        known_restrictions=("O portal retornou HTTP 403 (bloqueio geográfico) a partir do ambiente de "
                            "desenvolvimento; sua estrutura não pôde ser verificada e, por isso, nenhum "
                            "conector foi inventado. Cadernos em PDF baixados manualmente podem ser enviados "
                            "em 'Acervo > Enviar arquivos'. Comunicações do TRT-15 estão cobertas pela fonte DJEN."),
    ),
    dict(
        slug="trt15-jurisprudencia", name="TRT-15 — Jurisprudência (jurisprudencia.jt.jus.br)", court="TRT15",
        base_url="https://jurisprudencia.jt.jus.br/", category="jurisprudencia", connector="nenhum",
        access_method="Não verificado", search_mechanism="Não verificado", pagination="Não verificado",
        document_method="Não verificado", formats="—", update_policy="—",
        schedule_minutes=0, status="nao_configurada",
        config={},
        known_restrictions="HTTP 403 (bloqueio geográfico) no ambiente de desenvolvimento; estrutura não verificada.",
    ),
    dict(
        slug="arquivos-enviados", name="Arquivos enviados por mim (PDF, HTML, TXT)", court=None,
        base_url="local://uploads", category="arquivos", connector="upload",
        access_method="Envio manual pela interface", search_mechanism="Busca local", pagination="—",
        document_method="Arquivo enviado", formats="PDF (com OCR), HTML, TXT",
        update_policy="Manual", schedule_minutes=0, status="ativa", config={},
        known_restrictions="Somente o conteúdo efetivamente enviado é pesquisado.",
    ),
    dict(
        slug="paginas-sp", name="Páginas e PDFs por URL (domínios do TJSP/TRT-15)", court=None,
        base_url="https://www.tjsp.jus.br/", category="arquivos", connector="generic_web",
        access_method="HTTP GET das URLs cadastradas (respeita robots.txt)",
        search_mechanism="Nenhum (coleta das URLs informadas)",
        pagination="Opcional: segue links que casem com um padrão, até um limite",
        document_method="HTML e PDF (com OCR quando necessário)", formats="HTML, PDF",
        update_policy="Manual ou agendada", schedule_minutes=0, status="nao_configurada",
        config={"urls": [], "seguir_links_regex": "", "max_paginas": 20, "intervalo_seg": 2.0},
        known_restrictions="Restrito aos domínios permitidos (JURIS_ALLOWED_DOMAINS).",
    ),
]


def seed_sources(conn: sqlite3.Connection) -> None:
    existing = {r[0] for r in conn.execute("SELECT slug FROM sources")}
    for s in SEED:
        if s["slug"] in existing:
            continue
        cols = ["slug", "name", "court", "base_url", "category", "connector", "access_method",
                "search_mechanism", "pagination", "document_method", "formats", "update_policy",
                "schedule_minutes", "status", "known_restrictions", "status_message"]
        vals = [s.get(c) for c in cols]
        cols.append("config_json")
        vals.append(json.dumps(s.get("config", {}), ensure_ascii=False))
        enabled = 1 if s["status"] in ("ativa", "nao_validada") else 0
        cols.append("enabled")
        vals.append(enabled)
        conn.execute(f"INSERT INTO sources ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
