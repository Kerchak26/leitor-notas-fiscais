"""Leitura de boletos bancários a partir do texto do PDF.

A fonte mais confiável é a linha digitável (47 dígitos): ela contém o fator de
vencimento e o valor, protegidos por dígitos verificadores. Os rótulos impressos
("Vencimento", "Valor do Documento") servem de reserva e de conferência.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .parser import (
    RE_CNPJ,
    RE_DATA,
    RE_VALOR,
    _apos_rotulo,
    _limpar_nome,
    formatar_cnpj,
    para_data,
    para_decimal,
)

# 5+5 5+6 5+6 1 14  (pontos e espaços opcionais)
RE_LINHA_DIGITAVEL = re.compile(
    r"(?<!\d)(\d{5})\.?(\d{5})\s*(\d{5})\.?(\d{6})\s*(\d{5})\.?(\d{6})\s*(\d)\s*(\d{14})(?!\d)"
)

PALAVRAS_BOLETO = [
    "LINHA DIGITAVEL", "NOSSO NUMERO", "FICHA DE COMPENSACAO", "RECIBO DO PAGADOR",
    "RECIBO DO SACADO", "AUTENTICACAO MECANICA", "LOCAL DE PAGAMENTO", "PAGAVEL EM",
    "BENEFICIARIO", "CEDENTE", "CARTEIRA", "BOLETO",
]
ROTULOS_VENCIMENTO = ["VENCIMENTO"]
ROTULOS_VALOR = ["VALOR DO DOCUMENTO", "(=) VALOR COBRADO", "VALOR COBRADO", "VALOR DO BOLETO", "VALOR"]
ROTULOS_BENEFICIARIO = ["BENEFICIARIO", "CEDENTE"]
ROTULOS_DOCUMENTO = ["NUMERO DO DOCUMENTO", "N° DO DOCUMENTO", "NUM. DOCUMENTO", "NO DOCUMENTO", "N. DOCUMENTO"]

BASE_FATOR = date(1997, 10, 7)
# O fator chegou a 9999 em 21/02/2025 e recomeçou em 1000 no dia seguinte
BASE_FATOR_NOVA = date(2025, 2, 22)


@dataclass
class Boleto:
    linha_digitavel: str | None = None
    vencimento: date | None = None
    valor: Decimal | None = None
    beneficiario: str | None = None
    cnpjs: list[str] = field(default_factory=list)
    numero_documento: str | None = None
    banco: str | None = None
    arquivo: str | None = None
    avisos: list[str] = field(default_factory=list)


# ------------------------------------------------------- linha digitável ----

def _mod10(numero: str) -> int:
    soma = 0
    for i, d in enumerate(reversed(numero)):
        p = int(d) * (2 if i % 2 == 0 else 1)
        soma += p // 10 + p % 10
    return (10 - soma % 10) % 10


def _mod11_barras(codigo43: str) -> int:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(d) * pesos[i % 8] for i, d in enumerate(reversed(codigo43)))
    dv = 11 - soma % 11
    return 1 if dv in (0, 10, 11) else dv


def linha_valida(linha: str) -> bool:
    """Confere os 3 dígitos dos campos (módulo 10) e o DV geral (módulo 11)."""
    if len(linha) != 47:
        return False
    campos = [(linha[0:9], linha[9]), (linha[10:20], linha[20]), (linha[21:31], linha[31])]
    if any(_mod10(c) != int(dv) for c, dv in campos):
        return False
    barras = codigo_de_barras(linha)
    return _mod11_barras(barras[:4] + barras[5:]) == int(barras[4])


def codigo_de_barras(linha: str) -> str:
    return linha[0:4] + linha[32] + linha[33:47] + linha[4:9] + linha[10:20] + linha[21:31]


def vencimento_pelo_fator(fator: int, referencia: date | None = None) -> date | None:
    """Converte o fator de vencimento em data, escolhendo o ciclo mais próximo da referência."""
    if fator == 0:
        return None
    referencia = referencia or date.today()
    candidatos = [BASE_FATOR + timedelta(days=fator)]
    if fator >= 1000:
        candidatos.append(BASE_FATOR_NOVA + timedelta(days=fator - 1000))
    return min(candidatos, key=lambda d: abs((d - referencia).days))


def formatar_linha(linha: str) -> str:
    l = linha
    return f"{l[0:5]}.{l[5:10]} {l[10:15]}.{l[15:21]} {l[21:26]}.{l[26:32]} {l[32]} {l[33:]}"


def extrair_linhas_digitaveis(texto: str) -> list[str]:
    encontradas: list[str] = []
    for m in RE_LINHA_DIGITAVEL.finditer(texto):
        linha = "".join(m.groups())
        if linha not in encontradas:  # a linha costuma aparecer duas vezes no boleto
            encontradas.append(linha)
    return encontradas


# ---------------------------------------------------------------- campos ----

def pontuacao_boleto(texto: str) -> int:
    pontos = sum(1 for p in PALAVRAS_BOLETO if p in texto)
    if any(linha_valida(l) for l in extrair_linhas_digitaveis(texto)):
        pontos += 5
    return pontos


def _beneficiario(texto: str) -> str | None:
    """Nome abaixo (ou ao lado) do rótulo Beneficiário/Cedente, na mesma coluna."""
    linhas = texto.splitlines()
    for rotulo in ROTULOS_BENEFICIARIO:
        for i, linha in enumerate(linhas):
            pos = linha.find(rotulo)
            if pos < 0:
                continue
            resto = linha[pos + len(rotulo):]
            # Texto a 3+ espaços do rótulo já é outra coluna, não o valor
            mesma_linha = "" if re.match(r"\s{3,}", resto) else re.split(r"\s{3,}", resto.strip(" :-"))[0]
            if mesma_linha and not mesma_linha.startswith(("(", "/")):
                candidato = mesma_linha
            elif i + 1 < len(linhas):
                abaixo = linhas[i + 1]
                inicio = max(0, pos - 2)
                candidato = re.split(r"\s{3,}", abaixo[inicio:].strip())[0]
            else:
                continue
            candidato = re.split(r"\bCNPJ\b|\bCPF\b", candidato)[0]
            nome = _limpar_nome(candidato)
            if nome and not any(r in nome for r in ("AGENCIA", "CODIGO", "ENDERECO")):
                return nome
    return None


def interpretar_boleto(texto: str, arquivo: str | None = None, referencia: date | None = None) -> list[Boleto]:
    """Interpreta o texto (já normalizado) de uma página de boleto.

    Retorna uma lista porque uma página pode trazer mais de um boleto (carnê).
    """
    linhas = extrair_linhas_digitaveis(texto)
    validas = [l for l in linhas if linha_valida(l)]
    venc_impresso = _apos_rotulo(texto, ROTULOS_VENCIMENTO, RE_DATA)
    valor_impresso = _apos_rotulo(texto, ROTULOS_VALOR, RE_VALOR)
    documento = _apos_rotulo(texto, ROTULOS_DOCUMENTO, re.compile(r"\b\d[\d/.-]*\b"))

    base = dict(
        beneficiario=_beneficiario(texto),
        cnpjs=sorted({formatar_cnpj(c) for c in RE_CNPJ.findall(texto)}),
        numero_documento=documento.group(0) if documento else None,
        arquivo=arquivo,
    )

    if not validas:
        boleto = Boleto(**base)
        boleto.vencimento = para_data(*venc_impresso.groups()) if venc_impresso else None
        boleto.valor = para_decimal(valor_impresso.group(1)) if valor_impresso else None
        if linhas:
            boleto.avisos.append("linha digitável com dígito verificador inválido")
        else:
            boleto.avisos.append("linha digitável não encontrada; dados lidos dos rótulos")
        return [boleto]

    boletos = []
    for linha in validas:
        b = Boleto(linha_digitavel=formatar_linha(linha), banco=linha[:3], **base)
        b.vencimento = vencimento_pelo_fator(int(linha[33:37]), referencia)
        valor = Decimal(int(linha[37:47])) / 100
        b.valor = valor if valor else None
        if len(validas) == 1:  # só dá para conferir com os rótulos se houver um boleto na página
            impresso = para_data(*venc_impresso.groups()) if venc_impresso else None
            if impresso and b.vencimento and impresso != b.vencimento:
                # A linha digitável tem dígito verificador; o rótulo pode ter sido lido errado
                b.avisos.append(f"vencimento impresso ({impresso:%d/%m/%Y}) difere da linha digitável")
            if b.vencimento is None:
                b.vencimento = impresso
            if b.valor is None and valor_impresso:
                b.valor = para_decimal(valor_impresso.group(1))
        boletos.append(b)
    return boletos
