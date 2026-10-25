"""Separa notas e boletos dos PDFs de um e-mail e associa cada boleto à sua nota.

Um mesmo e-mail pode trazer a nota e o boleto em PDFs separados, ou os dois no
mesmo PDF (DANFE na página 1, boleto na página 2); por isso a classificação é
feita página a página.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .boleto import Boleto, interpretar_boleto, pontuacao_boleto
from .parser import NotaFiscal, extrair_chave, interpretar_nota, normalizar
from .pdf_extractor import extrair_paginas

log = logging.getLogger(__name__)

PALAVRAS_NOTA = ["DANFE", "NOTA FISCAL", "NF-E", "NFS-E", "DOCUMENTO AUXILIAR", "PRESTADOR DE SERVICOS"]


@dataclass
class Lancamento:
    """Uma linha da planilha: uma nota e (se houver) um dos seus boletos."""

    nota: NotaFiscal | None = None
    boleto: Boleto | None = None
    parcela: str | None = None
    origem_email: str | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def vencimento(self):
        if self.boleto and self.boleto.vencimento:
            return self.boleto.vencimento
        return self.nota.data_vencimento if self.nota else None

    @property
    def id(self) -> str:
        n, b = self.nota, self.boleto
        id_nota = (n.chave_acesso or f"{n.cnpj_emitente or ''}|{n.numero or ''}|{n.serie or ''}") if n else ""
        id_boleto = (b.linha_digitavel or f"{b.vencimento}|{b.valor}") if b else ""
        return f"{id_nota}#{id_boleto}"

    def como_dict(self) -> dict:
        n, b = self.nota, self.boleto
        avisos = (n.avisos if n else []) + (b.avisos if b else []) + self.avisos
        return {
            "numero": n.numero if n else None,
            "serie": n.serie if n else None,
            "emitente": (n.emitente if n else None) or (b.beneficiario if b else None),
            "cnpj_emitente": n.cnpj_emitente if n else None,
            "data_emissao": n.data_emissao if n else None,
            "valor_nota": n.valor_total if n else None,
            "vencimento": self.vencimento,
            "valor_boleto": b.valor if b else None,
            "parcela": self.parcela,
            "linha_digitavel": b.linha_digitavel if b else None,
            "beneficiario": b.beneficiario if b else None,
            "chave_acesso": n.chave_acesso if n else None,
            "arquivos": " + ".join(dict.fromkeys(x.arquivo for x in (n, b) if x and x.arquivo)),
            "origem_email": self.origem_email,
            "avisos": "; ".join(dict.fromkeys(avisos)),
            "id": self.id,
        }


# --------------------------------------------------------- classificação ----

def classificar_pagina(texto: str) -> str:
    """'boleto', 'nota' ou 'desconhecido' para o texto normalizado de uma página."""
    p_boleto = pontuacao_boleto(texto)
    p_nota = sum(1 for p in PALAVRAS_NOTA if p in texto) + (5 if extrair_chave(texto) else 0)
    if p_boleto >= 3 and p_boleto > p_nota:
        return "boleto"
    if p_nota >= 1:
        return "nota"
    return "desconhecido"


def ler_pdf(pdf: str | Path | bytes, nome: str) -> tuple[list[NotaFiscal], list[Boleto]]:
    notas: list[NotaFiscal] = []
    boletos: list[Boleto] = []
    paginas_nota: list[str] = []
    anterior = "nota"

    def fechar_nota():
        if paginas_nota:
            notas.append(interpretar_nota("\n".join(paginas_nota), arquivo=nome))
            paginas_nota.clear()

    for texto in (normalizar(p) for p in extrair_paginas(pdf)):
        tipo = classificar_pagina(texto)
        if tipo == "desconhecido":
            tipo = anterior  # página de continuação
        if tipo == "boleto":
            fechar_nota()
            boletos.extend(interpretar_boleto(texto, arquivo=nome))
        else:
            # Chave de acesso diferente na mesma sequência = começou outra nota
            chave = extrair_chave(texto)
            if paginas_nota and chave and chave != extrair_chave("\n".join(paginas_nota)):
                fechar_nota()
            paginas_nota.append(texto)
        anterior = tipo
    fechar_nota()

    if not notas and not boletos:
        nota = interpretar_nota("", arquivo=nome)
        notas.append(nota)
    log.debug("%s: %d nota(s), %d boleto(s)", nome, len(notas), len(boletos))
    return notas, boletos


# ------------------------------------------------------------ associação ----

def _pontuacao(nota: NotaFiscal, boleto: Boleto) -> int:
    pontos = 0
    if nota.cnpj_emitente and nota.cnpj_emitente in boleto.cnpjs:
        pontos += 3
    if nota.numero and boleto.numero_documento:
        partes = [p.lstrip("0") for p in re.split(r"\D+", boleto.numero_documento) if p]
        if nota.numero in partes:
            pontos += 3
    if nota.valor_total is not None and boleto.valor == nota.valor_total:
        pontos += 2
    if nota.data_vencimento and boleto.vencimento == nota.data_vencimento:  # duplicata do DANFE
        pontos += 2
    if nota.emitente and boleto.beneficiario:
        if nota.emitente.split()[0] == boleto.beneficiario.split()[0]:
            pontos += 1
    return pontos


def associar(notas: list[NotaFiscal], boletos: list[Boleto], origem: str | None = None) -> list[Lancamento]:
    boletos_por_nota: dict[int, list[Boleto]] = {i: [] for i in range(len(notas))}
    sem_nota: list[Boleto] = []
    incertos: set[int] = set()

    # Os pares mais evidentes primeiro, para que os ambíguos sejam decididos por último
    pontuados = [(b, [_pontuacao(n, b) for n in notas]) for b in boletos]
    pontuados.sort(key=lambda par: max(par[1], default=0), reverse=True)

    for boleto, pontos in pontuados:
        melhor = max(pontos, default=0)
        if melhor == 0 and len(notas) != 1:
            sem_nota.append(boleto)
            continue
        empatadas = [i for i, p in enumerate(pontos) if p == melhor]
        if len(empatadas) > 1:
            # Empate (ex.: mesmo fornecedor e mesmo valor): prefere a nota que ainda não tem boleto
            empatadas.sort(key=lambda i: len(boletos_por_nota[i]))
            incertos.add(id(boleto))
        boletos_por_nota[empatadas[0]].append(boleto)

    lancamentos: list[Lancamento] = []
    for i, nota in enumerate(notas):
        lista = sorted(boletos_por_nota[i], key=lambda b: (b.vencimento is None, b.vencimento))
        if not lista:
            lanc = Lancamento(nota=nota, origem_email=origem)
            lanc.avisos.append("boleto não encontrado" + ("; vencimento tirado da NF" if nota.data_vencimento else ""))
            lancamentos.append(lanc)
            continue
        for j, boleto in enumerate(lista, start=1):
            lanc = Lancamento(nota=nota, boleto=boleto, origem_email=origem,
                              parcela=f"{j}/{len(lista)}" if len(lista) > 1 else None)
            if id(boleto) in incertos:
                lanc.avisos.append("boleto associado por aproximação (notas parecidas no mesmo e-mail); confira")
            lancamentos.append(lanc)
        soma = sum((b.valor for b in lista if b.valor is not None), start=0)
        if nota.valor_total is not None and soma and soma != nota.valor_total:
            lancamentos[-1].avisos.append(
                f"soma dos boletos (R$ {soma:.2f}) difere do valor da nota (R$ {nota.valor_total:.2f})")

    for boleto in sem_nota:
        lanc = Lancamento(boleto=boleto, origem_email=origem)
        lanc.avisos.append("boleto sem nota fiscal correspondente")
        lancamentos.append(lanc)

    for lanc in lancamentos:
        if lanc.vencimento is None:
            lanc.avisos.append("sem data de vencimento")
    return lancamentos


def processar_documentos(pdfs: list[tuple[str, str | Path | bytes]], origem: str | None = None) -> list[Lancamento]:
    """Processa os PDFs de um mesmo e-mail (ou pasta) como um grupo."""
    notas: list[NotaFiscal] = []
    boletos: list[Boleto] = []
    for nome, conteudo in pdfs:
        n, b = ler_pdf(conteudo, nome)
        notas.extend(n)
        boletos.extend(b)
    return associar(notas, boletos, origem)
