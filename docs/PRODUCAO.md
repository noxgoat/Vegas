# Implantação em produção

O sistema foi pensado para uso pessoal (um computador, um usuário). Para mantê-lo ligado em um
servidor:

## 1. Servidor
- Linux com Python 3.11+, `tesseract-ocr` e `tesseract-ocr-por`.
- **Rede no Brasil** (a API do DJEN e portais da Justiça do Trabalho bloqueiam acessos do exterior).
- Usuário dedicado sem privilégios; pasta de dados com backup.

```bash
sudo useradd -r -m -d /opt/jurisbusca jurisbusca
sudo -u jurisbusca git clone <repositório> /opt/jurisbusca/app   # ou copie a pasta
cd /opt/jurisbusca/app && sudo -u jurisbusca python3 -m venv .venv
sudo -u jurisbusca .venv/bin/pip install -r requirements.txt
sudo -u jurisbusca cp config.example.env .env   # defina JURIS_APP_TOKEN e JURIS_DATA_DIR
```

## 2. Serviço (systemd)

`/etc/systemd/system/jurisbusca.service`:

```ini
[Unit]
Description=JurisBusca SP
After=network-online.target

[Service]
User=jurisbusca
WorkingDirectory=/opt/jurisbusca/app
EnvironmentFile=/opt/jurisbusca/app/.env
ExecStart=/opt/jurisbusca/app/.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/jurisbusca/app/data

[Install]
WantedBy=multi-user.target
```

Use **um único worker**: o agendador e o controle de coletas rodam dentro do processo.

## 3. HTTPS e acesso
- Coloque um proxy reverso (Caddy ou Nginx) com HTTPS na frente do `127.0.0.1:8000`.
- **Defina `JURIS_APP_TOKEN`** (longo e aleatório). Sem ele, qualquer pessoa com acesso à porta usa o sistema.
- Opcional: restrinja por IP/VPN no proxy.

## 4. Dados, backup e retenção
- Banco: `data/juris.db` (SQLite em modo WAL). Originais: `data/originais/` (endereçados por SHA-256).
- Backup consistente: `sqlite3 data/juris.db ".backup backup.db"` + cópia de `data/originais/`.
- Retenção: `JURIS_RETENCAO_PESQUISAS_DIAS` e `JURIS_RETENCAO_LOGS_DIAS` (aplicadas ao iniciar).
- Migrações: arquivos em `app/migrations/` aplicados automaticamente na inicialização
  (tabela `schema_migrations`).

## 5. Coletas agendadas
Em **Fontes → Editar**, defina "Coleta agendada (min)". Exemplos:
- DJEN TJSP / TRT-15: `1440` (diário), com `dias_retroativos: 2` na configuração.
- TJSP 1º grau: `720`, com `termos_agendados` na configuração, por exemplo
  `["\"expedição de alvará\"", "\"levantamento de valores\""]` (um termo por ciclo, em rodízio).
- "Nova tentativa após falha (min)" controla a repetição quando a coleta falha.

## 6. Escala
Para volumes muito grandes (milhões de documentos) ou múltiplos usuários, os pontos de troca são
`app/db.py` (conexão), o índice de trigramas (`doc_fts` → PostgreSQL `pg_trgm` com índice GIN em
uma coluna normalizada) e a fila de coletas (`collector.executor` → fila externa, ex.: RQ/Celery).
A confirmação de correspondências (`search.find_matches`) não muda.
