"""Robô Selenium que lança os títulos aprovados no ERP de contas a pagar.

Faz o que o analista fazia à mão: abre o sistema, entra com usuário e senha,
preenche o formulário "Novo título a pagar" para cada nota aprovada, salva e
lê o número de protocolo gerado. Cada passo vira uma linha de log e as telas
importantes são salvas como evidência (prints).

O ERP usado na demonstração é `erp_simulado/index.html`, uma página local que
imita um sistema real (login, validação de campos, protocolo, títulos duplicados).
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

log = logging.getLogger(__name__)

ERP_PADRAO = (Path(__file__).resolve().parent.parent / "erp_simulado" / "index.html").as_uri()


class ErroERP(RuntimeError):
    """Falha ao operar o ERP (fora do ar, tela inesperada, título recusado...)."""


@dataclass
class TituloERP:
    fornecedor: str
    cnpj: str
    unidade: str
    numero_nf: str
    parcela: str
    valor: str            # já formatado: 1.234,56
    vencimento: str       # dd/mm/aaaa
    data_pagamento: str   # dd/mm/aaaa
    linha_digitavel: str
    prioridade: str = "NORMAL"


class RoboERP:
    def __init__(self, url: str = ERP_PADRAO, usuario: str = "rpa.financeiro", senha: str = "demo123",
                 headless: bool = True, pasta_prints: Path | None = None, pausa: float = 0.0,
                 timeout: float = 10.0):
        self.url, self.usuario, self.senha = url, usuario, senha
        self.headless = headless
        self.pasta_prints = pasta_prints
        self.pausa = pausa          # >0 deixa a digitação visível (para gravar vídeo)
        self.timeout = timeout
        self.driver: webdriver.Chrome | None = None
        self.prints: list[Path] = []

    # --------------------------------------------------------------- navegador ----
    def __enter__(self) -> "RoboERP":
        opcoes = webdriver.ChromeOptions()
        if self.headless:
            opcoes.add_argument("--headless=new")
        for arg in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1366,900", "--lang=pt-BR"):
            opcoes.add_argument(arg)
        # Em máquinas normais o Selenium Manager encontra Chrome e driver sozinho;
        # as variáveis abaixo só são necessárias em ambientes sem Chrome instalado.
        if os.getenv("CHROME_BINARY"):
            opcoes.binary_location = os.environ["CHROME_BINARY"]
        servico = Service(os.environ["CHROMEDRIVER"]) if os.getenv("CHROMEDRIVER") else Service()
        try:
            log.info("ERP: abrindo o navegador (headless=%s)", self.headless)
            self.driver = webdriver.Chrome(service=servico, options=opcoes)
            # ERP_JANELA="x,y,largura,altura" posiciona a janela (útil para gravar a tela)
            x, y, largura, altura = map(int, os.getenv("ERP_JANELA", "0,0,1366,900").split(","))
            self.driver.set_window_rect(x, y, largura, altura)
        except WebDriverException as erro:
            raise ErroERP(f"não foi possível abrir o Chrome: {erro.msg}") from erro
        return self

    def __exit__(self, *_):
        if self.driver:
            log.info("ERP: fechando o navegador")
            self.driver.quit()

    def _esperar(self, condicao, descricao: str):
        try:
            return WebDriverWait(self.driver, self.timeout).until(condicao)
        except TimeoutException as erro:
            self.print("erro_" + re.sub(r"\W+", "_", descricao))
            raise ErroERP(f"tempo esgotado esperando {descricao}") from erro

    def _digitar(self, id_campo: str, texto: str) -> None:
        campo = self._esperar(EC.element_to_be_clickable((By.ID, id_campo)), f"campo {id_campo}")
        campo.clear()
        if self.pausa:
            for letra in texto:          # digitação "humana" para o vídeo
                campo.send_keys(letra)
                time.sleep(self.pausa / 10)
        else:
            campo.send_keys(texto)
        log.info("ERP:   campo %-16s <- %s", id_campo, texto)

    def print(self, nome: str) -> Path | None:
        """Salva a tela atual como evidência."""
        if not (self.pasta_prints and self.driver):
            return None
        try:
            self.pasta_prints.mkdir(parents=True, exist_ok=True)
            caminho = self.pasta_prints / f"{len(self.prints) + 1:02d}_{nome}.png"
            self.driver.save_screenshot(str(caminho))
            self.prints.append(caminho)
            log.info("ERP: print salvo em %s", caminho)
            return caminho
        except WebDriverException as erro:
            log.warning("ERP: não foi possível salvar o print: %s", erro)
            return None

    # ------------------------------------------------------------------ passos ----
    def entrar(self) -> None:
        log.info("ERP: acessando %s", self.url)
        try:
            self.driver.get(self.url)
        except WebDriverException as erro:
            raise ErroERP(f"ERP fora do ar ou endereço inválido: {self.url}") from erro
        self._esperar(EC.presence_of_element_located((By.ID, "form-login")), "a tela de login")
        self._digitar("login-usuario", self.usuario)
        self._digitar("login-senha", self.senha)
        self.print("login")
        self.driver.find_element(By.ID, "btn-entrar").click()
        try:
            self._esperar(EC.visibility_of_element_located((By.ID, "form-titulo")), "a tela principal após o login")
        except ErroERP:
            erro = self.driver.find_element(By.ID, "msg-login").text
            raise ErroERP(f"login recusado: {erro or 'motivo desconhecido'}")
        log.info("ERP: login realizado como %s", self.usuario)

    def lancar(self, titulo: TituloERP) -> str:
        """Preenche e salva um título; devolve o protocolo lido da tela."""
        log.info("ERP: lançando NF %s (%s) de %s", titulo.numero_nf, titulo.parcela, titulo.fornecedor)
        self._digitar("fornecedor", titulo.fornecedor)
        self._digitar("cnpj", titulo.cnpj)
        self._digitar("unidade", titulo.unidade)
        self._digitar("numero-nf", titulo.numero_nf)
        self._digitar("parcela", titulo.parcela)
        self._digitar("valor", titulo.valor)
        Select(self.driver.find_element(By.ID, "prioridade")).select_by_visible_text(titulo.prioridade)
        self._digitar("vencimento", titulo.vencimento)
        self._digitar("data-pagamento", titulo.data_pagamento)
        self._digitar("linha-digitavel", titulo.linha_digitavel)
        self.driver.find_element(By.ID, "btn-salvar").click()

        mensagem = self._esperar(EC.visibility_of_element_located((By.ID, "msg-titulo")), "a resposta do ERP")
        if "erro" in mensagem.get_attribute("class"):
            self.print(f"recusado_nf_{titulo.numero_nf}")
            raise ErroERP(f"ERP recusou o título: {mensagem.text}")
        protocolo = self.driver.find_element(By.ID, "protocolo").text
        if not re.fullmatch(r"CP-\d{4}-\d{6}", protocolo):     # leitura validada por regex
            raise ErroERP(f"protocolo em formato inesperado: {protocolo!r}")
        log.info("ERP: título salvo, protocolo %s", protocolo)
        self.print(f"salvo_nf_{titulo.numero_nf}")
        return protocolo
