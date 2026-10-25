from datetime import date
from decimal import Decimal
from email.message import EmailMessage

from nf_reader.boleto import interpretar_boleto, linha_valida, vencimento_pelo_fator
from nf_reader.email_client import anexos_pdf
from nf_reader.parser import normalizar
from nf_reader.pdf_extractor import extrair_paginas
from nf_reader.processamento import processar_documentos
from tests.gerar_exemplos import (
    gerar_boleto_alfa,
    gerar_boletos_tech,
    gerar_danfe,
    gerar_nfse,
    gerar_nota_e_boleto_mesmo_pdf,
    linha_digitavel,
    pagina_boleto,
    pagina_danfe,
    _escrever,
)


def _pdf(tmp_path, gerador, nome):
    caminho = gerador(tmp_path / nome)
    return str(caminho), caminho.read_bytes()


# ----------------------------------------------------------- boleto puro ----

def test_fator_de_vencimento_nos_dois_ciclos():
    # Ciclo antigo (base 07/10/1997) e novo (fator 1000 = 22/02/2025)
    assert vencimento_pelo_fator(9999, referencia=date(2025, 1, 1)) == date(2025, 2, 21)
    assert vencimento_pelo_fator(1000, referencia=date(2026, 10, 25)) == date(2025, 2, 22)
    assert vencimento_pelo_fator(1000, referencia=date(2000, 7, 1)) == date(2000, 7, 3)
    assert vencimento_pelo_fator(0) is None


def test_linha_digitavel_valida_e_invalida():
    linha = linha_digitavel(date(2026, 11, 13), 325090, "1234509000000045870012345")
    digitos = "".join(c for c in linha if c.isdigit())
    assert linha_valida(digitos)
    adulterada = digitos[:40] + ("1" if digitos[40] != "1" else "2") + digitos[41:]
    assert not linha_valida(adulterada)


def test_le_boleto(tmp_path):
    caminho = gerar_boleto_alfa(tmp_path / "boleto.pdf")
    [boleto] = interpretar_boleto(normalizar(extrair_paginas(caminho)[0]))
    assert boleto.vencimento == date(2026, 11, 13)
    assert boleto.valor == Decimal("3250.90")
    assert boleto.beneficiario == "DISTRIBUIDORA ALFA COMERCIO LTDA"
    assert "12.345.678/0001-95" in boleto.cnpjs
    assert boleto.banco == "237"
    assert not boleto.avisos


# ------------------------------------------------- nota + boleto juntos ----

def test_nota_e_boleto_em_pdfs_separados(tmp_path):
    [lanc] = processar_documentos([_pdf(tmp_path, gerar_danfe, "nf.pdf"),
                                   _pdf(tmp_path, gerar_boleto_alfa, "boleto.pdf")])
    d = lanc.como_dict()
    assert d["numero"] == "4587"
    assert d["vencimento"] == date(2026, 11, 13)
    assert d["valor_boleto"] == d["valor_nota"] == Decimal("3250.90")
    assert d["avisos"] == ""


def test_nota_e_boleto_no_mesmo_pdf(tmp_path):
    [lanc] = processar_documentos([_pdf(tmp_path, gerar_nota_e_boleto_mesmo_pdf, "tudo.pdf")])
    assert lanc.nota.numero == "4587"
    assert lanc.boleto.vencimento == date(2026, 11, 13)


def test_boleto_parcelado(tmp_path):
    lancs = processar_documentos([_pdf(tmp_path, gerar_nfse, "nfse.pdf"),
                                  _pdf(tmp_path, gerar_boletos_tech, "boletos.pdf")])
    assert [l.parcela for l in lancs] == ["1/2", "2/2"]
    assert [l.vencimento for l in lancs] == [date(2026, 11, 3), date(2026, 12, 3)]
    assert all(not l.avisos for l in lancs)  # 6.272 + 6.272 = 12.544, bate com a nota


def test_duas_notas_no_mesmo_email_sao_pareadas_pelo_cnpj(tmp_path):
    outra = _escrever(tmp_path / "nf_beta.pdf",
                      pagina_danfe(numero=990, cnpj="55666777000108", empresa="BETA INDUSTRIA SA", valor="800,00"))
    boleto_beta = _escrever(tmp_path / "boleto_beta.pdf",
                            pagina_boleto("BETA INDUSTRIA SA", "55.666.777/0001-08", "990",
                                          date(2026, 11, 20), "800,00"))
    lancs = processar_documentos([
        _pdf(tmp_path, gerar_boleto_alfa, "boleto_alfa.pdf"),
        (str(boleto_beta), boleto_beta.read_bytes()),
        _pdf(tmp_path, gerar_danfe, "nf_alfa.pdf"),
        (str(outra), outra.read_bytes()),
    ])
    pares = {l.nota.numero: l.vencimento for l in lancs}
    assert pares == {"4587": date(2026, 11, 13), "990": date(2026, 11, 20)}


def test_nota_sem_boleto_usa_vencimento_da_duplicata(tmp_path):
    [lanc] = processar_documentos([_pdf(tmp_path, gerar_danfe, "nf.pdf")])
    assert lanc.vencimento == date(2026, 11, 13)
    assert "boleto não encontrado" in lanc.como_dict()["avisos"]


