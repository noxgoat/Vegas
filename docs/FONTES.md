# Fontes: verificação, estado e como adicionar novas

Escopo do projeto: **Estado de São Paulo — TJSP e TRT-15**. Os conectores só acessam os domínios
de `JURIS_ALLOWED_DOMAINS`.

## Verificação feita em 12/10/2026

### TJSP — Consulta de Julgados de 1º Grau (`tjsp_cjpg`) — **validada**
- `GET https://esaj.tjsp.jus.br/cjpg/` abre a sessão (cookie).
- `GET /cjpg/pesquisar.do` com `dadosConsulta.pesquisaLivre`, `tipoNumero=UNIFICADO`,
  `dadosConsulta.dtInicio`/`dtFim` (dd/mm/aaaa), `dadosConsulta.ordenacao=DESC`.
- Paginação: `GET /cjpg/trocarDePagina.do?pagina=N&conversationId=` com `X-Requested-With`.
- Cada resultado é `tr.fundocinza1`; o **inteiro teor** está num `div` oculto. Metadados: classe,
  assunto, magistrado, comarca, foro, vara, data de disponibilização.
- Original: `obterArquivo.do?cdProcesso&cdForo&nmAlias&cdDocumento` (atributo `name` do link).
- **Filtro por comarca** (pesquisa por cidade): `POST pesquisar.do` com `varasTreeSelection.values` =
  códigos `foro-vara` de todas as varas da comarca, lidos de `GET /cjpg/varasTreeSelect.do`. Verificado:
  com as varas de Campinas, todos os resultados vêm da comarca de Campinas.
- **Instabilidade verificada:** com o filtro de varas, o portal às vezes responde "nenhum resultado"
  por engano (em geral na 1ª consulta da sessão; 4 de 8 sessões no teste). O conector repete até 3
  vezes; se a resposta vazia persistir, a coleta fica **parcial — ausência não confirmada**.
- Municípios que não são sede de comarca: a fonte é marcada como "não aplicável" na pesquisa por cidade.
- Sem CAPTCHA; `robots.txt` inexistente (404). "Não foi encontrado nenhum resultado" = zero
  resultados; qualquer outra resposta sem lista é tratada como **falha**, nunca como ausência.
- Busca do portal é por palavras: termos sem aspas são enviados como prefixo (`termo*`).

### TJSP — DJe (`tjsp_dje`) — **validada (acervo histórico)**
- `POST https://dje.tjsp.jus.br/cdje/consultaAvancada.do` (`dadosConsulta.pesquisaLivre`,
  `dtInicio`, `dtFim`, `cdCaderno=-11`), paginação `POST trocaDePagina.do pagina=N`.
- Resultado = página do diário; PDF em `getPaginaDoDiario.do?cdVolume&nuDiario&cdCaderno&nuSeqpagina`.
- A última edição disponível é de **22/07/2025** (datas posteriores retornam essa edição).
- **Falso negativo do portal:** para períodos longos (ex.: 2008–2025) o portal responde "nenhum
  registro". O conector divide o período em janelas de até 365 dias. Sem datas informadas, pesquisa
  os últimos 365 dias do acervo.

### DJEN — CNJ (`djen`) — **implementada, não validada**
- `GET https://comunicaapi.pje.jus.br/api/v1/comunicacao` com `siglaTribunal`,
  `dataDisponibilizacaoInicio/Fim`, `pagina`, `itensPorPagina` (5 ou 100), `numeroProcesso`,
  `numeroOab`, `ufOab`. Resposta `{status, message, count, items}`; certidão em
  `/api/v1/comunicacao/{hash}/certidao`. Parâmetros conforme documentação pública e projetos que
  usam a API; o parâmetro `texto` é enviado somente se configurado e **não foi confirmado**.
- A API responde **403 (CloudFront) fora do Brasil**; não foi possível validar a resposta real
  no ambiente de desenvolvimento. Os campos são lidos de forma tolerante e o item completo fica
  nos metadados. Restrição de escopo: só aceita `TJSP` e `TRT15`.
- Para validar: Fontes → **Testar conexão** (mostra os campos reais) e uma coleta de 1 dia.

### Não integradas
- **TJSP CJSG (2º grau)**: o formulário exige reCAPTCHA; sem o token, o portal devolve o formulário.
  CAPTCHA não é contornado.
- **TRT-15 DEJT** (`dejt.jt.jus.br`) e **jurisprudencia.jt.jus.br**: HTTP 403 (bloqueio geográfico)
  no ambiente de desenvolvimento; estrutura não verificada, logo nenhum conector foi escrito.
  Alternativas: DJEN-TRT15 e envio manual dos cadernos em PDF.

## Estados das fontes

| Estado | Significado |
|---|---|
| Ativa | Última coleta concluída (sucesso ou parcial por limite). |
| Implementada — não validada | Conector escrito, ainda sem coleta bem-sucedida. Passa a "Ativa" após a primeira. |
| Falha de acesso | A última coleta falhou por rede/bloqueio/HTTP. |
| Não configurada | Sem conector (estrutura não verificada) ou sem URLs. |
| Não integrável | Controle de acesso (ex.: CAPTCHA) impede a coleta automatizada. |
| Desativada | Desligada pelo administrador; não é pesquisada. |

## Adicionar uma fonte sem programar

**Fontes → Nova fonte**, conector `generic_web`, URL principal no domínio permitido e configuração:

```json
{"urls": ["https://www.tjsp.jus.br/..."], "seguir_links_regex": "\\.pdf$", "max_paginas": 20, "intervalo_seg": 2}
```

Para outro tribunal fora do escopo, inclua o domínio em `JURIS_ALLOWED_DOMAINS`.

## Escrever um novo conector

1. Verifique a estrutura real da fonte (requisições, parâmetros, paginação, robots.txt, CAPTCHA)
   e documente-a no topo do módulo. Não invente endpoints.
2. Crie `app/connectors/<nome>.py` com uma classe derivada de `Connector`:
   - `collect(params)`: use `self.ctx.http` (cliente seguro), `self.ctx.page_examined()`,
     `self.ctx.save(external_id, Extraction, DocMeta, blob)`, `self.ctx.fail_document()`,
     `self.ctx.checkpoint({...})` / `get_checkpoint()` e `self.ctx.set_total(n)`.
   - Lance `AccessError` para falhas de acesso e `Truncated` quando parar por limite.
   - Nunca trate uma resposta inesperada como "zero resultados".
   - `test_connection()`: uma requisição leve que mostre a estrutura recebida.
   - `supports_query_discovery = True` se o portal pesquisa por termo (usado em "Consultar os
     portais antes"); `portal_query(term)` traduz o termo para a sintaxe do portal.
3. Registre em `app/connectors/registry.py`.
4. Preencha só metadados informados pela fonte (`DocMeta`); deixe vazio o que não existir.
5. Escreva testes com HTML/JSON sintético reproduzindo a estrutura e um teste em
   `tests/test_conectores_online.py`.
