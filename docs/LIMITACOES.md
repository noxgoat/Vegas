# Limitações conhecidas

1. **A pesquisa cobre apenas o acervo coletado.** Nenhuma fonte é pesquisada "inteira" de uma
   vez: o TJSP 1º grau tem centenas de milhares de sentenças por expressão comum. O relatório de
   cobertura informa, por fonte, quantos documentos existem no acervo, quantas páginas foram
   examinadas e se a última coleta falhou.
2. **DJEN não validado.** A API do CNJ recusou acessos do ambiente de desenvolvimento (fora do
   Brasil). O conector segue a documentação pública, mas precisa ser validado no Brasil
   (Fontes → Testar conexão). Até lá, despachos e decisões **atuais** do TJSP e do TRT-15 não
   estão cobertos automaticamente.
3. **DJe do TJSP é histórico**: termina em 22/07/2025.
4. **TRT-15 (DEJT e jurisprudência)**: sem conector; estrutura não verificada por bloqueio
   geográfico. Use o DJEN-TRT15 ou envie os PDFs manualmente.
5. **TJSP 2º grau (CJSG)**: protegido por reCAPTCHA; não integrado.
6. **Descoberta por termo nos portais é por palavras.** Substrings no meio de palavras
   (ex.: `defer` em "indeferimento") são enviadas como prefixo (`defer*`) ao TJSP 1º grau; documentos
   que só contenham a substring no meio de uma palavra serão encontrados apenas se já estiverem no
   acervo (por outra coleta). O DJe não confirmou suporte a `*`.
7. **Período longo no DJe**: o portal retorna "nenhum registro" para períodos extensos; o conector
   divide em janelas de 365 dias. Sem datas, pesquisa só os últimos 365 dias do acervo histórico.
8. **Páginas do DJe contêm várias publicações.** O documento é a página do diário; a data é a da
   edição (disponibilização). O tipo de ato não é inferido.
9. **OCR**: depende do Tesseract (`por`). Páginas com confiança média abaixo de
   `JURIS_OCR_MIN_CONF` ou sem texto reconhecido deixam o documento como **Parcial**.
10. **Metadados**: somente os informados pela fonte. Datas de publicação não são deduzidas da
    disponibilização. Campos ausentes aparecem como "Não localizado na fonte consultada".
11. **TJSP 1º grau**: o texto vem do inteiro teor exibido na própria página de resultados; o PDF
    (visualizador do e-SAJ) não é baixado. Se o inteiro teor oculto não existir, só o trecho
    visível é guardado e o documento fica **Parcial**.
12. **Ocorrências não sobrepostas**: `aa` em `aaa` conta 1 ocorrência.
13. **Monitoramento é periódico**, não em tempo real; frequência mínima prática é limitada pelo
    respeito às fontes (intervalo entre requisições configurável por fonte).
14. **Resumo por IA** é opcional, exige `ANTHROPIC_API_KEY` e envia o texto do documento à API da
    Anthropic. Não substitui a leitura do original.
15. **Pesquisa por cidade**: depende de a fonte informar a cidade. TJSP 1º grau usa a comarca (um
    município sem comarca própria não tem resultados por cidade nessa fonte); DJEN usa o nome do órgão
    ("Vara ... de <cidade>"); o DJe histórico e páginas por URL não informam a cidade e ficam de fora.
16. A "IA" da conversa interpreta a pergunta por regras fixas (não por um modelo de linguagem), para
    que a interpretação seja previsível e sempre exibida. O resumo opcional por IA continua separado.
17. **Escopo despacho/decisão**: depende do tipo declarado pela fonte. Com o padrão
    (`JURIS_SOMENTE_DESPACHO_DECISAO=1`), praticamente só o DJEN fornece resultados — e o DJEN só responde
    a acessos do Brasil. Fora do Brasil (ou se o DJEN falhar), a pesquisa não terá resultados e a resposta
    informa a falha. Os valores reais de `tipoDocumento` do DJEN não puderam ser conferidos daqui;
    valores não reconhecidos são tratados como "outro ato" e ficam fora (confira em Fontes → Testar conexão).
    Decisões monocráticas de desembargador, quando declaradas como "Decisão", entram no escopo.
18. Uso pessoal: SQLite atende bem a dezenas/centenas de milhares de documentos. Para volumes muito
    maiores ou vários usuários, ver docs/PRODUCAO.md.
