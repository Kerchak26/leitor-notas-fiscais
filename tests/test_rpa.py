import os
import shutil
from datetime import date, timedelta
from decimal import Decimal

import pytest
import requests
from openpyxl import load_workbook

from nf_reader import api_brasil
from nf_reader.api_brasil import ApiIndisponivel, BrasilAPI, SituacaoCnpj
from nf_reader.processamento import processar_documentos
from nf_reader.regras import INSUCESSO, SUCESSO, avaliar
from nf_reader.rpa import OpcoesRPA, executar
from nf_reader.validacoes import (
    DadoInvalido,
    formatar_cnpj,
    formatar_linha_digitavel,
    formatar_moeda,
    moeda_para_decimal,
    normalizar_numero_nf,
    texto_para_data,
    validar_cnpj,
)
from tests import cenarios_rpa

HOJE = date(2026, 11, 9)  # segunda-feira


class ApiFalsa:
    """Imita a BrasilAPI sem acessar a internet."""

    def __init__(self, situacoes=None, fora_do_ar=False, feriados=()):
        self.situacoes = situacoes or {}
        self.fora_do_ar = fora_do_ar
        self.feriados = set(feriados)

    def _checar(self):
        if self.fora_do_ar:
            raise ApiIndisponivel("simulação de API fora do ar")

    def proximo_dia_util(self, dia):
        self._checar()
        motivo = None
        while dia.weekday() >= 5 or dia in self.feriados:
            motivo = motivo or ("feriado" if dia in self.feriados else "fim de semana")
            dia += timedelta(days=1)
        return dia, motivo

    def dias_uteis_entre(self, inicio, fim):
        return sum(1 for i in range(1, (fim - inicio).days + 1)
                   if (inicio + timedelta(days=i)).weekday() < 5)

    def consultar_cnpj(self, cnpj):
        self._checar()
        situacao = self.situacoes.get(cnpj)
        return SituacaoCnpj(cnpj, encontrado=situacao is not None, situacao=situacao)


# ------------------------------------------------------------- validações ----

@pytest.mark.parametrize("cnpj", ["99.001.122/0001-60", "99001122000160", " 99.001.122/0001-60 "])
def test_cnpj_valido_em_varios_formatos(cnpj):
    assert validar_cnpj(cnpj) == "99001122000160"


@pytest.mark.parametrize("cnpj, motivo", [
    ("99.001.122/0001-99", "dígito verificador"),
    ("11.111.111/1111-11", "todos os dígitos iguais"),
    ("99.001.122/0001", "formato"),
    (None, "formato"),
])
def test_cnpj_invalido(cnpj, motivo):
    with pytest.raises(DadoInvalido, match=motivo):
        validar_cnpj(cnpj)


def test_formatacoes():
    assert formatar_cnpj("99001122000160") == "99.001.122/0001-60"
    assert formatar_moeda(Decimal("1234567.5")) == "1.234.567,50"
    assert moeda_para_decimal("R$ 3.250,90") == Decimal("3250.90")
    assert normalizar_numero_nf("000.004.587") == "4587"
    assert texto_para_data("14-10-2026") == date(2026, 10, 14)
    assert formatar_linha_digitavel("2379" + "1" * 43).count(" ") == 4
    with pytest.raises(DadoInvalido):
        texto_para_data("31/02/2026")
    with pytest.raises(DadoInvalido):
        moeda_para_decimal("3250.90")


# ------------------------------------------------------------------ regras ----

