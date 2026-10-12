# JurisBusca SP

Plataforma pessoal de pesquisa textual em decisões, despachos, sentenças e publicações do
**Estado de São Paulo — TJSP e TRT-15**. Coleta documentos das fontes oficiais, extrai o texto
integral (HTML, PDF e PDF digitalizado com OCR em português), indexa e pesquisa com um motor
**determinístico**: toda correspondência exibida foi confirmada no texto armazenado e vem com o
trecho original, a posição exata e o link da fonte.

## Escopo: somente despachos e decisões

Por padrão, o sistema mostra **apenas despachos e decisões de magistrado** (interlocutórias e
monocráticas). Ficam de fora sentenças, acórdãos, intimações de cartório, editais, certidões, atos
ordinatórios e documentos sem tipo informado. O tipo vem do que a **fonte declara** (ex.: campo
`tipoDocumento` do DJEN); o sistema nunca deduz o tipo a partir das palavras do texto.

Consequência: a fonte que traz despachos e decisões com tipo declarado é o **DJEN (TJSP e TRT-15)**. As
fontes "TJSP — Julgados de 1º grau" (só sentenças) e "DJe histórico" (páginas sem tipo por ato) ficam
desativadas. Para voltar a incluir tudo, defina `JURIS_SOMENTE_DESPACHO_DECISAO=0` e reative essas
fontes em **Fontes**. Arquivos enviados manualmente entram se você marcar o tipo como Despacho ou Decisão.

## Como pesquisar (conversa)

A tela inicial funciona como uma conversa: escreva a pergunta e o sistema responde com o que
encontrou. Exemplos:

```
procure "defiro o pedido" em Campinas
busque decisões com "tutela de urgência" e alvará na comarca de Santos
"levantamento de valores" indeferimento cidade: São José dos Campos
```

- Por padrão, cada pergunta **consulta automaticamente todas as fontes ativas do Estado de SP** antes
  de responder (dá para desligar em "Opções da pesquisa").
- **Cidade**: termine com "em <cidade>", "na comarca de <cidade>" ou "cidade: <cidade>". O município é
  conferido na lista oficial do IBGE (645 municípios de SP); se não existir, o sistema avisa e sugere.
  No TJSP 1º grau a busca é feita só nas varas daquela comarca; no DJEN, pelas varas com o nome da
  cidade. O DJe histórico não informa a cidade e fica de fora da pesquisa por cidade.
- Palavras de comando ("procure", "busque decisões com", "e") são ignoradas e **listadas na resposta**,
  junto com os termos e a cidade entendidos. Não há modelo de IA decidindo correspondências: a
  interpretação é por regras fixas e a confirmação é sempre no texto integral.

O comando original continua valendo (o prefixo `busque:` ativa o modo estrito, sem descartar palavras):

```
busque: "defiro o pedido" deferimento "expedição de alvará" despacho
```

| Escrita | Modo | O que encontra |
|---|---|---|
| `"defiro o pedido"` (aspas retas ou curvas) | **Frase exata** | As palavras consecutivas, na mesma ordem, como palavras inteiras. Não encontra "defiro integralmente o pedido", "defiro, o pedido" nem "indefiro o pedido". |
| `defer` (sem aspas) | **Correspondência parcial (substring)** | A sequência em qualquer posição: defer**ido**, **in**deferimento, etc. |

- Cada termo é uma **pesquisa independente**, com seu próprio grupo de resultados (não há E/OU implícito).
- Maiúsculas/minúsculas são ignoradas; **acentos são preservados** ("Ignorar acentos" é um modo adicional opcional).
- Sem sinônimos, sem radicalização, sem expansão de consulta.
- Aspas não fechadas geram erro — a consulta nunca é alterada em silêncio.
- Regras completas de normalização: [docs/NORMALIZACAO.md](docs/NORMALIZACAO.md).

**"Consultar os portais antes"**: antes da busca local, o sistema usa a pesquisa dos portais que a
oferecem (TJSP 1º grau e DJe histórico) para trazer novos documentos ao acervo, e atualiza o DJEN
pelo período. A confirmação continua sendo feita localmente. O relatório de cobertura mostra, para
cada fonte, o que foi consultado, quantas páginas foram examinadas e quais falhas ocorreram.

## Fontes (situação verificada em 12/10/2026)

