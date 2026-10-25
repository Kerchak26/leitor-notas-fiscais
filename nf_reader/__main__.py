"""Ponto de entrada: python -m nf_reader [email|pasta CAMINHO] [opções]"""

from __future__ import annotations

import argparse
import logging
from decimal import Decimal
from pathlib import Path

from . import exporter
from .config import Config
from .parser import data_br
from .processamento import Lancamento, processar_documentos

log = logging.getLogger("nf_reader")


def processar_email(config: Config) -> list[Lancamento]:
    from .email_client import ClienteEmail, pasta_do_email, salvar_anexo

    arquivo_estado = config.arquivo_saida.parent / "emails_processados.json"
    processados = exporter.carregar_processados(arquivo_estado)
    lancamentos: list[Lancamento] = []

    with ClienteEmail(config) as cliente:
        for msg in cliente.buscar_emails(processados):
            pasta = pasta_do_email(msg, config.pasta_anexos)
            pdfs = [(str(salvar_anexo(a, pasta)), a.conteudo) for a in msg.anexos]
            log.info("E-mail '%s': %d PDF(s)", msg.assunto, len(pdfs))
            lancamentos += processar_documentos(pdfs, origem=msg.descricao)
            processados.add(msg.message_id)
            exporter.salvar_processados(processados, arquivo_estado)
    return lancamentos


def processar_pasta(pasta: Path) -> list[Lancamento]:
    """Cada subpasta é tratada como um e-mail (nota e boleto que vieram juntos)."""
    arquivos = sorted(pasta.glob("**/*.pdf")) if pasta.is_dir() else [pasta]
    grupos: dict[Path, list[Path]] = {}
    for arquivo in arquivos:
        grupos.setdefault(arquivo.parent, []).append(arquivo)
    lancamentos: list[Lancamento] = []
    for arquivos_grupo in grupos.values():
        lancamentos += processar_documentos([(str(a), a) for a in arquivos_grupo])
    return lancamentos


def _moeda(valor) -> str:
    if valor is None:
        return "-"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def imprimir_resumo(lancamentos: list[Lancamento]) -> None:
    if not lancamentos:
        print("Nenhuma nota fiscal ou boleto encontrado.")
        return
    print(f"\n{'Número NF':>16}  {'Emissão':10}  {'Valor NF':>12}  {'Vencimento':10}  {'Boleto':>12}  Emitente")
    print("-" * 100)
    for lanc in lancamentos:
        d = lanc.como_dict()
        numero = (d["numero"] or "-") + (f" ({d['parcela']})" if d["parcela"] else "")
        print(f"{numero:>16}  {data_br(d['data_emissao']):10}  {_moeda(d['valor_nota']):>12}  "
              f"{data_br(d['vencimento']):10}  {_moeda(d['valor_boleto']):>12}  {(d['emitente'] or '-')[:35]}")
        if d["avisos"]:
            for aviso in d["avisos"].split("; "):
                print(f"{'':12}⚠ {aviso}")


def lancar_nas_capas(arquivo_config: Path, lancamentos: list[Lancamento]) -> None:
    from .capas import ConfigCapas, atualizar_capas

    cfg = ConfigCapas.carregar(arquivo_config)
    resultados = atualizar_capas(cfg, lancamentos)
    pendentes = [r for r in resultados if r.situacao.startswith("pendente")]
    lancados = [r for r in resultados if r.celula]
    print(f"\nCapas: {len(lancados)} lançamento(s) na cópia para conferência, {len(pendentes)} pendente(s)")
    for r in lancados:
        print(f"  ✔ {r.unidade} / {r.aba} / {r.celula}: Nota {r.lanc.numero} R$ {_moeda(Decimal(r.lanc.valor))}")
    for r in pendentes:
        print(f"  ⚠ Nota {r.lanc.numero or '-'} ({(r.lanc.emitente or '-')[:30]}): {r.situacao[10:]}")
    print(f"Cópias e relatório em: {cfg.pasta_conferencia}")


def main(argv: list[str] | None = None) -> None:
    comuns = argparse.ArgumentParser(add_help=False)
    comuns.add_argument("-o", "--saida", type=Path, help="arquivo de saída .xlsx ou .csv")
    comuns.add_argument("-v", "--verbose", action="store_true")
    comuns.add_argument("--capas", nargs="?", const=Path("capas.toml"), type=Path, metavar="CONFIG",
                        help="também lança as notas nas capas de custos (padrão: capas.toml)")

    ap = argparse.ArgumentParser(prog="nf_reader", description="Leitor automático de notas fiscais")
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("email", parents=[comuns], help="lê a caixa de e-mail configurada no .env")
    p = sub.add_parser("pasta", parents=[comuns], help="processa PDFs de uma pasta local (ou um único arquivo)")
    p.add_argument("caminho", type=Path)
    c = sub.add_parser("capas", parents=[comuns],
                       help="refaz as cópias das capas com as notas já lidas (após mudar capas.toml)")
    c.set_defaults(capas=Path("capas.toml"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = Config.carregar()
    if args.saida:
        config.arquivo_saida = args.saida

    if args.comando == "capas":
        lancar_nas_capas(args.capas, [])
        return

    lancamentos = processar_email(config) if args.comando == "email" else processar_pasta(args.caminho)
    lancamentos = list({l.id: l for l in lancamentos}.values())  # mesma nota recebida 2x
    imprimir_resumo(lancamentos)
    if lancamentos:
        novos = exporter.salvar(lancamentos, config.arquivo_saida)
        print(f"\n{novos} lançamento(s) novo(s) gravado(s) em {config.arquivo_saida}")
    if args.capas:
        lancar_nas_capas(args.capas, lancamentos)


if __name__ == "__main__":
    main()
