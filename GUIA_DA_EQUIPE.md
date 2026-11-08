# Guia da equipe: RPA Contas a Pagar

Guia prático para **Kaua, @luenmarbriani e @Toxiros0804**: como rodar o robô, onde está
cada requisito do professor, como gravar o vídeo e como dividir a apresentação.

> **Datas:** prévia em **10/11** · apresentação final em **17/11**.
> O [README](README.md) é a página "vitrine" do projeto. Este guia é o nosso manual interno.

## Sumário

1. [O projeto em um minuto](#1-o-projeto-em-um-minuto)
2. [Onde está cada requisito do professor](#2-onde-está-cada-requisito-do-professor)
3. [Rodando no seu computador (Windows)](#3-rodando-no-seu-computador-windows)
4. [O que acontece quando o robô roda](#4-o-que-acontece-quando-o-robô-roda)
5. [Os 15 cenários de teste](#5-os-15-cenários-de-teste)
6. [Gerando as evidências de teste](#6-gerando-as-evidências-de-teste)
7. [Gravando o vídeo](#7-gravando-o-vídeo)
8. [Mapa dos arquivos](#8-mapa-dos-arquivos)
9. [Roteiro e divisão da apresentação](#9-roteiro-e-divisão-da-apresentação)
10. [Perguntas que o professor pode fazer](#10-perguntas-que-o-professor-pode-fazer)
11. [Checklist das entregas](#11-checklist-das-entregas)
12. [Trabalhando juntos no GitHub](#12-trabalhando-juntos-no-github)
13. [Problemas comuns](#13-problemas-comuns)

---

## 1. O projeto em um minuto

**O problema:** no setor de Contas a Pagar de uma empresa com várias unidades (nome preservado), o gestor
encaminha por e-mail as notas fiscais e os boletos dos fornecedores. Para cada nota, um
analista abre os PDFs, confere nota × boleto, digita tudo no ERP e atualiza a planilha de
custos ("capa") da unidade. São cerca de **8 minutos por nota** e **120 notas por mês**:
**16 horas** de trabalho manual todo mês. Com o robô, sobra só a conferência (cerca de
2 horas por mês, uma redução de 88%).

**A solução:** um robô em Python que faz esse trabalho sozinho:

```
E-mail → lê os PDFs → valida (regex) → aplica as regras (+ BrasilAPI) → Excel → ERP (Selenium) → relatório
```

O que está certo é lançado (**SUCESSO**). O que quebra uma regra de negócio **não** é
lançado e aparece no relatório com o motivo (**INSUCESSO**), por exemplo: "boleto vencido,
solicitar boleto atualizado". O analista só confere e trata as exceções.

**Tudo é fictício:** notas, boletos, empresas, CNPJs e capas de custos são gerados pelo
próprio projeto. Nenhum dado da empresa real está no repositório.

## 2. Onde está cada requisito do professor

| Requisito do enunciado | Onde está | Como mostrar |
|---|---|---|
| Integração com API | [`nf_reader/api_brasil.py`](nf_reader/api_brasil.py): BrasilAPI de **feriados** e de **CNPJ** | No log: linhas `API: GET https://brasilapi.com.br/...` |
| Gerar ou ler Excel | [`nf_reader/capas.py`](nf_reader/capas.py) (lê e grava as capas) e [`nf_reader/rpa.py`](nf_reader/rpa.py) (relatório) | Abrir `dados/relatorio_execucao_*.xlsx` |
| Selenium | [`nf_reader/erp_selenium.py`](nf_reader/erp_selenium.py) + o ERP simulado [`erp_simulado/index.html`](erp_simulado/index.html) | Rodar com `--erp --visivel` e ver o Chrome digitando |
| Logs de todos os passos | [`nf_reader/rpa.py`](nf_reader/rpa.py) (`configurar_logs`) | Abrir `dados/logs/rpa_*.log`: as 7 etapas e cada campo digitado |
| Regex para validar e formatar | [`nf_reader/validacoes.py`](nf_reader/validacoes.py) | CT06 (CNPJ inválido) e CT10 (linha digitável adulterada) |
| try/except (resiliência) | [`nf_reader/rpa.py`](nf_reader/rpa.py) e [`nf_reader/api_brasil.py`](nf_reader/api_brasil.py) | CT11 (PDF corrompido), CT12 (API fora), CT13 (ERP fora) |
| Evidências de sucesso e insucesso | [`evidencias/gerar_evidencias.py`](evidencias/gerar_evidencias.py) | Gera o `Evidencias_de_Teste_RPA.docx` |

**Regras de negócio** (em [`nf_reader/regras.py`](nf_reader/regras.py)), boas para citar
como "outros requisitos de negócio que melhoram a qualidade":

| Regra | Se não for atendida |
|---|---|
| CNPJ do emitente com formato e dígito verificador válidos | INSUCESSO |
| Fornecedor com situação ATIVA na Receita (BrasilAPI) | INSUCESSO (pagamento bloqueado) |
| Nota tem boleto e a linha digitável é válida | INSUCESSO |
| Valor do boleto igual ao valor da nota | INSUCESSO |
| Boleto não vencido | INSUCESSO (pedir boleto atualizado) |
| Vencimento em fim de semana ou feriado | Data de pagamento vai para o próximo dia útil |
| Vence em até 3 dias úteis | Prioridade **URGENTE** no ERP |
| Mesmo e-mail processado de novo | IGNORADO, citando o protocolo anterior |

## 3. Rodando no seu computador (Windows)

### Pré-requisitos (uma vez só)

1. **Python 3.11 ou mais novo**: <https://www.python.org/downloads/>. Na instalação, marque
   **"Add python.exe to PATH"**.
2. **Git**: <https://git-scm.com/download/win>.
3. **Google Chrome** instalado. O Selenium baixa o driver do Chrome sozinho na primeira vez
   (precisa de internet).

### Baixar e instalar o projeto

No **PowerShell**:

```powershell
git clone https://github.com/Kerchak26/leitor-notas-fiscais.git
cd leitor-notas-fiscais
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Se o PowerShell disser que a execução de scripts está desabilitada, rode uma vez
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e tente de novo.
> No Prompt de Comando (cmd), a ativação é `.venv\Scripts\activate.bat`.

Toda vez que abrir um terminal novo, entre na pasta e ative o ambiente
(`.venv\Scripts\Activate.ps1`) antes de rodar os comandos.

### Rodar o robô

```powershell
python tests/cenarios_rpa.py                                     # 1. cria os e-mails de teste
python -m nf_reader rpa pasta dados/cenarios/principal --erp --visivel --pausa 0.3   # 2. roda o robô
```

- `--erp` lança os títulos aprovados no ERP via Selenium.
- `--visivel` mostra o Chrome (sem ele, o navegador roda escondido).
- `--pausa 0.3` digita devagar, para dar para acompanhar (ótimo para o vídeo).

**Depois de rodar, abra:**

| Arquivo | O que é |
|---|---|
| `dados/relatorio_execucao_*.xlsx` | Resultado de cada nota: SUCESSO / INSUCESSO e o motivo |
| `dados/logs/rpa_*.log` | Log de todas as etapas |
| `dados/notas_fiscais.xlsx` | Planilha de controle das notas |
| `dados/evidencias/erp_*/` | Prints que o robô tirou do ERP |

> Rodou duas vezes e tudo saiu como **IGNORADO**? É a regra de duplicidade funcionando.
> Para começar do zero, apague a pasta `dados` e gere os cenários de novo.

### Outros comandos úteis

```powershell
python -m pytest                                  # 54 testes automatizados
python -m nf_reader rpa pasta dados/cenarios/api_fora --url-api https://nao-existe.invalid/api   # simula a API fora do ar
python -m nf_reader rpa pasta dados/cenarios/erp_fora --erp --url-erp http://127.0.0.1:9         # simula o ERP fora do ar
python -m nf_reader rpa --help                    # todas as opções
```

O **ERP simulado** também pode ser aberto à mão: dê dois cliques em
`erp_simulado/index.html` e entre com usuário `rpa.financeiro` e senha `demo123`.

## 4. O que acontece quando o robô roda

Cada etapa aparece no log como `ETAPA n/7`:

| Etapa | O que o robô faz | Requisito |
|---|---|---|
| 1. Captura | Lê os e-mails (ou uma pasta); cada subpasta é um "e-mail" | — |
| 2. Leitura dos PDFs | Separa o que é nota e o que é boleto (página por página) e liga um ao outro | — |
| 3. Validação e regras | Regex + dígitos verificadores + regras de negócio + consulta à BrasilAPI | API, regex |
| 4. Planilha Excel | Grava a planilha de controle das notas | Excel |
| 5. Capas de custos | Lança nas capas (só com `--capas` e um `capas.toml` configurado) | Excel |
| 6. ERP | Faz login, digita cada título aprovado, salva e lê o protocolo | Selenium |
| 7. Relatório | Gera o relatório de execução em Excel | Excel, logs |

Em todas as etapas há `try/except`: se algo falhar (PDF ilegível, API ou ERP fora do ar,
planilha aberta no Excel), o erro vai para o log e para o relatório, e o robô continua.

## 5. Os 15 cenários de teste

Gerados por [`tests/cenarios_rpa.py`](tests/cenarios_rpa.py), com datas calculadas a partir
do dia em que roda (um boleto "a vencer" nunca fica vencido com o tempo).

| ID | Cenário | Tipo | Resultado esperado |
|---|---|---|---|
| CT01 | Nota e boleto em PDFs separados | Sucesso | Lançado no ERP com protocolo |
| CT02 | Nota parcelada em dois boletos | Sucesso | Dois títulos (1/2 e 2/2) |
| CT03 | Vencimento no fim de semana | Sucesso | Pagamento na segunda-feira |
| CT04 | Nota e boleto no mesmo PDF | Sucesso | Páginas separadas e associadas |
| CT05 | Vence em até 3 dias úteis | Sucesso | Prioridade URGENTE |
| CT06 | CNPJ do emitente inválido | Insucesso | Barrado pela regex e pelo dígito verificador |
| CT07 | Valor do boleto ≠ valor da nota | Insucesso | Divergência de valor |
| CT08 | Boleto vencido | Insucesso | Pedir boleto atualizado |
| CT09 | Nota sem boleto | Insucesso | Pedir o boleto ao fornecedor |
| CT10 | Linha digitável adulterada | Insucesso | Dígito verificador não confere |
| CT11 | PDF corrompido | Insucesso | Erro registrado, robô continua |
| CT12 | BrasilAPI fora do ar | Sucesso | 3 tentativas no log, segue com aviso |
| CT13 | ERP fora do ar | Insucesso | Erro registrado, relatório gerado |
| CT14 | Mesmo e-mail duas vezes | Insucesso | IGNORADO, cita o protocolo anterior |
| CT15 | Fornecedor com CNPJ baixado | Insucesso | Pagamento bloqueado (teste automatizado) |

**Por que o log diz "CNPJ não encontrado na Receita Federal"?** Os CNPJs dos cenários são
inventados: têm dígito verificador válido, mas não existem. A BrasilAPI responde "não
encontrado" e, no modo padrão, o robô só avisa. Com `--modo-cnpj bloquear`, isso vira
INSUCESSO. Isso prova que a API está sendo chamada de verdade.

## 6. Gerando as evidências de teste

```powershell
python evidencias/gerar_evidencias.py
```

Em cerca de 1 minuto, o script roda todos os cenários (API real + ERP no Selenium) e
gera `dados/evidencias/Evidencias_de_Teste_RPA.docx` (e o `.pdf`, se o LibreOffice estiver
instalado). Para cada cenário, o documento traz: a entrada (o PDF recebido), o resultado
esperado e o obtido, a linha do relatório, o trecho do log e o print do ERP. Esse é o
arquivo da **entrega 3**.

## 7. Gravando o vídeo

Já existe um vídeo de 70 segundos pronto (enviado pelo Kaua). Para gravar outro:

1. Apague a pasta `dados` e rode `python tests/cenarios_rpa.py`.
2. Deixe o terminal numa metade da tela. O Chrome vai abrir na outra.
3. Comece a gravar a **tela inteira** com o [OBS Studio](https://obsproject.com/) (gratuito).
   A Xbox Game Bar (Win + Alt + R) grava só uma janela por vez, e o vídeo precisa mostrar o
   terminal e o Chrome juntos.
4. Rode `python -m nf_reader rpa pasta dados/cenarios/principal --erp --visivel --pausa 0.3`.
5. No fim, abra o `relatorio_execucao_*.xlsx` no Excel e mostre os status e os motivos.

**Roteiro sugerido de narração:** os e-mails recebidos → o log das etapas → as chamadas à
BrasilAPI → as regras reprovando os casos errados → o Chrome lançando no ERP → o relatório
com sucessos e insucessos.

## 8. Mapa dos arquivos

```
leitor-notas-fiscais/
├── nf_reader/                ← o robô
│   ├── __main__.py           comandos de terminal (rpa, email, pasta, capas)
│   ├── rpa.py                ★ orquestrador: as 7 etapas, logs, try/except, relatório
│   ├── regras.py             ★ regras de negócio (SUCESSO / INSUCESSO)
│   ├── validacoes.py         ★ expressões regulares (CNPJ, valores, datas, boleto)
│   ├── api_brasil.py         ★ BrasilAPI (feriados e CNPJ), com novas tentativas
│   ├── erp_selenium.py       ★ robô Selenium que lança no ERP
│   ├── email_client.py       lê a caixa de e-mail (IMAP)
│   ├── pdf_extractor.py      extrai o texto dos PDFs
│   ├── parser.py             lê a nota fiscal (número, CNPJ, valor, datas)
│   ├── boleto.py             lê o boleto (linha digitável, vencimento, valor)
│   ├── processamento.py      separa nota e boleto e liga um ao outro
│   ├── capas.py              lança nas capas de custos (Excel)
│   └── exporter.py           planilha de controle das notas
├── erp_simulado/index.html   ★ ERP de mentira para o Selenium usar
├── tests/
│   ├── cenarios_rpa.py       ★ gera os 15 cenários de teste
│   ├── test_rpa.py           testes do robô (regras, API, ERP)
│   └── ...                   demais testes (54 no total)
├── evidencias/
│   └── gerar_evidencias.py   ★ gera o documento de evidências (.docx)
├── docs/ARQUITETURA.md       explicação técnica detalhada
└── GUIA_DA_EQUIPE.md         este guia
```

★ = arquivos mais importantes para a apresentação do RPA.

## 9. Roteiro e divisão da apresentação

A apresentação tem 18 slides (link compartilhado pelo Kaua). Sugestão de divisão para
cerca de **15 minutos**. Ajustem como preferirem:

| Quem | Slides | Assunto | Tempo |
|---|---|---|---|
| Pessoa 1 | 1 a 7 | O case: empresa, processo, tarefa repetitiva, tempos e regras de negócio | ~5 min |
| Pessoa 2 | 8 a 14 | Fluxos AS-IS e TO-BE, requisitos e exemplos de código | ~5 min |
| Pessoa 3 | 15 a 18 | Vídeo, testes e evidências, ganhos e próximos passos | ~5 min |

Cada slide tem **notas do apresentador** com o que falar.

**Antes da prévia (10/11), preencher na apresentação:**
- [x] Tempos e volume: 8 min por nota e 120 notas/mês (16 h/mês → cerca de 2 h com o robô).
      O "~1 min de conferência por nota" com o robô é estimativa; ajustem se medirem outro valor.
- [ ] Nomes dos colegas, curso e professor na capa e no último slide.

## 10. Perguntas que o professor pode fazer

**"Onde está a integração com API?"**
BrasilAPI, em `api_brasil.py`. Duas consultas: feriados nacionais (para pagar no próximo dia
útil e calcular a urgência) e CNPJ (situação do fornecedor na Receita). No log aparecem as
URLs e os códigos de resposta HTTP.

**"Por que um ERP simulado?"**
Não podemos usar o sistema real da empresa num trabalho acadêmico. O ERP simulado tem o
que um sistema real tem: login, validação dos campos, protocolo e bloqueio de duplicidade. O
robô usa Selenium do mesmo jeito que usaria num sistema web de verdade.

**"E se a API ou o ERP cair?"**
A API é tentada 3 vezes, com espera de 1 s e depois 2 s. Se não responder, o robô segue com
o aviso "não verificado" (CT12). Se o ERP cair, os títulos viram INSUCESSO com o motivo e o
relatório é gerado mesmo assim (CT13). Tudo fica no log.

**"Onde estão as expressões regulares?"**
`validacoes.py`: CNPJ, linha digitável, valores (`1.234,56`), datas, número da nota e o
protocolo lido do ERP (`CP-2026-001001`). O `boleto.py` e o `parser.py` também usam regex
para achar os dados dentro do texto do PDF.

**"Como o robô sabe que o boleto é válido?"**
A linha digitável tem dígitos verificadores (módulo 10 em cada campo e módulo 11 no código de
barras). Se um número for alterado, a conta não fecha (CT10). Isso também protege contra
boletos fraudados.

**"Como o robô não paga duas vezes?"**
Cada título lançado tem o protocolo guardado em `dados/historico_rpa.json`. Se o mesmo e-mail
chegar de novo, sai como IGNORADO (CT14).

**"Como foi testado?"**
15 cenários de ponta a ponta, documentados nas evidências, mais 54 testes automatizados com
pytest, que rodam no GitHub a cada envio.

## 11. Checklist das entregas

| Entrega | Status | Onde |
|---|---|---|
| Apresentação (case, justificativa, AS-IS, TO-BE, código, vídeo, ganhos) | ✅ pronta; falta só os nomes na capa | Link compartilhado pelo Kaua |
| Vídeo da execução | ✅ pronto (70 s) | Arquivo enviado pelo Kaua; também está dentro do slide 15 |
| Código-fonte | ✅ | Este repositório |
| Evidências de teste (sucesso e insucesso) | ✅ 15/15 cenários aprovados | `python evidencias/gerar_evidencias.py` |

## 12. Trabalhando juntos no GitHub

1. Aceite o convite de colaborador (chega por e-mail do GitHub).
2. Para alterar algo, crie uma branch: `git checkout -b minha-alteracao`.
3. Rode `python -m pytest` antes de enviar: tudo precisa passar.
4. Envie e abra um Pull Request: `git push -u origin minha-alteracao`.

**Nunca envie dados reais da empresa** (notas, boletos, planilhas, CNPJs, endereços). Os
arquivos `.env`, `capas.toml` e a pasta `dados/` já ficam fora do Git. Mais detalhes em
[CONTRIBUTING.md](CONTRIBUTING.md).

## 13. Problemas comuns

| Problema | Solução |
|---|---|
| `python` não é reconhecido | Reinstale o Python marcando "Add python.exe to PATH" |
| `ModuleNotFoundError` | Ative o ambiente (`.venv\Scripts\Activate.ps1`) e rode `pip install -r requirements.txt` |
| O Chrome não abre / erro de driver | Atualize o Chrome e confira se há internet na primeira execução (o driver é baixado sozinho) |
| Tudo saiu IGNORADO | Regra de duplicidade: apague a pasta `dados` e gere os cenários de novo |
| Erro ao gravar a planilha | Feche o arquivo no Excel e rode de novo (o robô registra o erro e continua) |
| Caracteres estranhos no terminal | Use o Windows Terminal ou rode `set PYTHONUTF8=1` antes |
| API "não verificada" no log | Sem internet ou BrasilAPI fora do ar: é o tratamento de erro funcionando |