@pytest.fixture(scope="module")
def cenarios(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("cenarios")
    lista = cenarios_rpa.gerar(pasta, hoje=HOJE)
    return pasta, {c.codigo: c for c in lista}


def _lancamentos(pasta_cenarios, codigo):
    [pasta] = list(pasta_cenarios.glob(f"*/{codigo} - *"))
    return processar_documentos([(str(a), a) for a in sorted(pasta.glob("*.pdf"))])


@pytest.mark.parametrize("codigo, status, trecho", [
    ("CT01", SUCESSO, None),
    ("CT04", SUCESSO, None),
    ("CT06", INSUCESSO, "CNPJ do emitente inválido"),
    ("CT07", INSUCESSO, "difere do valor da nota"),
    ("CT08", INSUCESSO, "boleto vencido"),
    ("CT09", INSUCESSO, "nota sem boleto"),
    ("CT10", INSUCESSO, "dígito verificador"),
    ("CT11", INSUCESSO, "PDF ilegível"),
])
def test_regras_por_cenario(cenarios, codigo, status, trecho):
    pasta, _ = cenarios
    for lanc in _lancamentos(pasta, codigo):
        av = avaliar(lanc, ApiFalsa(), hoje=HOJE)
        assert av.status == status, av.motivos
        if trecho:
            assert any(trecho in m for m in av.motivos), av.motivos


def test_parcelas_e_fim_de_semana(cenarios):
    pasta, _ = cenarios
    parcelas = [avaliar(l, ApiFalsa(), hoje=HOJE) for l in _lancamentos(pasta, "CT02")]
    assert [a.status for a in parcelas] == [SUCESSO, SUCESSO]
    [sabado] = [avaliar(l, ApiFalsa(), hoje=HOJE) for l in _lancamentos(pasta, "CT03")]
    assert sabado.data_pagamento.weekday() == 0                      # segunda-feira
    assert any("pagamento em" in a for a in sabado.avisos)


def test_urgente(cenarios):
    pasta, _ = cenarios
    [av] = [avaliar(l, ApiFalsa(), hoje=HOJE) for l in _lancamentos(pasta, "CT05")]
    assert av.prioridade == "URGENTE"


def test_feriado_adia_pagamento(cenarios):
    pasta, _ = cenarios
    [lanc] = _lancamentos(pasta, "CT01")
    feriado = lanc.vencimento
    av = avaliar(lanc, ApiFalsa(feriados=[feriado]), hoje=HOJE)
    assert av.data_pagamento > feriado and av.status == SUCESSO


def test_ct15_fornecedor_baixado_na_receita_bloqueia_pagamento(cenarios):
    pasta, _ = cenarios
    [lanc] = _lancamentos(pasta, "CT01")
    av = avaliar(lanc, ApiFalsa({"99.001.122/0001-60": "BAIXADA"}), hoje=HOJE)
    assert av.status == INSUCESSO
    assert av.situacao_receita == "BAIXADA"
    assert any("situação cadastral BAIXADA" in m for m in av.motivos)


def test_cnpj_nao_encontrado_avisa_ou_bloqueia(cenarios):
    pasta, _ = cenarios
    [lanc] = _lancamentos(pasta, "CT01")
    assert avaliar(lanc, ApiFalsa(), hoje=HOJE, modo_cnpj="avisar").status == SUCESSO
    [lanc] = _lancamentos(pasta, "CT01")
    assert avaliar(lanc, ApiFalsa(), hoje=HOJE, modo_cnpj="bloquear").status == INSUCESSO


def test_api_fora_do_ar_nao_impede_o_lancamento(cenarios):
    pasta, _ = cenarios
    [lanc] = _lancamentos(pasta, "CT12")
    av = avaliar(lanc, ApiFalsa(fora_do_ar=True), hoje=HOJE)
    assert av.status == SUCESSO
    assert av.situacao_receita == "não verificada"
    assert av.data_pagamento is not None                            # calculada localmente


# ------------------------------------------------------- API: resiliência ----

def test_api_tenta_de_novo_e_desiste(monkeypatch):
    chamadas = []

    def get_falho(self, url, timeout):
        chamadas.append(url)
        raise requests.ConnectionError("sem rede")

    monkeypatch.setattr(requests.Session, "get", get_falho)
    monkeypatch.setattr(api_brasil.time, "sleep", lambda s: None)
    with pytest.raises(ApiIndisponivel):
        BrasilAPI(tentativas=3).feriados(2026)
    assert len(chamadas) == 3


def test_api_usa_cache(monkeypatch, tmp_path):
    class Resposta:
        status_code = 200

        def json(self):
            return [{"date": "2026-11-20", "name": "Dia da consciência negra"}]

        def raise_for_status(self):
            pass

    chamadas = []
    monkeypatch.setattr(requests.Session, "get", lambda self, url, timeout: chamadas.append(url) or Resposta())
    cache = tmp_path / "cache.json"
    assert BrasilAPI(arquivo_cache=cache).proximo_dia_util(date(2026, 11, 20))[0] == date(2026, 11, 23)
    BrasilAPI(arquivo_cache=cache).feriados(2026)                    # segunda instância: lê do cache
    assert len(chamadas) == 1


# ---------------------------------------------------------- de ponta a ponta ----

def test_rpa_completo_sem_erp(cenarios, tmp_path, monkeypatch):
    pasta, _ = cenarios
    monkeypatch.setattr("nf_reader.rpa.BrasilAPI", lambda *a, **k: ApiFalsa())
    resultado = executar(OpcoesRPA(origem="pasta", caminho=pasta / "principal", pasta_saida=tmp_path, hoje=HOJE))
    assert resultado.contar(SUCESSO) == 6       # CT01, CT02 (2 parcelas), CT03, CT04, CT05
    assert resultado.contar(INSUCESSO) == 6     # CT06 a CT11
    log = resultado.arquivo_log.read_text(encoding="utf-8")
    for etapa in range(1, 8):
        assert f"ETAPA {etapa}/7" in log
    relatorio = load_workbook(resultado.relatorio)
    assert relatorio["Execução"].max_row == 13
    assert relatorio["Resumo"]["B5"].value == 6


def _chrome_disponivel() -> bool:
    return bool(os.getenv("CHROMEDRIVER")) or shutil.which("chromedriver") is not None


@pytest.mark.skipif(not _chrome_disponivel(), reason="Chrome/chromedriver não disponível")
def test_rpa_com_erp_e_reexecucao(cenarios, tmp_path, monkeypatch):
    pasta, _ = cenarios
    monkeypatch.setattr("nf_reader.rpa.BrasilAPI", lambda *a, **k: ApiFalsa())
    [ct01] = list((pasta / "principal").glob("CT01 - *"))
    opcoes = OpcoesRPA(origem="pasta", caminho=ct01, pasta_saida=tmp_path, usar_erp=True, hoje=HOJE)
    [av] = executar(opcoes).avaliacoes
    assert av.protocolo_erp and av.protocolo_erp.startswith("CP-")
    [av] = executar(opcoes).avaliacoes                               # CT14: não lança de novo
    assert av.status == "IGNORADO"


@pytest.mark.skipif(not _chrome_disponivel(), reason="Chrome/chromedriver não disponível")
def test_erp_fora_do_ar_vira_insucesso(cenarios, tmp_path, monkeypatch):
    pasta, _ = cenarios
    monkeypatch.setattr("nf_reader.rpa.BrasilAPI", lambda *a, **k: ApiFalsa())
    [ct13] = list(pasta.glob("erp_fora/CT13 - *"))
    resultado = executar(OpcoesRPA(origem="pasta", caminho=ct13, pasta_saida=tmp_path, usar_erp=True,
                                   url_erp="http://127.0.0.1:9/erp", hoje=HOJE))
    [av] = resultado.avaliacoes
    assert av.status == INSUCESSO and "ERP" in av.motivos[0]
    assert resultado.relatorio.exists()


@pytest.mark.skipif(not _chrome_disponivel(), reason="Chrome/chromedriver não disponível")
def test_erp_por_http(cenarios, tmp_path, monkeypatch):
    """O robô também funciona com o ERP servido por http (python -m nf_reader erp)."""
    from nf_reader.servidor_erp import iniciar_em_segundo_plano

    pasta, _ = cenarios
    monkeypatch.setattr("nf_reader.rpa.BrasilAPI", lambda *a, **k: ApiFalsa())
    servidor, url = iniciar_em_segundo_plano()
    try:
        [ct01] = list((pasta / "principal").glob("CT01 - *"))
        [av] = executar(OpcoesRPA(origem="pasta", caminho=ct01, pasta_saida=tmp_path, usar_erp=True,
                                  url_erp=url, hoje=HOJE)).avaliacoes
        assert av.protocolo_erp and av.protocolo_erp.startswith("CP-")
    finally:
        servidor.shutdown()