| Fonte | Situação | Observação |
|---|---|---|
| TJSP — Consulta de Julgados de 1º Grau (sentenças) | **Ativa, validada** | Inteiro teor das sentenças |
| TJSP — DJe (acervo histórico) | **Ativa, validada** | PDFs das páginas do diário; última edição: 22/07/2025 |
| DJEN (CNJ) — TJSP | Implementada, **não validada** | API bloqueia acessos de fora do Brasil |
| DJEN (CNJ) — TRT-15 | Implementada, **não validada** | Idem |
| TJSP — Jurisprudência 2º Grau (CJSG) | Não integrável | Exige reCAPTCHA (não é contornado) |
| TRT-15 — DEJT | Não configurada | Bloqueio geográfico impediu verificar a estrutura |
| TRT-15 — Jurisprudência | Não configurada | Idem |
| Arquivos enviados por mim | Ativa | PDF/HTML/TXT enviados pela interface |
| Páginas e PDFs por URL | Não configurada | Cadastre URLs dos domínios do TJSP/TRT-15 |

Detalhes e instruções para novas fontes: [docs/FONTES.md](docs/FONTES.md).
Limitações conhecidas: [docs/LIMITACOES.md](docs/LIMITACOES.md).

> **Importante para o DJEN (despachos e decisões atuais do TJSP e do TRT-15):** rode o sistema em
> uma rede no Brasil e clique em **Fontes → Testar conexão** nas fontes DJEN. A resposta mostra os
> campos reais devolvidos pela API. Depois da primeira coleta bem-sucedida, a fonte passa a "Ativa".

## Instalação (Windows, macOS ou Linux)

Requisitos: Python 3.11+ e, para OCR, o Tesseract com o idioma português.

```bash
# 1. dependências Python
python3 -m pip install -r requirements.txt

# 2. OCR (opcional, mas recomendado)
#   Ubuntu/Debian: sudo apt install tesseract-ocr tesseract-ocr-por
#   macOS:         brew install tesseract tesseract-lang
#   Windows:       instale o Tesseract (UB Mannheim) marcando "Portuguese" e adicione-o ao PATH

# 3. configuração (opcional)
cp config.example.env .env

# 4. executar
./run.sh                 # ou: python3 -m uvicorn app.api:app --port 8000
```

Abra **http://127.0.0.1:8000**. O banco (SQLite) e os arquivos originais ficam em `data/` e
persistem entre reinicializações.

### Primeiros passos

1. **Pesquisa** → digite `busque: "defiro o pedido" defer`, marque **Consultar os portais antes**,
   escolha quantas páginas por fonte e clique em **Pesquisar**.
2. Navegue pelos grupos de cada termo, abra o **Texto integral com destaques** (setas para ir de
   uma ocorrência à outra) e use os links da publicação/original.
3. **Exportar** CSV ou XLSX (todos os resultados ou apenas a seleção atual).
4. **Fontes → Coletar** para ampliar o acervo (por termo, período, processo ou OAB). Coletas
   interrompidas podem ser **retomadas** do checkpoint em **Coletas**.
5. **Acervo → Enviar arquivos** para incluir PDFs do DEJT ou acórdãos baixados manualmente.

## Testes

```bash
python3 -m pytest                       # 46 testes (motor, OCR, conectores com HTML sintético, API)
JURIS_TESTES_ONLINE=1 python3 -m pytest tests/test_conectores_online.py   # validação contra os portais reais
```

## Arquitetura

```
app/
  normalize.py      normalização documentada + mapa de posições para o texto original
  query_parser.py   interpretador do comando (aspas = frase exata; sem aspas = substring)
  search.py         motor: pré-filtro por trigramas (SQLite FTS5) + confirmação no texto armazenado
  extract.py        HTML, PDF digital e OCR (Tesseract por)
  ingest.py         gravação, deduplicação por hash, versões e índice
  http.py           cliente HTTP seguro (domínios permitidos, anti-SSRF, robots.txt, limites, novas tentativas)
  collector.py      coletas, checkpoints, logs, estados das fontes e agendador
  jobs.py           pesquisa com consulta online opcional
  connectors/       tjsp_cjpg, tjsp_dje, djen, generic_web
  export.py         CSV/XLSX com proteção contra fórmulas
  summarize.py      resumo opcional por IA (somente quando solicitado)
  api.py            API REST (FastAPI) + interface web
  static/           interface (HTML/CSS/JS sem dependências externas)
  migrations/       migrações SQL
```

Coleta, pesquisa e IA são separadas: a IA generativa **não** participa da localização nem da
confirmação de correspondências; ela só produz resumos quando você pede, sempre rotulados.

Hospedagem gratuita (Oracle Cloud, região São Paulo): [docs/HOSPEDAGEM_GRATIS.md](docs/HOSPEDAGEM_GRATIS.md).
Produção (detalhes técnicos): [docs/PRODUCAO.md](docs/PRODUCAO.md).
