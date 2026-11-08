"""Orquestrador do RPA de contas a pagar.

Etapas (cada uma registrada no log):
  1. Captura dos documentos (caixa de e-mail via IMAP ou pasta local)
  2. Leitura dos PDFs: separa notas e boletos e associa um ao outro
  3. Validação (regex) e regras de negócio, com consulta à BrasilAPI
  4. Planilha Excel de controle das notas
  5. Lançamento nas capas de custos (opcional)
  6. Lançamento dos títulos no ERP via Selenium (opcional)
  7. Relatório de execução em Excel

Resiliência: cada e-mail e cada título é processado dentro de try/except. Um PDF
corrompido, a API fora do ar ou um título recusado pelo ERP viram INSUCESSO com
o motivo no relatório, e o robô segue para o próximo item.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import exporter
from .api_brasil import URL_PADRAO, BrasilAPI
from .config import Config
from .processamento import Lancamento, processar_documentos
from .regras import INSUCESSO, SUCESSO, Avaliacao, avaliar
from .validacoes import formatar_data, formatar_linha_digitavel, formatar_moeda

log = logging.getLogger("nf_reader.rpa")

IGNORADO = "IGNORADO"
FORMATO_LOG = "%(asctime)s | %(levelname)-7s | %(message)s"


@dataclass
class OpcoesRPA:
    origem: str                              # "email" ou "pasta"
    caminho: Path | None = None              # pasta com os PDFs (origem "pasta")
    pasta_saida: Path = Path("dados")
    usar_erp: bool = False
    url_erp: str | None = None
    headless: bool = True
    pausa_erp: float = 0.0
    arquivo_capas: Path | None = None
    url_api: str = URL_PADRAO
    usar_api: bool = True
    modo_cnpj: str = "avisar"
    hoje: date | None = None


@dataclass
class ResultadoRPA:
    avaliacoes: list[Avaliacao] = field(default_factory=list)
    arquivo_log: Path | None = None
    relatorio: Path | None = None
    planilha: Path | None = None
    prints: list[Path] = field(default_factory=list)
    duracao: float = 0.0

    def contar(self, status: str) -> int:
        return sum(1 for a in self.avaliacoes if a.status == status)


# ------------------------------------------------------------------- logs ----

def configurar_logs(pasta: Path, nivel: int = logging.INFO) -> Path:
    """Log de todas as etapas na tela e em logs/rpa_AAAAMMDD_HHMMSS.log."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"rpa_{datetime.now():%Y%m%d_%H%M%S}.log"
    raiz = logging.getLogger()
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
    formato = logging.Formatter(FORMATO_LOG, datefmt="%d/%m/%Y %H:%M:%S")
    for handler in (logging.FileHandler(arquivo, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formato)
        raiz.addHandler(handler)
    raiz.setLevel(nivel)
    for ruidoso in ("selenium", "urllib3", "pdfminer", "pdfplumber"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    return arquivo


def _etapa(numero: int, texto: str) -> None:
    log.info("=" * 70)
    log.info("ETAPA %d/7 - %s", numero, texto)
    log.info("=" * 70)


# ------------------------------------------------------------- histórico ----

def _carregar_historico(arquivo: Path) -> dict:
    try:
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
    except (OSError, json.JSONDecodeError) as erro:
        log.warning("Histórico ilegível (%s); começando vazio", erro)
        return {}


def _salvar_historico(historico: dict, arquivo: Path) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(historico, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- etapas ----

def _capturar(op: OpcoesRPA, config: Config) -> list[tuple[str, list[tuple[str, bytes | Path]]]]:
    """Grupos de PDFs: cada grupo é um e-mail (ou uma subpasta)."""
    grupos = []
    if op.origem == "pasta":
        if not op.caminho or not op.caminho.exists():
            raise FileNotFoundError(f"pasta de entrada não encontrada: {op.caminho}")
        arquivos = sorted(op.caminho.glob("**/*.pdf")) if op.caminho.is_dir() else [op.caminho]
        por_pasta: dict[Path, list[Path]] = {}
        for arquivo in arquivos:
            por_pasta.setdefault(arquivo.parent, []).append(arquivo)
        for pasta, lista in por_pasta.items():
            grupos.append((f"pasta {pasta.name}", [(str(a), a) for a in lista]))
            log.info("Capturado: pasta '%s' com %d PDF(s)", pasta.name, len(lista))
    else:
        from .email_client import ClienteEmail, pasta_do_email, salvar_anexo

        estado = op.pasta_saida / "emails_processados.json"
        processados = exporter.carregar_processados(estado)
        with ClienteEmail(config) as cliente:
            for msg in cliente.buscar_emails(processados):
                pasta = pasta_do_email(msg, config.pasta_anexos)
                pdfs = [(str(salvar_anexo(a, pasta)), a.conteudo) for a in msg.anexos]
                grupos.append((msg.descricao, pdfs))
                processados.add(msg.message_id)
                log.info("Capturado: e-mail '%s' com %d PDF(s)", msg.assunto, len(pdfs))
        exporter.salvar_processados(processados, estado)
    return grupos


def _unidade(lanc: Lancamento, arquivo_capas: Path | None) -> str:
    if not arquivo_capas or not lanc.nota:
        return "Não informada"
    try:
        from .capas import ConfigCapas, LancamentoCapa, _regra_aplicavel, identificar_unidade

        cfg = ConfigCapas.carregar(arquivo_capas)
        lc = LancamentoCapa.de(lanc)
        unidade, _ = identificar_unidade(cfg, lc, _regra_aplicavel(cfg.regras, lc))
        return unidade.nome if unidade else "Não identificada"
    except Exception as erro:  # noqa: BLE001 - a unidade é informativa, não pode parar o robô
        log.warning("Não foi possível identificar a unidade: %s", erro)
        return "Não identificada"


def _lancar_no_erp(op: OpcoesRPA, aprovadas: list[Avaliacao], resultado: ResultadoRPA) -> None:
    from .erp_selenium import ERP_PADRAO, ErroERP, RoboERP, TituloERP

    pasta_prints = op.pasta_saida / "evidencias" / f"erp_{datetime.now():%Y%m%d_%H%M%S}"
    try:
        with RoboERP(url=op.url_erp or ERP_PADRAO, headless=op.headless, pasta_prints=pasta_prints,
                     pausa=op.pausa_erp) as robo:
            robo.entrar()
            for av in aprovadas:
                n, b, lanc = av.lancamento.nota, av.lancamento.boleto, av.lancamento
                titulo = TituloERP(
                    fornecedor=(n.emitente or "")[:60], cnpj=n.cnpj_emitente,
                    unidade=_unidade(lanc, op.arquivo_capas), numero_nf=n.numero,
                    parcela=lanc.parcela or "1/1",
                    valor=formatar_moeda(b.valor if lanc.parcela else n.valor_total),
                    vencimento=formatar_data(lanc.vencimento), data_pagamento=formatar_data(av.data_pagamento),
                    linha_digitavel=formatar_linha_digitavel(b.linha_digitavel), prioridade=av.prioridade,
                )
                for tentativa in (1, 2):
                    try:
                        av.protocolo_erp = robo.lancar(titulo)
                        break
                    except ErroERP as erro:
                        if "recusou" in str(erro) or tentativa == 2:
                            av.falha(f"ERP: {erro}")
                            break
                        log.warning("ERP: falha na tentativa %d (%s); tentando de novo", tentativa, erro)
            resultado.prints += robo.prints
    except ErroERP as erro:
        log.error("ERP indisponível: %s", erro)
        for av in aprovadas:
            if not av.protocolo_erp and av.status == SUCESSO:
                av.falha(f"ERP indisponível: {erro}")


# -------------------------------------------------------------- principal ----

def executar(op: OpcoesRPA) -> ResultadoRPA:
    inicio = time.monotonic()
    resultado = ResultadoRPA()
    resultado.arquivo_log = configurar_logs(op.pasta_saida / "logs")
    hoje = op.hoje or date.today()
    log.info("RPA Contas a Pagar iniciado | origem=%s | ERP=%s | data de referência=%s",
             op.origem, "sim" if op.usar_erp else "não", formatar_data(hoje))
    config = Config.carregar()
    config.arquivo_saida = op.pasta_saida / "notas_fiscais.xlsx"

    # 1 - captura
    _etapa(1, "Captura dos documentos")
    try:
        grupos = _capturar(op, config)
    except Exception as erro:  # noqa: BLE001
        log.exception("Falha na captura: %s", erro)
        grupos = []
    log.info("%d grupo(s) de documentos para processar", len(grupos))

    # 2 - leitura dos PDFs (um e-mail com problema não derruba os outros)
    _etapa(2, "Leitura dos PDFs (notas e boletos)")
    lancamentos: list[Lancamento] = []
    for origem, pdfs in grupos:
        try:
            achados = processar_documentos(pdfs, origem=origem)
            log.info("%s: %d lançamento(s) identificados", origem, len(achados))
            lancamentos += achados
        except Exception as erro:  # noqa: BLE001
            log.exception("Erro ao ler os PDFs de %s", origem)
            av = Avaliacao(Lancamento(origem_email=origem))
            av.falha(f"erro ao ler os PDFs: {erro}")
            resultado.avaliacoes.append(av)

    # 3 - validação e regras
    _etapa(3, "Validação (regex) e regras de negócio com a BrasilAPI")
    api = BrasilAPI(op.url_api, arquivo_cache=op.pasta_saida / "cache_api.json") if op.usar_api else None
    historico_arq = op.pasta_saida / "historico_rpa.json"
    historico = _carregar_historico(historico_arq)
    vistos: set[str] = set()
    for lanc in lancamentos:
        try:
            if lanc.id in vistos:
                continue
            vistos.add(lanc.id)
            if lanc.id in historico:
                av = Avaliacao(lanc, status=IGNORADO)
                anterior = historico[lanc.id]
                av.motivos.append(f"já processado em {anterior['data']} (protocolo {anterior['protocolo']})")
                log.info("REGRA: %s -> IGNORADO: %s", av.descricao, av.motivos[0])
            else:
                av = avaliar(lanc, api, hoje=hoje, modo_cnpj=op.modo_cnpj)
        except Exception as erro:  # noqa: BLE001
            log.exception("Erro inesperado ao avaliar um lançamento")
            av = Avaliacao(lanc)
            av.falha(f"erro inesperado na validação: {erro}")
        resultado.avaliacoes.append(av)
    aprovadas = [a for a in resultado.avaliacoes if a.status == SUCESSO]
    log.info("Aprovados: %d | Reprovados: %d | Ignorados: %d", len(aprovadas),
             resultado.contar(INSUCESSO), resultado.contar(IGNORADO))

    # 4 - planilha de controle
    _etapa(4, "Planilha Excel de controle das notas")
    try:
        novos = exporter.salvar([a.lancamento for a in resultado.avaliacoes if a.lancamento.nota or a.lancamento.boleto],
                                config.arquivo_saida)
        resultado.planilha = config.arquivo_saida
        log.info("%d linha(s) nova(s) em %s", novos, config.arquivo_saida)
    except Exception as erro:  # noqa: BLE001 - ex.: planilha aberta no Excel
        log.exception("Não foi possível gravar a planilha (está aberta no Excel?): %s", erro)

    # 5 - capas de custos
    _etapa(5, "Lançamento nas capas de custos")
    if op.arquivo_capas and aprovadas:
        try:
            from .capas import ConfigCapas, atualizar_capas

            res = atualizar_capas(ConfigCapas.carregar(op.arquivo_capas), [a.lancamento for a in aprovadas])
            log.info("Capas: %d lançado(s), %d pendente(s)", sum(1 for r in res if r.celula),
                     sum(1 for r in res if r.situacao.startswith("pendente")))
        except Exception as erro:  # noqa: BLE001
            log.exception("Falha no lançamento das capas: %s", erro)
    else:
        log.info("Etapa não solicitada (use --capas) ou nada aprovado")

    # 6 - ERP
    _etapa(6, "Lançamento dos títulos no ERP (Selenium)")
    if op.usar_erp and aprovadas:
        _lancar_no_erp(op, aprovadas, resultado)
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        for av in aprovadas:
            if av.protocolo_erp:
                historico[av.lancamento.id] = {"protocolo": av.protocolo_erp, "data": agora}
        _salvar_historico(historico, historico_arq)
    else:
        log.info("Etapa não solicitada (use --erp) ou nada aprovado")

    # 7 - relatório
    _etapa(7, "Relatório de execução")
    resultado.duracao = time.monotonic() - inicio
    try:
        resultado.relatorio = salvar_relatorio(resultado, op.pasta_saida)
        log.info("Relatório: %s", resultado.relatorio)
    except Exception as erro:  # noqa: BLE001
        log.exception("Não foi possível gravar o relatório: %s", erro)
    log.info("RPA finalizado em %.1fs | SUCESSO=%d INSUCESSO=%d IGNORADO=%d | log: %s",
             resultado.duracao, resultado.contar(SUCESSO), resultado.contar(INSUCESSO),
             resultado.contar(IGNORADO), resultado.arquivo_log)
    return resultado


# -------------------------------------------------------------- relatório ----

COLUNAS_RELATORIO = [
    ("Status", 12), ("Motivo do insucesso", 60), ("Avisos", 60), ("NF", 12), ("Parcela", 8),
    ("Emitente", 36), ("CNPJ", 20), ("Situação na Receita", 18), ("Valor NF (R$)", 14),
    ("Valor boleto (R$)", 14), ("Vencimento", 12), ("Data de pagamento", 16), ("Prioridade", 11),
    ("Protocolo ERP", 16), ("Linha digitável", 56), ("Origem", 40), ("Arquivos", 60),
]
CORES_STATUS = {SUCESSO: "C6EFCE", INSUCESSO: "FFC7CE", IGNORADO: "E7E6E6"}


def salvar_relatorio(resultado: ResultadoRPA, pasta: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Execução"
    ws.append([c for c, _ in COLUNAS_RELATORIO])
    for celula in ws[1]:
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="1F4E78")
    for av in resultado.avaliacoes:
        lanc, n, b = av.lancamento, av.lancamento.nota, av.lancamento.boleto
        ws.append([
            av.status, "; ".join(av.motivos), "; ".join(av.avisos),
            n.numero if n else None, lanc.parcela, (n.emitente if n else None) or (b.beneficiario if b else None),
            n.cnpj_emitente if n else None, av.situacao_receita,
            float(n.valor_total) if n and n.valor_total is not None else None,
            float(b.valor) if b and b.valor is not None else None,
            lanc.vencimento, av.data_pagamento, av.prioridade, av.protocolo_erp,
            b.linha_digitavel if b else None, lanc.origem_email,
            lanc.como_dict()["arquivos"],
        ])
        linha = ws.max_row
        ws.cell(linha, 1).fill = PatternFill("solid", fgColor=CORES_STATUS.get(av.status, "FFFFFF"))
        for col in (9, 10):
            ws.cell(linha, col).number_format = "#,##0.00"
        for col in (11, 12):
            ws.cell(linha, col).number_format = "DD/MM/YYYY"
        for col in (2, 3):
            ws.cell(linha, col).alignment = Alignment(wrap_text=True, vertical="top")
    for i, (_, largura) in enumerate(COLUNAS_RELATORIO, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = largura
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    resumo = wb.create_sheet("Resumo")
    for linha in [
        ("Execução", datetime.now().strftime("%d/%m/%Y %H:%M:%S")),
        ("Duração (s)", round(resultado.duracao, 1)),
        ("Lançamentos avaliados", len(resultado.avaliacoes)),
        ("SUCESSO", resultado.contar(SUCESSO)),
        ("INSUCESSO", resultado.contar(INSUCESSO)),
        ("IGNORADO (já processados)", resultado.contar(IGNORADO)),
        ("Log", str(resultado.arquivo_log)),
    ]:
        resumo.append(linha)
    resumo.column_dimensions["A"].width = 28
    resumo.column_dimensions["B"].width = 60
    for c in resumo["A"]:
        c.font = Font(bold=True)

    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"relatorio_execucao_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    wb.save(destino)
    return destino
