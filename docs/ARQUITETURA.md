# Arquitetura e decisões técnicas

Este documento explica **como** o projeto resolve cada parte do problema e **por que**
cada decisão foi tomada. O uso do programa está no [README](../README.md).

## Sumário

1. [Fluxo geral](#1-fluxo-geral)
2. [Extração de texto preservando colunas](#2-extração-de-texto-preservando-colunas)
3. [Leitura da nota fiscal](#3-leitura-da-nota-fiscal)
4. [Leitura do boleto](#4-leitura-do-boleto)
5. [Classificação de páginas](#5-classificação-de-páginas)
6. [Associação boleto ↔ nota](#6-associação-boleto--nota)
7. [Lançamento nas capas de custos](#7-lançamento-nas-capas-de-custos)
8. [Idempotência: rodar várias vezes sem duplicar](#8-idempotência-rodar-várias-vezes-sem-duplicar)
9. [Princípios de projeto](#9-princípios-de-projeto)

---

## 1. Fluxo geral

```
e-mail ─► anexos PDF ─► páginas ─► {nota, boleto} ─► associação ─► planilha / capas
```

A unidade de trabalho é **o e-mail**, não o PDF: nota e boleto que chegam juntos se
referem um ao outro, e isso ajuda a associá-los. No modo `pasta`, cada subpasta faz o
papel de um e-mail. Por isso os anexos são salvos em `dados/anexos/<assunto do e-mail>/`.

## 2. Extração de texto preservando colunas

O DANFE é um formulário em tabela: o **rótulo** fica numa linha e o **valor** na linha
de baixo, lado a lado com outros valores:

```
VALOR DO ICMS     VALOR TOTAL DOS PRODUTOS     VALOR TOTAL DA NOTA
   390,11                 3.250,90                  3.250,90
```

Uma busca ingênua ("primeiro valor depois de *VALOR TOTAL DA NOTA*") pega `390,11`, que
é o ICMS. A primeira versão do projeto cometia exatamente esse erro.

**Solução:** o texto é extraído com `pdfplumber` em modo `layout=True`, que mantém os
espaços horizontais. Na função `_apos_rotulo` (`parser.py`):

1. procura o valor **na mesma linha** do rótulo, desde que não haja outro rótulo entre os dois;
2. se não achar, olha as **linhas de baixo** e escolhe o valor cuja posição horizontal
   mais se sobrepõe à do rótulo (`_distancia` entre intervalos de colunas).

O mesmo princípio vale no boleto, para "Beneficiário" e "Vencimento": um texto a 3 ou
mais espaços do rótulo já pertence a outra coluna.

## 3. Leitura da nota fiscal

### Chave de acesso (NF-e)

A chave de 44 dígitos impressa no DANFE codifica vários campos:

| Posições | Campo |
|---|---|
| 1–2 | UF |
| 3–6 | Ano e mês de emissão (AAMM) |
| 7–20 | **CNPJ do emitente** |
| 21–22 | Modelo (55 = NF-e) |
| 23–25 | **Série** |
| 26–34 | **Número da nota** |
| 35 | Tipo de emissão |
| 36–43 | Código numérico |
| 44 | Dígito verificador (módulo 11) |

Antes de usar a chave, o programa **confere o dígito verificador** (`_chave_valida`).
Uma sequência qualquer de 44 dígitos que não passe na conferência é descartada. Com a
chave válida, número, série e CNPJ saem sem depender do layout do DANFE.

### Demais campos

Sem chave (NFS-e, por exemplo), os campos são procurados por **listas de rótulos em
ordem de prioridade** (`ROTULOS_VALOR_TOTAL`, `ROTULOS_EMISSAO`…). O texto é
normalizado antes (maiúsculas, sem acento), então "Data de Emissão", "DATA DA EMISSÃO"
e "data emissao" são equivalentes.

O **emitente** tem três estratégias, da mais confiável para a menos confiável:

1. canhoto do DANFE: "RECEBEMOS DE **\<EMPRESA\>** OS PRODUTOS…";
2. rótulos "Razão Social", "Prestador de Serviços"…;
3. a linha imediatamente acima do primeiro CNPJ.

Valores monetários usam `Decimal`, nunca `float`, para evitar erros de arredondamento
como `0.1 + 0.2 = 0.30000000000000004`.

## 4. Leitura do boleto

### Linha digitável

A linha digitável (padrão FEBRABAN) tem 47 dígitos:

```
AAABC.CCCCX  DDDDD.DDDDDY  EEEEE.EEEEEZ  K  FFFFVVVVVVVVVV
```

| Parte | Significado |
|---|---|
| `AAA` | Banco |
| `B` | Moeda (9 = real) |
| `C…`, `D…`, `E…` | Campo livre (definido pelo banco) |
| `X`, `Y`, `Z` | DV de cada campo (**módulo 10**) |
| `K` | DV geral do código de barras (**módulo 11**) |
| `FFFF` | **Fator de vencimento** |
| `VVVVVVVVVV` | **Valor** em centavos |

O programa só aceita a linha se os **quatro dígitos verificadores** baterem
(`linha_valida`). Com isso, vencimento e valor não dependem do layout do banco: estão
dentro de um número autoverificável.

### Fator de vencimento e o "bug do milênio" dos boletos

O fator é o número de dias desde **07/10/1997**. Com 4 dígitos, ele chegou a 9999 em
**21/02/2025**. A FEBRABAN definiu que, no dia seguinte, o fator **recomeçaria em 1000**.
Ou seja, o mesmo fator corresponde a duas datas separadas por 9000 dias (~25 anos):

| Fator | Ciclo antigo | Ciclo novo |
|---|---|---|
| 1000 | 03/07/2000 | 22/02/2025 |
| 1622 | 17/03/2002 | 06/11/2026 |

`vencimento_pelo_fator` calcula as duas datas e escolhe a **mais próxima de hoje**, o que
é inequívoco na prática. Os testes cobrem os dois ciclos.

Se a data impressa em "Vencimento" for diferente da data da linha digitável, prevalece
a **linha digitável**, porque ela tem dígito verificador. A divergência vira um aviso,
pois é mais provável que o rótulo tenha sido lido errado (ex.: "Data do Documento").

## 5. Classificação de páginas

Fornecedores mandam nota e boleto de vários jeitos: dois PDFs, um PDF com as duas coisas,
um PDF com várias notas e vários boletos. Por isso cada **página** recebe uma pontuação:

- **boleto:** palavras como "Nosso Número", "Ficha de Compensação", "Beneficiário"
  (1 ponto cada) + **5 pontos** se houver linha digitável válida;
- **nota:** "DANFE", "Nota Fiscal", "NFS-e"… (1 ponto cada) + **5 pontos** se houver
  chave de acesso válida.

Páginas sem pontuação (continuação de um DANFE com muitos itens) herdam o tipo da página
anterior. Páginas de nota consecutivas formam uma só nota, a não ser que apareça uma
chave de acesso diferente, o que indica o começo de outra nota.

## 6. Associação boleto ↔ nota

Cada par (boleto, nota) do mesmo e-mail recebe uma pontuação (`_pontuacao`):

| Evidência | Pontos |
|---|---|
| CNPJ do emitente da nota aparece no boleto | 3 |
| Número da nota aparece em "Nº do Documento" do boleto (ex.: `4587/1`) | 3 |
| Valor do boleto = valor total da nota | 2 |
| Vencimento do boleto = vencimento da duplicata no DANFE | 2 |
| Primeira palavra do emitente = primeira palavra do beneficiário | 1 |

Os boletos são processados **do par mais evidente para o menos evidente**, para que as
decisões fáceis restrinjam as difíceis. Casos especiais:

- **uma única nota no e-mail:** todos os boletos vão para ela (parcelas);
- **empate** (ex.: duas notas do mesmo fornecedor, com o mesmo valor, e boletos sem o
  número da nota): o boleto vai para a nota que ainda não tem boleto, e o lançamento
  recebe o aviso *"associado por aproximação; confira"*. Uma versão anterior mandava os
  dois boletos para a mesma nota. O teste
  `test_notas_identicas_nao_ficam_sem_boleto_e_geram_aviso` evita que esse erro volte;
- **parcelas:** cada boleto vira uma linha ("1/2", "2/2"). Se a soma não bater com o
  valor da nota, a planilha avisa.

## 7. Lançamento nas capas de custos

### Lendo a estrutura da aba (`ler_estrutura`)

As capas não seguem um gabarito fixo de células. O programa **descobre** a estrutura:

1. **Linha de cabeçalho:** a primeira linha (até a 15ª) com 3 ou mais nomes de meses.
   Aceita "Outubro", "OUT", "out/26" ou células com data.
2. **Ano de cada coluna:** do próprio cabeçalho ("out/26"); senão, das linhas de cima,
   resolvendo **células mescladas** ("2026" mesclado sobre Janeiro…Dezembro); senão, por
   sequência (Dezembro → Janeiro vira o ano).
3. **Coluna "Contratos"**, as **linhas de contrato** e a linha **"Valor Total"**, que
   nunca é alterada, assim como a coluna "Total". As fórmulas delas se atualizam sozinhas.

### Escolhendo unidade, aba e contrato

```
unidade:  regra → CNPJ do destinatário → endereço → assunto do e-mail
aba:      regra → nome da aba contido na razão social → desempate pelo histórico
contrato: regra → único contrato da aba → contrato que já teve esse valor
```

- Cada critério só decide se apontar **exatamente uma** opção. Mais de uma é tratado
  como ambiguidade, e a nota vira pendência com o motivo.
- Endereços são comparados de forma "compacta": sem acento, sem pontuação e com
  "Avenida"/"Av." equivalentes.
- Nomes de aba curtos (até 4 letras) só valem como **palavra inteira**, para "ZE" não
  bater dentro de "AZEVEDO".
- O **histórico de valores** aproveita uma característica do domínio: contratos têm
  valor mensal recorrente. Se a aba tem dois contratos e a nota é de R$ 640,00, o
  contrato que foi R$ 640,00 nos meses anteriores é o certo.

### Escrevendo

| Célula do mês | Ação |
|---|---|
| vazia | escreve o valor |
| número | vira soma: `=<anterior>+<novo>` |
| soma criada pelo programa (`=a+b`) | acrescenta `+<novo>` |
| texto ou outra fórmula | **não mexe**, vira pendência |

A soma visível (em vez do total já calculado) permite conferir a composição do valor
direto na barra de fórmulas. A célula fica verde, recebe um comentário com as notas, e a
anotação "Nota X - Vencimento …" é escrita embaixo da tabela.

## 8. Idempotência: rodar várias vezes sem duplicar

"Idempotente" significa que rodar duas vezes tem o mesmo efeito que rodar uma vez:

| Nível | Mecanismo |
|---|---|
| E-mail | `emails_processados.json` guarda os `Message-ID` já lidos |
| Planilha de notas | coluna oculta **ID** (chave de acesso + linha digitável) |
| Capas | a anotação **"Nota X"** na própria planilha indica que ela já foi lançada |

Para as capas, todas as notas lidas ficam em `lancamentos_capas.json`. A cada execução a
cópia é **refeita do zero** a partir da capa original, aplicando as notas cuja anotação
ainda não está nela. Consequências:

- rodar várias vezes antes de conferir não soma a mesma nota duas vezes;
- depois que a pessoa copia a cópia por cima da original, as notas passam a constar como
  "já está na capa original" e a cópia antiga é removida;
- uma regra nova em `capas.toml` vale também para notas que tinham ficado pendentes,
  sem precisar ler o e-mail de novo (`python -m nf_reader capas`).

## 9. Princípios de projeto

- **Nunca lançar no chute.** Em dados financeiros, um lançamento errado em silêncio é
  pior que uma pendência explícita. Toda ambiguidade vira aviso ou pendência com motivo.
- **Não destruir nada.** A capa original nunca é aberta para escrita; a caixa de e-mail
  não é alterada (`BODY.PEEK`); arquivos com o mesmo nome não são sobrescritos.
- **Preferir dados autoverificáveis.** Chave de acesso e linha digitável têm dígitos
  verificadores, então são usadas antes dos rótulos impressos.
- **Configuração em vez de código.** Unidades, endereços e exceções ficam em
  `capas.toml`, que alguém sem conhecimento de programação consegue editar.
- **Testes com dados fictícios gerados na hora.** O repositório não contém nenhum
  documento real; `tests/gerar_exemplos.py` gera notas, boletos (com dígitos
  verificadores válidos) e capas de custos.
