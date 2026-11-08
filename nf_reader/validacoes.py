"""Validação e formatação de dados com expressões regulares.

Tudo o que o robô lê de um PDF passa por aqui antes de ser usado numa regra de
negócio ou digitado no ERP: o texto extraído pode vir com espaços, pontuação
diferente ou erros de leitura, e um valor mal formatado não pode seguir adiante.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

# CNPJ com ou sem pontuação: 99.001.122/0001-60 ou 99001122000160
RE_CNPJ = re.compile(r"^\s*(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})\s*$")
# Linha digitável de boleto bancário: 47 dígitos, com pontos e espaços opcionais
RE_LINHA_DIGITAVEL = re.compile(r"^\s*(\d{5})\.?(\d{5})\s*(\d{5})\.?(\d{6})\s*(\d{5})\.?(\d{6})\s*(\d)\s*(\d{14})\s*$")
# Valor em reais no formato brasileiro: 1.234,56 / 1234,56 / R$ 1.234,56
RE_MOEDA_BR = re.compile(r"^\s*(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*$")
# Data dd/mm/aaaa (aceita "-" e "." como separador)
RE_DATA_BR = re.compile(r"^\s*(\d{2})[/.-](\d{2})[/.-](\d{4})\s*$")
# Número de nota fiscal: até 9 dígitos, com zeros à esquerda e pontos opcionais (000.004.587)
RE_NUMERO_NF = re.compile(r"^\s*(\d{1,3}(?:\.\d{3})+|\d{1,12})\s*$")
# E-mail simples (para os filtros de remetente)
RE_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")


class DadoInvalido(ValueError):
    """Valor que não passou na validação; a mensagem explica o motivo."""


# ------------------------------------------------------------------- CNPJ ----

def _digitos_verificadores_cnpj(base12: str) -> str:
    def calcular(numeros: str, pesos: list[int]) -> str:
        resto = sum(int(n) * p for n, p in zip(numeros, pesos)) % 11
        return "0" if resto < 2 else str(11 - resto)

    d1 = calcular(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = calcular(base12 + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return d1 + d2


def validar_cnpj(cnpj: str | None) -> str:
    """Retorna o CNPJ só com dígitos ou levanta DadoInvalido."""
    m = RE_CNPJ.match(cnpj or "")
    if not m:
        raise DadoInvalido(f"CNPJ em formato inválido: {cnpj!r}")
    digitos = "".join(m.groups())
    if re.fullmatch(r"(\d)\1{13}", digitos):  # 00000000000000, 11111111111111...
        raise DadoInvalido(f"CNPJ com todos os dígitos iguais: {cnpj}")
    if _digitos_verificadores_cnpj(digitos[:12]) != digitos[12:]:
        raise DadoInvalido(f"CNPJ com dígito verificador inválido: {cnpj}")
    return digitos


def formatar_cnpj(cnpj: str) -> str:
    """99001122000160 -> 99.001.122/0001-60"""
    return re.sub(r"^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$", r"\1.\2.\3/\4-\5", re.sub(r"\D", "", cnpj))


def cnpj_valido(cnpj: str | None) -> bool:
    try:
        validar_cnpj(cnpj)
        return True
    except DadoInvalido:
        return False


# -------------------------------------------------------- linha digitável ----

def validar_linha_digitavel(linha: str | None) -> str:
    """Retorna os 47 dígitos da linha digitável ou levanta DadoInvalido."""
    from .boleto import linha_valida  # dígitos verificadores (módulos 10 e 11)

    m = RE_LINHA_DIGITAVEL.match(linha or "")
    if not m:
        raise DadoInvalido(f"linha digitável em formato inválido: {linha!r}")
    digitos = "".join(m.groups())
    if not linha_valida(digitos):
        raise DadoInvalido("linha digitável com dígito verificador inválido")
    return digitos


def formatar_linha_digitavel(linha: str) -> str:
    d = re.sub(r"\D", "", linha)
    return re.sub(r"^(\d{5})(\d{5})(\d{5})(\d{6})(\d{5})(\d{6})(\d)(\d{14})$", r"\1.\2 \3.\4 \5.\6 \7 \8", d)


# ------------------------------------------------------------ valor e data ----

def moeda_para_decimal(texto: str) -> Decimal:
    """'R$ 1.234,56' -> Decimal('1234.56')"""
    m = RE_MOEDA_BR.match(texto or "")
    if not m:
        raise DadoInvalido(f"valor em formato inválido: {texto!r}")
    try:
        return Decimal(f"{m.group(1).replace('.', '')}.{m.group(2)}")
    except InvalidOperation as erro:
        raise DadoInvalido(f"valor inválido: {texto!r}") from erro


def formatar_moeda(valor: Decimal | float | None) -> str:
    """Decimal('1234.5') -> '1.234,50' (formato digitado no ERP)"""
    if valor is None:
        return ""
    inteiro, centavos = f"{Decimal(str(valor)):.2f}".split(".")
    inteiro = re.sub(r"(\d)(?=(\d{3})+$)", r"\1.", inteiro)  # separador de milhar
    return f"{inteiro},{centavos}"


def texto_para_data(texto: str) -> date:
    m = RE_DATA_BR.match(texto or "")
    if not m:
        raise DadoInvalido(f"data em formato inválido: {texto!r}")
    dia, mes, ano = map(int, m.groups())
    try:
        return date(ano, mes, dia)
    except ValueError as erro:
        raise DadoInvalido(f"data inexistente: {texto}") from erro


def formatar_data(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def normalizar_numero_nf(numero: str | None) -> str:
    """'000.004.587' -> '4587'"""
    m = RE_NUMERO_NF.match(numero or "")
    if not m:
        raise DadoInvalido(f"número de nota inválido: {numero!r}")
    return m.group(1).replace(".", "").lstrip("0") or "0"


def email_valido(endereco: str) -> bool:
    return bool(RE_EMAIL.match(endereco or ""))
