"""Cenários de teste do RPA (sucesso e insucesso), com dados fictícios.

Cada cenário é uma pasta que faz o papel de um e-mail recebido. As datas são
calculadas a partir da data de referência, então os cenários funcionam em
qualquer dia (um boleto "a vencer" nunca fica vencido com o passar do tempo).

Os CNPJs são fictícios: têm dígito verificador válido, mas não existem na
Receita Federal (a BrasilAPI responde 404 para eles).

Uso: python tests/cenarios_rpa.py [pasta_destino]
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.gerar_exemplos import _escrever, linha_digitavel, pagina_boleto, pagina_danfe  # noqa: E402

# Fornecedores fictícios (CNPJ com DV válido e inexistente na Receita)
ALFA = ("DISTRIBUIDORA ALFA COMÉRCIO LTDA", "99001122000160")
BETA = ("BETA LINK TELECOMUNICAÇÕES S.A.", "99004455000142")
GAMA = ("GAMA SERVIÇOS DE LIMPEZA LTDA", "99007788000125")
CNPJ_DV_ERRADO = "99001122000199"          # mesmo CNPJ da Alfa com dígitos verificadores trocados


@dataclass
class Cenario:
    codigo: str
    titulo: str
    tipo: str              # "Sucesso" ou "Insucesso"
    entrada: str
    esperado: str
    numero_nf: str | None  # para achar o resultado no relatório e no log
    execucao: str = "principal"


def _util(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _proximo_sabado(d: date) -> date:
    return d + timedelta(days=(5 - d.weekday()) % 7 or 7)


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _danfe(numero: int, fornecedor: tuple[str, str], valor: str, vencimento: date) -> list:
    nome, cnpj = fornecedor
    return pagina_danfe(numero=numero, cnpj=cnpj, empresa=nome, valor=valor, vencimento=_fmt(vencimento))


def _boleto(numero: int, fornecedor: tuple[str, str], valor: str, vencimento: date,
            livre: str, documento: str | None = None, adulterar: bool = False) -> list:
    nome, cnpj = fornecedor
    cnpj_fmt = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    pagina = pagina_boleto(nome, cnpj_fmt, documento or f"{numero}/1", vencimento, valor, livre)
    if adulterar:  # troca um dígito do campo livre: os dígitos verificadores deixam de bater
        centavos = int(valor.replace(".", "").replace(",", ""))
        original = linha_digitavel(vencimento, centavos, livre)
        trocado = original[:14] + ("7" if original[14] != "7" else "8") + original[15:]
        pagina = [[(x, trocado if t == original else t) for x, t in l] if isinstance(l, list) else l
                  for l in pagina]
    return pagina


def gerar(destino: Path, hoje: date | None = None) -> list[Cenario]:
    hoje = hoje or date.today()
    destino.mkdir(parents=True, exist_ok=True)
    cenarios: list[Cenario] = []

    def pasta(cenario: Cenario) -> Path:
        cenarios.append(cenario)
        p = destino / cenario.execucao / f"{cenario.codigo} - {cenario.titulo}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---------------------------------------------------------------- sucesso ----
    v = _util(hoje + timedelta(days=15))
    p = pasta(Cenario("CT01", "Nota e boleto em PDFs separados", "Sucesso",
                      f"NF 7001 + boleto de R$ 3.250,90, vencimento {_fmt(v)}",
                      "SUCESSO: título lançado no ERP com protocolo", "7001"))
    _escrever(p / "NF 7001.pdf", _danfe(7001, ALFA, "3.250,90", v))
    _escrever(p / "Boleto NF 7001.pdf", _boleto(7001, ALFA, "3.250,90", v, "1000100000000000000700101"))

    v1, v2 = _util(hoje + timedelta(days=20)), _util(hoje + timedelta(days=50))
    p = pasta(Cenario("CT02", "Nota parcelada em dois boletos", "Sucesso",
                      f"NF 7002 de R$ 12.000,00 com 2 boletos de R$ 6.000,00 ({_fmt(v1)} e {_fmt(v2)})",
                      "SUCESSO: dois títulos (1/2 e 2/2) lançados no ERP", "7002"))
    _escrever(p / "NF 7002.pdf", _danfe(7002, BETA, "12.000,00", v1))
    _escrever(p / "Boletos NF 7002.pdf",
              _boleto(7002, BETA, "6.000,00", v1, "1000200000000000000700201", "7002/1"),
              _boleto(7002, BETA, "6.000,00", v2, "1000200000000000000700202", "7002/2"))

    sabado = _proximo_sabado(hoje + timedelta(days=7))
    p = pasta(Cenario("CT03", "Vencimento no fim de semana", "Sucesso",
                      f"NF 7003 com vencimento no sábado {_fmt(sabado)}",
                      f"SUCESSO com data de pagamento ajustada para {_fmt(sabado + timedelta(days=2))} (API de feriados)",
                      "7003"))
    _escrever(p / "NF 7003.pdf", _danfe(7003, GAMA, "980,00", sabado))
    _escrever(p / "Boleto 7003.pdf", _boleto(7003, GAMA, "980,00", sabado, "1000300000000000000700301"))

    v = _util(hoje + timedelta(days=25))
    p = pasta(Cenario("CT04", "Nota e boleto no mesmo PDF", "Sucesso",
                      "Um único PDF: página 1 = DANFE, página 2 = boleto",
                      "SUCESSO: páginas separadas e associadas corretamente", "7004"))
    _escrever(p / "NF e boleto 7004.pdf", _danfe(7004, ALFA, "1.540,00", v),
              _boleto(7004, ALFA, "1.540,00", v, "1000400000000000000700401"))

    v = _util(hoje + timedelta(days=1))
    p = pasta(Cenario("CT05", "Vencimento próximo (urgente)", "Sucesso",
                      f"NF 7005 vencendo em {_fmt(v)} (até 3 dias úteis)",
                      "SUCESSO com prioridade URGENTE no ERP", "7005"))
    _escrever(p / "NF 7005.pdf", _danfe(7005, GAMA, "450,00", v))
    _escrever(p / "Boleto 7005.pdf", _boleto(7005, GAMA, "450,00", v, "1000500000000000000700501"))

    # -------------------------------------------------------------- insucesso ----
    v = _util(hoje + timedelta(days=15))
    p = pasta(Cenario("CT06", "CNPJ do emitente inválido", "Insucesso",
                      "NF 7006 com CNPJ 99.001.122/0001-99 (dígitos verificadores errados)",
                      "INSUCESSO: CNPJ reprovado na validação (regex + dígito verificador)", "7006"))
    _escrever(p / "NF 7006.pdf", _danfe(7006, (ALFA[0], CNPJ_DV_ERRADO), "2.100,00", v))
    _escrever(p / "Boleto 7006.pdf", _boleto(7006, ALFA, "2.100,00", v, "1000600000000000000700601"))

    p = pasta(Cenario("CT07", "Valor do boleto diferente da nota", "Insucesso",
                      "NF 7007 de R$ 3.250,90 com boleto de R$ 3.200,00",
                      "INSUCESSO: divergência de valor entre nota e boleto", "7007"))
    _escrever(p / "NF 7007.pdf", _danfe(7007, BETA, "3.250,90", v))
    _escrever(p / "Boleto 7007.pdf", _boleto(7007, BETA, "3.200,00", v, "1000700000000000000700701"))

    vencido = hoje - timedelta(days=5)
    p = pasta(Cenario("CT08", "Boleto vencido", "Insucesso",
                      f"NF 7008 com boleto vencido em {_fmt(vencido)}",
                      "INSUCESSO: boleto vencido, solicitar boleto atualizado", "7008"))
    _escrever(p / "NF 7008.pdf", _danfe(7008, GAMA, "720,00", vencido))
    _escrever(p / "Boleto 7008.pdf", _boleto(7008, GAMA, "720,00", vencido, "1000800000000000000700801"))

    p = pasta(Cenario("CT09", "Nota sem boleto", "Insucesso",
                      "E-mail só com a NF 7009, sem boleto",
                      "INSUCESSO: nota sem boleto, solicitar ao fornecedor", "7009"))
    _escrever(p / "NF 7009.pdf", _danfe(7009, ALFA, "1.800,00", v))

    p = pasta(Cenario("CT10", "Linha digitável adulterada", "Insucesso",
                      "NF 7010 com boleto cuja linha digitável tem um dígito alterado",
                      "INSUCESSO: dígito verificador da linha digitável não confere", "7010"))
    _escrever(p / "NF 7010.pdf", _danfe(7010, BETA, "990,00", v))
    _escrever(p / "Boleto 7010.pdf", _boleto(7010, BETA, "990,00", v, "1001000000000000000701001", adulterar=True))

    p = pasta(Cenario("CT11", "PDF corrompido", "Insucesso",
                      "E-mail com um arquivo .pdf ilegível (corrompido)",
                      "INSUCESSO registrado e o robô continua com os demais e-mails", None))
    (p / "NF corrompida.pdf").write_bytes(b"%PDF-1.4\n%%%% arquivo truncado na transmissao \x00\x01\x02")

    # ------------------------------------------- resiliência (execuções à parte) ----
    v = _util(hoje + timedelta(days=15))
    p = pasta(Cenario("CT12", "BrasilAPI fora do ar", "Sucesso",
                      "NF 7012 válida, executada com a API inacessível",
                      "SUCESSO com aviso: 3 tentativas registradas no log, robô segue sem a API",
                      "7012", execucao="api_fora"))
    _escrever(p / "NF 7012.pdf", _danfe(7012, ALFA, "640,00", v))
    _escrever(p / "Boleto 7012.pdf", _boleto(7012, ALFA, "640,00", v, "1001200000000000000701201"))

    p = pasta(Cenario("CT13", "ERP fora do ar", "Insucesso",
                      "NF 7013 válida, executada com o ERP inacessível",
                      "INSUCESSO no lançamento, erro registrado no log e relatório gerado",
                      "7013", execucao="erp_fora"))
    _escrever(p / "NF 7013.pdf", _danfe(7013, GAMA, "310,00", v))
    _escrever(p / "Boleto 7013.pdf", _boleto(7013, GAMA, "310,00", v, "1001300000000000000701301"))

    cenarios.append(Cenario("CT14", "Mesmo e-mail processado duas vezes", "Insucesso",
                            "Reexecução do CT01, que já foi lançado no ERP",
                            "IGNORADO: não lança em duplicidade (informa o protocolo anterior)",
                            "7001", execucao="reexecucao"))
    cenarios.append(Cenario("CT15", "Fornecedor com CNPJ baixado na Receita", "Insucesso",
                            "Resposta da API de CNPJ com situação BAIXADA (teste automatizado)",
                            "INSUCESSO: pagamento bloqueado pela situação cadastral", None,
                            execucao="pytest"))
    return cenarios


if __name__ == "__main__":
    destino = Path(sys.argv[1] if len(sys.argv) > 1 else "dados/cenarios")
    for c in gerar(destino):
        print(f"{c.codigo} [{c.tipo:9}] {c.titulo}")
    print(f"Cenários gerados em {destino}/")