# ---------------------------------------------------- e-mail encaminhado ----

def _email(assunto, remetente):
    msg = EmailMessage()
    msg["Subject"], msg["From"] = assunto, remetente
    msg.set_content("Segue para pagamento.")
    return msg


def test_email_encaminhado_com_anexos(tmp_path):
    msg = _email("ENC: NF 4587", "chefe@empresa.com.br")
    for nome, gerador in [("NF 4587.pdf", gerar_danfe), ("Boleto 4587.pdf", gerar_boleto_alfa)]:
        msg.add_attachment(gerador(tmp_path / nome).read_bytes(), maintype="application",
                           subtype="pdf", filename=nome)
    msg.add_attachment(b"<xml/>", maintype="application", subtype="xml", filename="NF 4587.xml")

    anexos = anexos_pdf(msg)
    assert [a.nome for a in anexos] == ["NF 4587.pdf", "Boleto 4587.pdf"]
    [lanc] = processar_documentos([(a.nome, a.conteudo) for a in anexos])
    assert lanc.vencimento == date(2026, 11, 13)


def test_email_encaminhado_como_anexo(tmp_path):
    """O chefe usou 'Encaminhar como anexo': os PDFs estão dentro de um .eml."""
    original = _email("NF-e 4587 - Distribuidora Alfa", "faturamento@alfa.com.br")
    original.add_attachment(gerar_nota_e_boleto_mesmo_pdf(tmp_path / "nf.pdf").read_bytes(),
                            maintype="application", subtype="pdf", filename="NF e boleto.pdf")
    encaminhado = _email("Fwd: NF-e 4587", "chefe@empresa.com.br")
    encaminhado.add_attachment(original)

    anexos = anexos_pdf(encaminhado)
    assert [a.nome for a in anexos] == ["NF e boleto.pdf"]
    [lanc] = processar_documentos([(a.nome, a.conteudo) for a in anexos])
    assert (lanc.nota.numero, lanc.vencimento) == ("4587", date(2026, 11, 13))


# ------------------------------- várias notas do mesmo fornecedor no e-mail ----

ALFA = ("DISTRIBUIDORA ALFA COMÉRCIO LTDA", "12.345.678/0001-95")
LIVRE_1, LIVRE_2 = "1111100000000000000000001", "2222200000000000000000002"


def _processar(tmp_path, documentos):
    return processar_documentos([(nome, _escrever(tmp_path / nome, *paginas).read_bytes())
                                 for nome, paginas in documentos])


def test_mesmo_fornecedor_diferenciado_pelo_valor(tmp_path):
    lancs = _processar(tmp_path, [
        ("b2.pdf", [pagina_boleto(*ALFA, "99887", date(2026, 11, 20), "1.100,00", LIVRE_2)]),
        ("b1.pdf", [pagina_boleto(*ALFA, "99886", date(2026, 11, 13), "3.250,90", LIVRE_1)]),
        ("nf1.pdf", [pagina_danfe(numero=4587, valor="3.250,90")]),
        ("nf2.pdf", [pagina_danfe(numero=4600, valor="1.100,00")]),
    ])
    assert {l.nota.numero: l.vencimento for l in lancs} == {"4587": date(2026, 11, 13), "4600": date(2026, 11, 20)}
    assert all(not l.avisos for l in lancs)


def test_varias_notas_e_boletos_no_mesmo_pdf(tmp_path):
    lancs = _processar(tmp_path, [("tudo.pdf", [
        pagina_danfe(numero=4587, valor="3.250,90"),
        pagina_boleto(*ALFA, "4587/1", date(2026, 11, 13), "3.250,90", LIVRE_1),
        pagina_danfe(numero=4600, valor="1.100,00"),
        pagina_boleto(*ALFA, "4600/1", date(2026, 11, 20), "1.100,00", LIVRE_2),
    ])])
    assert {l.nota.numero: l.vencimento for l in lancs} == {"4587": date(2026, 11, 13), "4600": date(2026, 11, 20)}


def test_notas_identicas_nao_ficam_sem_boleto_e_geram_aviso(tmp_path):
    """Mesmo fornecedor, mesmo valor e boleto sem o nº da nota: impossível ter certeza."""
    lancs = _processar(tmp_path, [
        ("b1.pdf", [pagina_boleto(*ALFA, "99886", date(2026, 11, 13), "1.100,00", LIVRE_1)]),
        ("b2.pdf", [pagina_boleto(*ALFA, "99887", date(2026, 11, 20), "1.100,00", LIVRE_2)]),
        ("nf2.pdf", [pagina_danfe(numero=4600, valor="1.100,00")]),
        ("nf3.pdf", [pagina_danfe(numero=4601, valor="1.100,00")]),
    ])
    assert sorted(l.nota.numero for l in lancs) == ["4600", "4601"]  # um boleto para cada
    assert all(l.boleto for l in lancs)
    assert all(any("confira" in a for a in l.avisos) for l in lancs)
