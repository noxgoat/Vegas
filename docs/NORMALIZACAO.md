# Política de normalização

A mesma função (`app/normalize.py`) é aplicada ao texto armazenado e aos termos da consulta.
Ela nunca remove nem reordena palavras.

| Item | Regra |
|---|---|
| Unicode | Texto extraído é armazenado em NFC (equivalência canônica; não altera o conteúdo). |
| Maiúsculas/minúsculas | Comparação com `str.lower()`. A grafia original é preservada para exibição. |
| Espaços e quebras de linha | Qualquer sequência de espaço, tabulação, espaço inseparável, quebra de linha ou de página vira **um** espaço. |
| Hifenização de fim de linha | `defe-⏎rimento` → `deferimento`, apenas quando o hífen segue uma letra, vem antes de uma quebra de linha e a linha seguinte começa com letra **minúscula**. |
| Caracteres invisíveis | Hífen condicional (U+00AD), espaços de largura zero (U+200B–U+200D), U+2060 e BOM são descartados. |
| Aspas e travessões | Aspas curvas → aspas retas; travessões/hífens Unicode → `-`. |
| Pontuação | **Preservada.** `defiro, o pedido` não corresponde a `"defiro o pedido"`. |
| Acentos | **Preservados.** O modo "Ignorar acentos" (opcional e identificado no resultado) remove diacríticos (NFD + remoção de marcas; `ç` → `c`). |

## Modos de correspondência

- **Frase exata** (entre aspas): a sequência normalizada deve ocorrer de forma contígua e, quando
  a frase começa/termina com letra ou número, o caractere anterior/posterior não pode ser letra ou
  número (limites de palavra). Assim, `"defiro o pedido"` não corresponde a `indefiro o pedido`.
- **Substring** (sem aspas): a sequência normalizada em qualquer posição, sem limites de palavra.

## Contagem de ocorrências

Todas as ocorrências **não sobrepostas** de cada termo em cada documento são registradas, da
esquerda para a direita.

## Posições

Cada caractere normalizado guarda o intervalo do caractere de origem. A posição gravada
(`start_pos`/`end_pos`) refere-se ao texto armazenado; `texto[start:end]` é exatamente o trecho
destacado. Para PDFs, a página é obtida do mapa de páginas registrado na extração.
