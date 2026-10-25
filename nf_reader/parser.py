"""Extração dos campos de uma nota fiscal a partir do texto do PDF.

Funciona com DANFE (NF-e) e com a maioria dos layouts de NFS-e. A estratégia é:
1. Procurar a chave de acesso (44 dígitos), que já contém número, série e CNPJ
   do emitente de forma confiável.
2. Procurar rótulos conhecidos ("VALOR TOTAL DA NOTA", "DATA DA EMISSÃO"...) e
   capturar o primeiro valor no formato esperado logo depois deles.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

RE_DATA = re.compile(r"\b(\d{2})[/.-](\d{2})[/.-](\d{4})\b")
RE_VALOR = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})(?![\d,])")
RE_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
RE_CHAVE = re.compile(r"((?:\d[\s.]?){43}\d)")

# Rótulos em ordem de prioridade (texto já normalizado: maiúsculo e sem acento)
ROTULOS_VALOR_TOTAL = [
    "VALOR TOTAL DA NOTA",
    "VALOR TOTAL DA NFS-E",
    "VALOR LIQUIDO DA NOTA",
    "VALOR LIQUIDO",
    "VALOR TOTAL DO SERVICO",
    "VALOR DOS SERVICOS",
    "VALOR TOTAL",
    "TOTAL DA NOTA",
]
ROTULOS_EMISSAO = ["DATA DA EMISSAO", "DATA DE EMISSAO", "DATA EMISSAO", "EMISSAO", "EMITIDA EM"]
ROTULOS_VENCIMENTO = ["DATA DE VENCIMENTO", "DATA VENCIMENTO", "VENCIMENTO", "VENC."]
ROTULOS_NUMERO = [
    "NUMERO DA NOTA",
    "NUMERO DA NFS-E",
    "NUMERO DA NFSE",
    "NOTA FISCAL N",
    "NFS-E N",
    "N.",
    "NO.",
    "N°",
    "NUMERO",
]
ROTULOS_EMITENTE = [
    "RAZAO SOCIAL DO EMITENTE",
    "NOME/RAZAO SOCIAL",
    "RAZAO SOCIAL",
    "PRESTADOR DE SERVICOS",
    "EMITENTE",
]


@dataclass
class NotaFiscal:
    numero: str | None = None
    serie: str | None = None
    emitente: str | None = None
    cnpj_emitente: str | None = None
    data_emissao: date | None = None
    data_vencimento: date | None = None
    valor_total: Decimal | None = None
    chave_acesso: str | None = None
    cnpj_destinatario: str | None = None
    # Texto do quadro destinatário/tomador (nome, endereço), usado para descobrir a unidade
    bloco_destinatario: str | None = None
    arquivo: str | None = None
    origem_email: str | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def completa(self) -> bool:
        return all([self.numero, self.emitente, self.valor_total is not None, self.data_emissao])

    def como_dict(self) -> dict:
        d = asdict(self)
        d["avisos"] = "; ".join(self.avisos)
        return d


def normalizar(texto: str) -> str:
    """Maiúsculas e sem acentos, preservando linhas e alinhamento."""
    texto = texto.replace("º", "°").replace("ª", "A")  # antes do NFKD, que viraria "o"
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    # Os espaços são mantidos: com layout=True eles indicam a coluna de cada valor
    linhas = [l.replace("\t", " ").rstrip() for l in sem_acento.upper().splitlines()]
    return "\n".join(l for l in linhas if l.strip())


def para_decimal(valor: str) -> Decimal | None:
    try:
        return Decimal(valor.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def para_data(dia: str, mes: str, ano: str) -> date | None:
    try:
        return date(int(ano), int(mes), int(dia))
    except ValueError:
        return None


def formatar_cnpj(digitos: str) -> str:
    d = re.sub(r"\D", "", digitos)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else digitos


def _apos_rotulo(texto: str, rotulos: list[str], padrao: re.Pattern, linhas_abaixo: int = 2):
    """Valor que acompanha um rótulo, na mesma linha ou nas linhas de baixo.

    Em layouts tabulares (DANFE) o rótulo fica numa linha e os valores na de
    baixo, lado a lado; nesse caso escolhe o valor alinhado com a coluna do rótulo.
    """
    linhas = texto.splitlines()
    for rotulo in rotulos:
        for i, linha in enumerate(linhas):
            for m in re.finditer(re.escape(rotulo), linha):
                achado = padrao.search(linha, m.end())
                # Na mesma linha, aceita só se não houver outro rótulo no meio
                if achado and not re.search(r"[A-Z]{3,}", linha[m.end():achado.start()].replace("R$", "")):
                    return achado
                for abaixo in linhas[i + 1: i + 1 + linhas_abaixo]:
                    candidatos = list(padrao.finditer(abaixo))
                    if candidatos:
                        return min(candidatos, key=lambda c: _distancia(c.span(), m.span()))
    return None


def _distancia(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Distância horizontal entre dois intervalos de colunas (0 se sobrepostos)."""
    return max(0, a[0] - b[1], b[0] - a[1])


# ---------------------------------------------------------------- campos ----

def extrair_chave(texto: str) -> str | None:
    for m in RE_CHAVE.finditer(texto):
        chave = re.sub(r"\D", "", m.group(1))
        if len(chave) == 44 and _chave_valida(chave):
            return chave
    return None


def _chave_valida(chave: str) -> bool:
    """Confere o dígito verificador (módulo 11) da chave de acesso."""
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(d) * pesos[i % 8] for i, d in enumerate(reversed(chave[:43])))
    resto = soma % 11
    dv = 0 if resto < 2 else 11 - resto
    return dv == int(chave[43])


