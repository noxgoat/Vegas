-- Esquema inicial do JurisBusca SP.

CREATE TABLE sources (
    id                  INTEGER PRIMARY KEY,
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    court               TEXT,                      -- TJSP, TRT15
    base_url            TEXT NOT NULL,
    category            TEXT NOT NULL,             -- diario, jurisprudencia, comunicacoes, arquivos
    connector           TEXT NOT NULL,             -- tipo do conector (app/connectors)
    access_method       TEXT,
    search_mechanism    TEXT,
    pagination          TEXT,
    document_method     TEXT,
    formats             TEXT,
    update_policy       TEXT,
    schedule_minutes    INTEGER NOT NULL DEFAULT 0, -- 0 = sem coleta agendada
    retry_minutes       INTEGER NOT NULL DEFAULT 60,
    max_retries         INTEGER NOT NULL DEFAULT 3,
    config_json         TEXT NOT NULL DEFAULT '{}',
    enabled             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'nao_configurada',
                        -- nao_configurada | ativa | falha_acesso | desativada | nao_integravel | nao_validada
    status_message      TEXT,
    last_success_at     TEXT,
    last_attempt_at     TEXT,
    last_error          TEXT,
    known_restrictions  TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE documents (
    id                  INTEGER PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id         TEXT NOT NULL,
    title               TEXT,
    url_publication     TEXT,
    url_original        TEXT,
    court               TEXT,
    organ               TEXT,                       -- órgão julgador / vara
    act_type            TEXT,                       -- tipo de ato informado PELA FONTE
    process_number      TEXT,
    judge               TEXT,
    date_available      TEXT,                       -- data de disponibilização
    date_publication    TEXT,                       -- data de publicação
    date_decision       TEXT,                       -- data da decisão/julgamento
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    text                TEXT NOT NULL,              -- texto extraído (NFC)
    pages_json          TEXT,                       -- [[pagina, inicio, fim], ...]
    content_hash        TEXT NOT NULL,
    blob_sha256         TEXT,                       -- arquivo original armazenado sem alteração
    extraction_method   TEXT NOT NULL,              -- html | pdf_texto | ocr | pdf_misto | api_texto | texto
    extraction_status   TEXT NOT NULL,              -- completo | parcial | falha
    extraction_notes    TEXT,
    text_scope          TEXT NOT NULL DEFAULT 'integral', -- integral | parcial
    ocr_confidence      REAL,
    version             INTEGER NOT NULL DEFAULT 1,
    dup_of              INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    first_seen_at       TEXT NOT NULL,
    last_collected_at   TEXT NOT NULL,
    collection_run_id   INTEGER,
    UNIQUE (source_id, external_id)
);
CREATE INDEX idx_documents_hash ON documents(content_hash);
CREATE INDEX idx_documents_process ON documents(process_number);
CREATE INDEX idx_documents_date_pub ON documents(date_publication);
CREATE INDEX idx_documents_date_av ON documents(date_available);
CREATE INDEX idx_documents_source ON documents(source_id);

-- Índice de trigramas (FTS5) sobre o texto NORMALIZADO; usado apenas como
-- pré-filtro de candidatos. A confirmação é sempre feita no texto armazenado.
CREATE VIRTUAL TABLE doc_fts USING fts5(norm, folded, tokenize = 'trigram');

CREATE TABLE document_versions (
    id              INTEGER PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    text            TEXT NOT NULL,
    metadata_json   TEXT,
    collected_at    TEXT NOT NULL,
    diff_summary    TEXT,
    UNIQUE (document_id, version)
);

CREATE TABLE search_runs (
    id              INTEGER PRIMARY KEY,
    command         TEXT NOT NULL,
    terms_json      TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    status          TEXT NOT NULL,                  -- em_andamento | concluida | erro
    progress_json   TEXT,
    stats_json      TEXT,
    coverage_json   TEXT,
    warnings_json   TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE search_results (
    id              INTEGER PRIMARY KEY,
    search_run_id   INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    term_index      INTEGER NOT NULL,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version INTEGER NOT NULL,
    occurrence_count INTEGER NOT NULL,
    first_position  INTEGER NOT NULL,
    UNIQUE (search_run_id, term_index, document_id)
);
CREATE INDEX idx_results_run_term ON search_results(search_run_id, term_index);

CREATE TABLE occurrences (
    id              INTEGER PRIMARY KEY,
    search_run_id   INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    term_index      INTEGER NOT NULL,
    term            TEXT NOT NULL,
    match_mode      TEXT NOT NULL,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version INTEGER NOT NULL,
    ordinal         INTEGER NOT NULL,               -- 1..n dentro do documento
    start_pos       INTEGER NOT NULL,               -- posição no texto armazenado
    end_pos         INTEGER NOT NULL,
    page            INTEGER,
    snippet_before  TEXT NOT NULL,
    match_text      TEXT NOT NULL,
    snippet_after   TEXT NOT NULL,
    verified        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_occ_run_doc ON occurrences(search_run_id, term_index, document_id);

CREATE TABLE collection_runs (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    trigger         TEXT NOT NULL,                  -- manual | agendada | pesquisa | retomada
    params_json     TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL,                  -- em_andamento | sucesso | parcial | falha | cancelada
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    pages_examined  INTEGER NOT NULL DEFAULT 0,
    docs_found      INTEGER NOT NULL DEFAULT 0,
    docs_new        INTEGER NOT NULL DEFAULT 0,
    docs_updated    INTEGER NOT NULL DEFAULT 0,
    docs_unchanged  INTEGER NOT NULL DEFAULT 0,
    docs_failed     INTEGER NOT NULL DEFAULT 0,
    docs_partial    INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT,
    total_reported  INTEGER,                        -- total informado pela fonte, se houver
    error           TEXT,
    message         TEXT,
    search_run_id   INTEGER
);
CREATE INDEX idx_coll_source ON collection_runs(source_id, started_at);

CREATE TABLE collection_logs (
    id              INTEGER PRIMARY KEY,
    collection_run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    at              TEXT NOT NULL,
    level           TEXT NOT NULL,                  -- info | aviso | erro
    message         TEXT NOT NULL,
    url             TEXT
);
CREATE INDEX idx_coll_logs_run ON collection_logs(collection_run_id);

CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    detail          TEXT
);
