# Guia de contribuição

Obrigado por ajudar! Este guia vale para os colaboradores do projeto e para quem quiser
contribuir de fora.

## ⚠️ Regra número 1: nenhum dado real no repositório

Este projeto nasceu de uma rotina de trabalho real, mas o repositório é **público**.
Nunca faça commit de:

- notas fiscais, boletos ou e-mails reais (nem "só para testar");
- planilhas de custos reais, nomes de unidades, fornecedores ou valores reais;
- `.env`, `capas.toml`, senhas, CNPJs ou endereços reais.

`.env`, `capas.toml` e a pasta `dados/` já estão no `.gitignore`. Para testar, use os
**dados fictícios** gerados por `python tests/gerar_exemplos.py`. Se precisar de um caso
novo (um layout diferente de boleto, por exemplo), recrie-o com dados inventados em
`tests/gerar_exemplos.py`.

## Preparando o ambiente

```bash
git clone https://github.com/Kerchak26/leitor-notas-fiscais.git
cd leitor-notas-fiscais
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest                 # tudo deve passar antes de você começar
```

## Fluxo de trabalho

1. Crie uma branch a partir da `main`:
   `git checkout -b feature/ocr-pdf-escaneado` (ou `fix/...`, `docs/...`).
2. Faça a alteração **com teste**. Bug corrigido = um teste que falhava antes e passa
   agora. Funcionalidade nova = testes do caso normal e dos casos de borda.
3. Rode `python -m pytest`.
4. Faça commits pequenos, com mensagens no imperativo:
   `Adiciona leitura de boletos de concessionária`.
5. Abra um **Pull Request** para a `main` explicando o quê e o porquê. Os testes rodam
   automaticamente no GitHub Actions e precisam passar.

## Padrões de código

- **Português** em nomes de variáveis, funções, comentários e mensagens (o domínio
  é brasileiro: NF-e, boleto, CNPJ…).
- Valores monetários sempre em `Decimal`, nunca `float`.
- Na dúvida, **não lançar**: gere um aviso ou uma pendência com o motivo. Um lançamento
  errado em silêncio é o pior resultado possível.
- Siga o estilo do código ao redor: funções pequenas, `dataclasses`, type hints.
- Decisões técnicas relevantes devem ser registradas em
  [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

## Onde mexer

| Quero… | Arquivo |
|---|---|
| Reconhecer um novo rótulo de nota (outra prefeitura) | `nf_reader/parser.py` (listas `ROTULOS_*`) |
| Melhorar a leitura de boletos | `nf_reader/boleto.py` |
| Mudar como boleto e nota são associados | `nf_reader/processamento.py` |
| Mudar o lançamento nas capas de custos | `nf_reader/capas.py` |
| Adicionar colunas à planilha | `nf_reader/exporter.py` |
| Criar um exemplo fictício novo | `tests/gerar_exemplos.py` |

## Ideias de contribuição

Veja a seção **Limitações e próximos passos** do [README](README.md): OCR, leitura do
XML da NF-e, boletos de concessionária, anexos `.zip`, alerta de vencimentos e interface
web são bons pontos de partida.