def extrair_valor_total(texto: str) -> Decimal | None:
    m = _apos_rotulo(texto, ROTULOS_VALOR_TOTAL, RE_VALOR)
    if m:
        return para_decimal(m.group(1))
    # Fallback: o maior valor monetário do documento costuma ser o total
    valores = [para_decimal(v) for v in RE_VALOR.findall(texto)]
    valores = [v for v in valores if v is not None]
    return max(valores) if valores else None


def extrair_data(texto: str, rotulos: list[str]) -> date | None:
    m = _apos_rotulo(texto, rotulos, RE_DATA)
    return para_data(*m.groups()) if m else None


def extrair_numero(texto: str) -> str | None:
    padrao = re.compile(r"[:\s]*((?:\d{1,3}\.)+\d{3}|\d{1,12})\b")
    for rotulo in ROTULOS_NUMERO:
        for m in re.finditer(r"(?<![A-Z])" + re.escape(rotulo).replace(r"\ ", r"\s+"), texto):
            achado = padrao.match(texto, m.end())
            if achado:
                return achado.group(1).replace(".", "").lstrip("0") or "0"
    return None


def extrair_emitente(texto: str) -> str | None:
    # 1) Canhoto do DANFE: "RECEBEMOS DE <EMPRESA> OS PRODUTOS..."
    m = re.search(r"RECEBEMOS DE\s+(.+?)\s+OS\s+(?:PRODUTOS|SERVICOS)", texto, re.S)
    if m:
        return _limpar_nome(m.group(1))

    # 2) Rótulos de razão social: valor na mesma linha ou na seguinte
    linhas = texto.splitlines()
    for rotulo in ROTULOS_EMITENTE:
        for i, linha in enumerate(linhas):
            if rotulo in linha:
                resto = re.split(r"\s{3,}", linha.split(rotulo, 1)[1].strip(" :-"))[0]
                candidato = resto or (linhas[i + 1] if i + 1 < len(linhas) else "")
                nome = _limpar_nome(candidato)
                if nome and not _parece_rotulo(nome):
                    return nome

    # 3) Linha imediatamente acima do primeiro CNPJ
    for i, linha in enumerate(linhas):
        if RE_CNPJ.search(linha) and i > 0:
            nome = _limpar_nome(linhas[i - 1])
            if nome and not _parece_rotulo(nome):
                return nome
    return None


def _limpar_nome(nome: str) -> str | None:
    nome = re.sub(r"\s+", " ", RE_CNPJ.sub("", nome)).strip(" :-|")
    return nome or None


def _parece_rotulo(texto: str) -> bool:
    return any(p in texto for p in ("CNPJ", "INSCRICAO", "ENDERECO", "DANFE", "DOCUMENTO AUXILIAR"))


FIM_QUADRO_DESTINATARIO = ["FATURA", "DUPLICATA", "CALCULO DO IMPOSTO", "TRANSPORTADOR",
                           "DADOS DOS PRODUTOS", "DISCRIMINACAO", "DESCRICAO DOS SERVICOS"]


def extrair_destinatario(texto: str, cnpj_emitente: str | None) -> tuple[str | None, str | None]:
    """CNPJ e texto do quadro "Destinatário" (NF-e) ou "Tomador de Serviços" (NFS-e)."""
    linhas = texto.splitlines()
    for i, linha in enumerate(linhas):
        if "DESTINATARIO" in linha or "TOMADOR" in linha:
            bloco_linhas = [linha]
            for seguinte in linhas[i + 1: i + 10]:
                if any(q in seguinte for q in FIM_QUADRO_DESTINATARIO):
                    break
                bloco_linhas.append(seguinte)
            bloco = "\n".join(bloco_linhas)
            cnpjs = [formatar_cnpj(c) for c in RE_CNPJ.findall(bloco)]
            cnpjs = [c for c in cnpjs if c != cnpj_emitente]
            return (cnpjs[0] if cnpjs else None), bloco
    return None, None


# ------------------------------------------------------------- principal ----

def interpretar_nota(texto_pdf: str, arquivo: str | None = None) -> NotaFiscal:
    texto = normalizar(texto_pdf)
    nota = NotaFiscal(arquivo=arquivo)

    nota.chave_acesso = extrair_chave(texto)
    if nota.chave_acesso:
        c = nota.chave_acesso
        nota.cnpj_emitente = formatar_cnpj(c[6:20])
        nota.serie = c[22:25].lstrip("0") or "0"
        nota.numero = c[25:34].lstrip("0") or "0"

    nota.numero = nota.numero or extrair_numero(texto)
    nota.emitente = extrair_emitente(texto)
    if not nota.cnpj_emitente and (m := RE_CNPJ.search(texto)):
        nota.cnpj_emitente = formatar_cnpj(m.group(0))
    nota.data_emissao = extrair_data(texto, ROTULOS_EMISSAO)
    nota.data_vencimento = extrair_data(texto, ROTULOS_VENCIMENTO)
    nota.valor_total = extrair_valor_total(texto)
    nota.cnpj_destinatario, nota.bloco_destinatario = extrair_destinatario(texto, nota.cnpj_emitente)

    if not texto.strip():
        nota.avisos.append("PDF sem texto (provavelmente escaneado; requer OCR)")
    for campo in ("numero", "emitente", "data_emissao", "valor_total"):
        if getattr(nota, campo) in (None, ""):
            nota.avisos.append(f"campo '{campo}' não encontrado")
    return nota


def data_br(d: date | datetime | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""
