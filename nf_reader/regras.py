"""Regras de negócio do contas a pagar.

Cada lançamento (nota + boleto) é avaliado e sai como SUCESSO (pode seguir para
o ERP) ou INSUCESSO (precisa de ação humana), sempre com o motivo. As regras não
param na primeira falha: o analista recebe a lista completa do que corrigir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .api_brasil import ApiIndisponivel, BrasilAPI
from .processamento import Lancamento
from .validacoes import (
    DadoInvalido,
    formatar_cnpj,
    formatar_data,
    formatar_moeda,
    normalizar_numero_nf,
    validar_cnpj,
    validar_linha_digitavel,
)

log = logging.getLogger(__name__)

SUCESSO, INSUCESSO = "SUCESSO", "INSUCESSO"
DIAS_UTEIS_URGENTE = 3


@dataclass
class Avaliacao:
    lancamento: Lancamento
    status: str = SUCESSO
    motivos: list[str] = field(default_factory=list)     # por que é INSUCESSO
    avisos: list[str] = field(default_factory=list)      # pontos de atenção que não impedem
    data_pagamento: date | None = None
    prioridade: str = "NORMAL"
    situacao_receita: str = "não consultada"
    protocolo_erp: str | None = None

    def falha(self, motivo: str) -> None:
        self.status = INSUCESSO
        self.motivos.append(motivo)
        log.warning("REGRA: %s -> INSUCESSO: %s", self.descricao, motivo)

    def aviso(self, texto: str) -> None:
        self.avisos.append(texto)
        log.info("REGRA: %s -> aviso: %s", self.descricao, texto)

    @property
    def descricao(self) -> str:
        n = self.lancamento.nota
        parcela = f" ({self.lancamento.parcela})" if self.lancamento.parcela else ""
        return f"NF {n.numero if n and n.numero else '?'}{parcela}"


def _dia_util_local(dia: date) -> tuple[date, str | None]:
    """Reserva para quando a API de feriados está fora: considera só fins de semana."""
    motivo = None
    while dia.weekday() >= 5:
        motivo = motivo or ("sábado" if dia.weekday() == 5 else "domingo")
        dia += timedelta(days=1)
    return dia, motivo


def avaliar(lanc: Lancamento, api: BrasilAPI | None, hoje: date | None = None,
            modo_cnpj: str = "avisar") -> Avaliacao:
    """Aplica todas as regras a um lançamento.

    modo_cnpj: o que fazer quando o CNPJ não é encontrado na Receita:
    "bloquear" (INSUCESSO), "avisar" (só aviso) ou "desligado" (não consulta).
    """
    hoje = hoje or date.today()
    av = Avaliacao(lanc)
    n, b = lanc.nota, lanc.boleto

    # R1 - todo boleto precisa de uma nota fiscal
    if n is None:
        av.falha("boleto sem nota fiscal correspondente no e-mail")
        return av

    # R2 - PDF legível e campos obrigatórios da nota
    if any(a.startswith("PDF sem texto") for a in n.avisos):
        av.falha(f"PDF ilegível (corrompido ou escaneado, sem texto): {n.arquivo}")
        return av
    if not n.numero:
        av.falha("número da nota não encontrado (PDF ilegível ou escaneado?)")
    else:
        try:
            n.numero = normalizar_numero_nf(n.numero)
        except DadoInvalido as erro:
            av.falha(str(erro))
    if n.valor_total is None or n.valor_total <= 0:
        av.falha("valor total da nota não encontrado")
    if not n.emitente:
        av.aviso("razão social do emitente não encontrada na nota")

    # R3 - CNPJ do emitente: formato e dígito verificador (regex + módulo 11)
    cnpj_ok = False
    try:
        n.cnpj_emitente = formatar_cnpj(validar_cnpj(n.cnpj_emitente))
        cnpj_ok = True
    except DadoInvalido as erro:
        av.falha(f"CNPJ do emitente inválido: {erro}")

    # R4 - boleto presente e com linha digitável válida
    if b is None:
        av.falha("nota sem boleto: solicitar o boleto ao fornecedor")
    else:
        if not b.linha_digitavel:
            motivo = next((a for a in b.avisos if "linha digitável" in a), "linha digitável não encontrada")
            av.falha(f"boleto inválido: {motivo}")
        else:
            try:
                validar_linha_digitavel(b.linha_digitavel)
            except DadoInvalido as erro:
                av.falha(f"boleto inválido: {erro}")

        # R5 - valor do boleto confere com a nota (parcelas: a soma é conferida no agrupamento)
        if lanc.parcela is None and b.valor is not None and n.valor_total is not None:
            if abs(b.valor - n.valor_total) > Decimal("0.01"):
                av.falha(f"valor do boleto (R$ {formatar_moeda(b.valor)}) difere do valor da nota "
                         f"(R$ {formatar_moeda(n.valor_total)})")
        if lanc.parcela and any(a.startswith("soma dos boletos") for a in lanc.avisos):
            av.falha(next(a for a in lanc.avisos if a.startswith("soma dos boletos")))
        if any("aproximação" in a for a in lanc.avisos):
            av.aviso("boleto associado à nota por aproximação; conferir")

    # R6 - vencimento
    vencimento = lanc.vencimento
    if vencimento is None:
        av.falha("data de vencimento não encontrada")
    elif vencimento < hoje:
        av.falha(f"boleto vencido em {formatar_data(vencimento)}: solicitar boleto atualizado")
    else:
        # R7 - data de pagamento no próximo dia útil (API de feriados)
        try:
            if api is None:
                raise ApiIndisponivel("API desativada")
            av.data_pagamento, ajuste = api.proximo_dia_util(vencimento)
            uteis = api.dias_uteis_entre(hoje, av.data_pagamento)
        except ApiIndisponivel as erro:
            log.warning("Feriados indisponíveis (%s); usando só fins de semana", erro)
            av.aviso("calendário de feriados indisponível: considerados só fins de semana")
            av.data_pagamento, ajuste = _dia_util_local(vencimento)
            uteis = sum(1 for i in range(1, (av.data_pagamento - hoje).days + 1)
                        if (hoje + timedelta(days=i)).weekday() < 5)
        if ajuste:
            av.aviso(f"vencimento em {formatar_data(vencimento)} ({ajuste}): pagamento em "
                     f"{formatar_data(av.data_pagamento)}")
        # R8 - prioridade
        if uteis <= DIAS_UTEIS_URGENTE:
            av.prioridade = "URGENTE"
            av.aviso(f"vence em {uteis} dia(s) útil(eis): prioridade URGENTE")

    # R9 - situação cadastral do fornecedor na Receita Federal (API de CNPJ)
    if cnpj_ok and api is not None and modo_cnpj != "desligado":
        try:
            situacao = api.consultar_cnpj(n.cnpj_emitente)
            if not situacao.encontrado:
                av.situacao_receita = "não encontrado"
                texto = f"CNPJ {n.cnpj_emitente} não encontrado na Receita Federal"
                av.falha(texto) if modo_cnpj == "bloquear" else av.aviso(texto)
            else:
                av.situacao_receita = situacao.situacao or "?"
                if not situacao.ativa:
                    av.falha(f"fornecedor com situação cadastral {situacao.situacao} na Receita: "
                             "pagamento bloqueado")
        except ApiIndisponivel as erro:
            av.situacao_receita = "não verificada"
            av.aviso(f"situação cadastral não verificada (API indisponível): {erro}")
        except DadoInvalido:
            pass  # já registrado na R3

    log.info("REGRA: %s -> %s%s", av.descricao, av.status,
             f" ({'; '.join(av.motivos)})" if av.motivos else "")
    return av
