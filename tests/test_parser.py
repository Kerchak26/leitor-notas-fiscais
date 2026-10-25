from datetime import date
from decimal import Decimal

import pytest

from nf_reader import exporter
from nf_reader.parser import interpretar_nota
from nf_reader.processamento import Lancamento
from nf_reader.pdf_extractor import extrair_texto
from tests.gerar_exemplos import gerar_danfe, gerar_nfse


@pytest.fixture
def danfe(tmp_path):
    caminho = tmp_path / "danfe.pdf"
    gerar_danfe(caminho)
    return interpretar_nota(extrair_texto(caminho), arquivo=str(caminho))


@pytest.fixture
def nfse(tmp_path):
    caminho = tmp_path / "nfse.pdf"
    gerar_nfse(caminho)
    return interpretar_nota(extrair_texto(caminho), arquivo=str(caminho))


def test_danfe(danfe):
    assert danfe.numero == "4587"
    assert danfe.serie == "1"
    assert danfe.emitente == "DISTRIBUIDORA ALFA COMERCIO LTDA"
    assert danfe.cnpj_emitente == "12.345.678/0001-95"
    assert danfe.data_emissao == date(2026, 10, 14)
    assert danfe.data_vencimento == date(2026, 11, 13)
    assert danfe.valor_total == Decimal("3250.90")
    assert len(danfe.chave_acesso) == 44


def test_nfse(nfse):
    assert nfse.numero == "2026000123"
    assert nfse.emitente == "TECH SOLUCOES EM SOFTWARE EIRELI"
    assert nfse.cnpj_emitente == "11.222.333/0001-81"
    assert nfse.data_emissao == date(2026, 10, 3)
    assert nfse.data_vencimento is None  # o vencimento vem do boleto
    assert nfse.valor_total == Decimal("12544.00")


def test_texto_vazio_gera_avisos():
    nota = interpretar_nota("")
    assert not nota.completa
    assert any("OCR" in a for a in nota.avisos)


@pytest.mark.parametrize("extensao", ["xlsx", "csv"])
def test_exportacao_sem_duplicar(tmp_path, danfe, nfse, extensao):
    lancamentos = [Lancamento(nota=danfe), Lancamento(nota=nfse)]
    saida = tmp_path / f"notas.{extensao}"
    assert exporter.salvar(lancamentos, saida) == 2
    assert exporter.salvar(lancamentos, saida) == 0
