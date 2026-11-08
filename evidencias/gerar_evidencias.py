"""Executa todos os cenários de teste do RPA e gera o documento de evidências (.docx).

Para cada cenário o documento traz: a entrada (o PDF recebido), o resultado
esperado e o obtido, a linha do relatório de execução, o trecho do log e, quando
há lançamento no ERP, o print da tela. No fim, o resultado dos testes automatizados.

Uso:  python evidencias/gerar_evidencias.py
Saída: dados/evidencias/Evidencias_de_Teste_RPA.docx (e .pdf, se o LibreOffice existir)
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pypdfium2  # noqa: E402
from docx import Document  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Cm, Pt, RGBColor  # noqa: E402

from nf_reader.regras import INSUCESSO, SUCESSO  # noqa: E402
from nf_reader.rpa import IGNORADO, OpcoesRPA, ResultadoRPA, executar  # noqa: E402
from tests import cenarios_rpa  # noqa: E402

SAIDA = Path("dados") / "evidencias"     # relativo: os logs não mostram caminhos da máquina
AZUL = RGBColor(0x1F, 0x4E, 0x78)
CORES = {SUCESSO: "C6EFCE", INSUCESSO: "FFC7CE", IGNORADO: "E7E6E6", "PASSOU": "C6EFCE", "FALHOU": "FFC7CE"}


# ------------------------------------------------------------- execuções ----

def rodar_cenarios(hoje: date) -> tuple[list, dict[str, ResultadoRPA], str]:
    pasta = SAIDA / "cenarios"
    shutil.rmtree(SAIDA, ignore_errors=True)
    cenarios = cenarios_rpa.gerar(pasta, hoje=hoje)
    execucoes = {
        "principal": OpcoesRPA("pasta", pasta / "principal", SAIDA / "execucao_principal", usar_erp=True, hoje=hoje),
        "api_fora": OpcoesRPA("pasta", pasta / "api_fora", SAIDA / "execucao_api_fora", usar_erp=True,
                              url_api="https://api-fora-do-ar.invalid/api", hoje=hoje),
        "erp_fora": OpcoesRPA("pasta", pasta / "erp_fora", SAIDA / "execucao_erp_fora", usar_erp=True,
                              url_erp="http://127.0.0.1:9/erp-fora-do-ar", hoje=hoje),
    }
    resultados = {nome: executar(op) for nome, op in execucoes.items()}
    # CT14: o mesmo e-mail do CT01 chega de novo, depois de já ter sido lançado
    [ct01] = list((pasta / "principal").glob("CT01 - *"))
    resultados["reexecucao"] = executar(OpcoesRPA("pasta", ct01, SAIDA / "execucao_principal", usar_erp=True,
                                                  hoje=hoje))
    pytest = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests"],
                            cwd=RAIZ, capture_output=True, text=True)
    return cenarios, resultados, pytest.stdout


def _avaliacoes_do_cenario(cenario, resultado: ResultadoRPA):
    if cenario.numero_nf:
        achadas = [a for a in resultado.avaliacoes if a.lancamento.nota and a.lancamento.nota.numero == cenario.numero_nf]
    else:
        achadas = [a for a in resultado.avaliacoes if cenario.codigo in (a.lancamento.origem_email or "")]
    return achadas


def _trecho_do_log(cenario, resultado: ResultadoRPA) -> list[str]:
    linhas = resultado.arquivo_log.read_text(encoding="utf-8").splitlines()
    if cenario.execucao in ("api_fora", "erp_fora", "reexecucao"):
        chaves = ("ETAPA", "API", "REGRA", "ERP", "finalizado", "ERROR")
        return [l for l in linhas if any(c in l for c in chaves) and "=====" not in l]
    marcas = [cenario.codigo] + ([f"NF {cenario.numero_nf}"] if cenario.numero_nf else ["NF ?", "ler o PDF"])
    trecho, no_erp = [], False
    for linha in linhas:
        if "ERP: lançando" in linha:
            no_erp = f"NF {cenario.numero_nf} " in linha
        elif "ERP: fechando" in linha:
            no_erp = False
        if no_erp or any(m in linha for m in marcas):
            trecho.append(linha)
    return trecho


def _situacao(cenario, avaliacoes) -> tuple[str, str]:
    esperado = {"Sucesso": SUCESSO, "Insucesso": INSUCESSO}[cenario.tipo]
    if cenario.codigo == "CT14":
        esperado = IGNORADO
    if not avaliacoes:
        return "FALHOU", "nenhum lançamento encontrado"
    obtido = ", ".join(sorted({a.status for a in avaliacoes}))
    ok = all(a.status == esperado for a in avaliacoes)
    if cenario.codigo in ("CT01", "CT02", "CT03", "CT04", "CT05", "CT12"):
        ok = ok and all(a.protocolo_erp for a in avaliacoes)
    return ("PASSOU" if ok else "FALHOU"), obtido


# ---------------------------------------------------------------- docx ----

def _sombrear(celula, cor: str) -> None:
    tc = celula._tc.get_or_add_tcPr()
    sombra = OxmlElement("w:shd")
    sombra.set(qn("w:val"), "clear")
    sombra.set(qn("w:color"), "auto")
    sombra.set(qn("w:fill"), cor)
    tc.append(sombra)


def _tabela(doc, cabecalho: list[str], linhas: list[list[str]], larguras: list[float], cor_coluna: int | None = None):
    tabela = doc.add_table(rows=1, cols=len(cabecalho))
    tabela.style = "Table Grid"
    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, texto in enumerate(cabecalho):
        c = tabela.rows[0].cells[i]
        c.text = texto
        _sombrear(c, "1F4E78")
        for r in c.paragraphs[0].runs:
            r.font.bold, r.font.color.rgb, r.font.size = True, RGBColor(0xFF, 0xFF, 0xFF), Pt(9)
    for linha in linhas:
        celulas = tabela.add_row().cells
        for i, texto in enumerate(linha):
            celulas[i].text = str(texto or "")
            for p in celulas[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(8.5)
            if cor_coluna is not None and i == cor_coluna and texto in CORES:
                _sombrear(celulas[i], CORES[texto])
    for linha in tabela.rows:
        for i, largura in enumerate(larguras):
            linha.cells[i].width = Cm(largura)
    return tabela


def _log(doc, linhas: list[str]) -> None:
    if not linhas:
        return
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.3)
    run = p.add_run("\n".join(re.sub(r"^\d{2}/\d{2}/\d{4} ", "", l) for l in linhas[:40]))
    run.font.name, run.font.size = "Consolas", Pt(7)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
    _sombrear_paragrafo(p, "F2F4F7")


def _sombrear_paragrafo(paragrafo, cor: str) -> None:
    ppr = paragrafo._p.get_or_add_pPr()
    sombra = OxmlElement("w:shd")
    sombra.set(qn("w:val"), "clear")
    sombra.set(qn("w:fill"), cor)
    ppr.append(sombra)


def _miniatura_pdf(pdf: Path, destino: Path) -> Path | None:
    try:
        pagina = pypdfium2.PdfDocument(str(pdf))[0]
        imagem = pagina.render(scale=1.4).to_pil()
        largura, altura = imagem.size
        imagem.crop((0, 0, largura, int(altura * 0.42))).save(destino)
        return destino
    except Exception:  # noqa: BLE001 - PDF corrompido (CT11) não tem miniatura
        return None


def montar_documento(cenarios, resultados: dict[str, ResultadoRPA], saida_pytest: str, hoje: date) -> Path:
    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.name, estilo.font.size = "Calibri", Pt(10.5)
    for secao in doc.sections:
        secao.left_margin = secao.right_margin = Cm(2)
        secao.top_margin = secao.bottom_margin = Cm(1.8)

    titulo = doc.add_heading("Evidências de Teste — RPA Contas a Pagar", 0)
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph("Leitura automática de notas fiscais e boletos, validação e lançamento no ERP")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    _tabela(doc, ["Item", "Informação"], [
        ["Projeto", "github.com/Kerchak26/leitor-notas-fiscais"],
        ["Equipe", "Kaua Queiroga, luenmarbriani, Toxiros0804"],
        ["Data da execução", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["Data de referência dos cenários", hoje.strftime("%d/%m/%Y")],
        ["Versão do código", commit or "-"],
        ["Ambiente", f"Python {platform.python_version()} · {platform.system()} · Chrome (Selenium) · BrasilAPI"],
    ], [5, 11])

    doc.add_heading("1. Objetivo e escopo", 1)
    doc.add_paragraph(
        "Comprovar que o robô processa corretamente os e-mails com notas fiscais e boletos: lança no ERP "
        "o que está correto (cenários de sucesso) e, quando uma regra de negócio não é atendida, não executa "
        "o processo completo, registra o motivo no log e no relatório de execução e segue para o próximo "
        "documento (cenários de insucesso). Também foram testadas falhas de infraestrutura (API e ERP fora "
        "do ar) e a reexecução do mesmo e-mail.")
    doc.add_paragraph(
        "Todos os documentos usados são fictícios, gerados por tests/cenarios_rpa.py. Os CNPJs têm dígito "
        "verificador válido, mas não existem na Receita Federal; por isso a consulta à BrasilAPI retorna "
        "\"não encontrado\", o que o robô registra como aviso (modo padrão).")

    doc.add_heading("2. Requisitos do RPA cobertos pelos testes", 1)
    _tabela(doc, ["Requisito", "Onde é evidenciado"], [
        ["Integração com API", "BrasilAPI: feriados (data de pagamento) e CNPJ (situação cadastral) — CT03, CT12, CT15"],
        ["Leitura/geração de Excel", "Planilha de notas, capas de custos e relatório de execução (.xlsx) — todos os cenários"],
        ["Selenium", "Login e lançamento dos títulos no ERP, leitura do protocolo e prints — CT01 a CT05, CT13"],
        ["Logs de todos os passos", "Arquivo dados/logs/rpa_*.log com as 7 etapas — trechos em cada cenário"],
        ["Expressões regulares", "CNPJ, linha digitável, valores, datas, protocolo do ERP — CT06, CT10"],
        ["try/except (resiliência)", "PDF corrompido, API e ERP fora do ar — CT11, CT12, CT13"],
    ], [4.5, 11.5])

    doc.add_heading("3. Resumo dos cenários", 1)
    linhas, detalhes = [], []
    for c in cenarios:
        if c.execucao == "pytest":
            ok = "test_ct15_fornecedor_baixado_na_receita_bloqueia_pagamento PASSED" in saida_pytest
            linhas.append([c.codigo, c.titulo, c.tipo, c.esperado, "INSUCESSO (teste automatizado)",
                           "PASSOU" if ok else "FALHOU"])
            detalhes.append((c, [], ok))
            continue
        avaliacoes = _avaliacoes_do_cenario(c, resultados[c.execucao])
        situacao, obtido = _situacao(c, avaliacoes)
        linhas.append([c.codigo, c.titulo, c.tipo, c.esperado, obtido, situacao])
        detalhes.append((c, avaliacoes, situacao == "PASSOU"))
    _tabela(doc, ["ID", "Cenário", "Tipo", "Resultado esperado", "Obtido", "Situação"], linhas,
            [1.2, 3.6, 1.8, 5.6, 2.4, 1.6], cor_coluna=5)
    passou = sum(1 for l in linhas if l[5] == "PASSOU")
    doc.add_paragraph().add_run(f"{passou} de {len(linhas)} cenários passaram.").bold = True

    doc.add_heading("4. Evidências por cenário", 1)
    imagens = SAIDA / "imagens"
    imagens.mkdir(parents=True, exist_ok=True)
    for c, avaliacoes, ok in detalhes:
        doc.add_heading(f"{c.codigo} — {c.titulo} ({c.tipo})", 2)
        doc.add_paragraph().add_run("Entrada: ").bold = True
        doc.paragraphs[-1].add_run(c.entrada)
        doc.add_paragraph().add_run("Resultado esperado: ").bold = True
        doc.paragraphs[-1].add_run(c.esperado)

        if c.execucao == "pytest":
            doc.add_paragraph(
                "Como não é possível usar um fornecedor real com CNPJ baixado, a resposta da API foi simulada "
                "no teste automatizado test_ct15_fornecedor_baixado_na_receita_bloqueia_pagamento "
                "(tests/test_rpa.py): a API devolve situação BAIXADA e o robô bloqueia o pagamento.")
            _log(doc, [l for l in saida_pytest.splitlines() if "ct15" in l or "cnpj_nao_encontrado" in l])
            continue

        [pasta] = list((SAIDA / "cenarios").glob(f"*/{c.codigo} - *")) or [None]
        if pasta and c.codigo != "CT14":
            pdfs = sorted(pasta.glob("*.pdf"), key=lambda a: (a.name.lower().startswith("boleto"), a.name))
            for pdf in pdfs[:2]:
                mini = _miniatura_pdf(pdf, imagens / f"{c.codigo}_{pdf.stem}.png")
                if mini:
                    doc.add_paragraph(f"Documento recebido: {pdf.name}").runs[0].italic = True
                    doc.add_picture(str(mini), width=Cm(12))
                else:
                    doc.add_paragraph(f"Documento recebido: {pdf.name} (arquivo corrompido, não abre)").runs[0].italic = True

        doc.add_paragraph().add_run("Resultado no relatório de execução:").bold = True
        _tabela(doc, ["NF", "Status", "Motivo / avisos", "Pagamento", "Prioridade", "Protocolo ERP"], [
            [((a.lancamento.nota.numero if a.lancamento.nota else None) or "-")
             + (f" ({a.lancamento.parcela})" if a.lancamento.parcela else ""),
             a.status, "; ".join(a.motivos + a.avisos) or "-",
             a.data_pagamento.strftime("%d/%m/%Y") if a.data_pagamento else "-", a.prioridade, a.protocolo_erp or "-"]
            for a in avaliacoes
        ], [1.8, 2.2, 6.4, 2.1, 1.8, 2.5], cor_coluna=1)

        doc.add_paragraph().add_run("Trecho do log:").bold = True
        _log(doc, _trecho_do_log(c, resultados[c.execucao]))

        for print_ in resultados[c.execucao].prints:
            if c.numero_nf and f"_nf_{c.numero_nf}" in print_.name:
                doc.add_paragraph("Tela do ERP após o lançamento (Selenium):").runs[0].italic = True
                doc.add_picture(str(print_), width=Cm(15))
                break
        doc.add_paragraph().add_run(f"Situação: {'PASSOU' if ok else 'FALHOU'}").bold = True

    doc.add_heading("5. Testes automatizados (pytest)", 1)
    resumo = [l for l in saida_pytest.splitlines() if re.search(r"(passed|failed|PASSED|FAILED|SKIPPED)", l)]
    doc.add_paragraph(resumo[-1].strip("= ") if resumo else "-").runs[0].bold = True
    _log(doc, [l.replace("tests/", "") for l in resumo[:-1]][:80])

    doc.add_heading("6. Arquivos gerados nas execuções", 1)
    for nome, r in resultados.items():
        doc.add_paragraph(f"{nome}: log {r.arquivo_log} · relatório {r.relatorio or '-'}", style="List Bullet")

    destino = SAIDA / "Evidencias_de_Teste_RPA.docx"
    doc.save(destino)
    return destino


def main() -> None:
    import os

    os.chdir(RAIZ)
    hoje = date.today()
    cenarios, resultados, saida_pytest = rodar_cenarios(hoje)
    documento = montar_documento(cenarios, resultados, saida_pytest, hoje)
    print(f"\nDocumento de evidências: {documento}")
    if shutil.which("soffice"):
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(documento.parent),
                        str(documento)], capture_output=True, timeout=180)
        print(f"PDF: {documento.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
