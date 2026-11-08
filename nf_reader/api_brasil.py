"""Integração com a BrasilAPI (https://brasilapi.com.br), API pública e gratuita.

- Feriados nacionais: para calcular a data efetiva de pagamento (próximo dia útil).
- CNPJ: para conferir a situação cadastral do fornecedor na Receita Federal.

O robô não pode parar porque a internet caiu ou a API está fora do ar: cada
chamada tem tempo limite, novas tentativas com espera crescente e cache em disco.
Se mesmo assim falhar, levanta ApiIndisponivel e quem chamou decide o que fazer
(normalmente seguir com um aviso de "não verificado").
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

from .validacoes import formatar_cnpj, validar_cnpj

log = logging.getLogger(__name__)

URL_PADRAO = "https://brasilapi.com.br/api"


class ApiIndisponivel(RuntimeError):
    """A API não respondeu depois de todas as tentativas."""


@dataclass
class SituacaoCnpj:
    cnpj: str
    encontrado: bool
    razao_social: str | None = None
    situacao: str | None = None          # ATIVA, BAIXADA, INAPTA, SUSPENSA, NULA

    @property
    def ativa(self) -> bool:
        return self.encontrado and (self.situacao or "").upper() == "ATIVA"


class BrasilAPI:
    def __init__(self, url_base: str = URL_PADRAO, arquivo_cache: Path | None = None,
                 tentativas: int = 3, timeout: float = 10.0, espera_inicial: float = 1.0):
        self.url_base = url_base.rstrip("/")
        self.arquivo_cache = arquivo_cache
        self.tentativas = tentativas
        self.timeout = timeout
        self.espera_inicial = espera_inicial
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = "leitor-notas-fiscais (projeto academico)"
        self._cache: dict = self._carregar_cache()

    # ----------------------------------------------------------------- cache ----
    def _carregar_cache(self) -> dict:
        try:
            if self.arquivo_cache and self.arquivo_cache.exists():
                return json.loads(self.arquivo_cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as erro:
            log.warning("Cache da API ilegível (%s); começando vazio", erro)
        return {}

    def _salvar_cache(self) -> None:
        if not self.arquivo_cache:
            return
        try:
            self.arquivo_cache.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo_cache.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as erro:
            log.warning("Não foi possível gravar o cache da API: %s", erro)

    # ------------------------------------------------------------- requisição ----
    def _get(self, caminho: str) -> requests.Response:
        url = f"{self.url_base}/{caminho}"
        espera = self.espera_inicial
        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.tentativas + 1):
            try:
                log.info("API: GET %s (tentativa %d/%d)", url, tentativa, self.tentativas)
                resposta = self.sessao.get(url, timeout=self.timeout)
                if resposta.status_code >= 500 or resposta.status_code == 429:
                    raise requests.HTTPError(f"HTTP {resposta.status_code}", response=resposta)
                log.info("API: resposta HTTP %d", resposta.status_code)
                return resposta
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as erro:
                ultimo_erro = erro
                log.warning("API: falha na tentativa %d: %s", tentativa, erro)
                if tentativa < self.tentativas:
                    time.sleep(espera)
                    espera *= 2  # espera crescente: 1s, 2s, 4s...
        raise ApiIndisponivel(f"{url} não respondeu após {self.tentativas} tentativas: {ultimo_erro}")

    # -------------------------------------------------------------- feriados ----
    def feriados(self, ano: int) -> dict[date, str]:
        chave = f"feriados:{ano}"
        if chave not in self._cache:
            resposta = self._get(f"feriados/v1/{ano}")
            resposta.raise_for_status()
            self._cache[chave] = {f["date"]: f["name"] for f in resposta.json()}
            self._salvar_cache()
        return {date.fromisoformat(d): nome for d, nome in self._cache[chave].items()}

    def proximo_dia_util(self, dia: date) -> tuple[date, str | None]:
        """Data efetiva de pagamento e o motivo do ajuste (fim de semana/feriado)."""
        motivo = None
        while True:
            feriados = self.feriados(dia.year)
            if dia.weekday() >= 5:
                motivo = motivo or ("sábado" if dia.weekday() == 5 else "domingo")
            elif dia in feriados:
                motivo = motivo or f"feriado: {feriados[dia]}"
            else:
                return dia, motivo
            dia += timedelta(days=1)

    def dias_uteis_entre(self, inicio: date, fim: date) -> int:
        dias, atual = 0, inicio
        while atual < fim:
            atual += timedelta(days=1)
            if atual.weekday() < 5 and atual not in self.feriados(atual.year):
                dias += 1
        return dias

    # ------------------------------------------------------------------ CNPJ ----
    def consultar_cnpj(self, cnpj: str) -> SituacaoCnpj:
        digitos = validar_cnpj(cnpj)  # nem chama a API com CNPJ inválido
        chave = f"cnpj:{digitos}"
        if chave not in self._cache:
            resposta = self._get(f"cnpj/v1/{digitos}")
            if resposta.status_code == 404:
                self._cache[chave] = {"encontrado": False}
            else:
                resposta.raise_for_status()
                dados = resposta.json()
                # Guarda só o necessário (a resposta também traz dados pessoais dos sócios)
                self._cache[chave] = {
                    "encontrado": True,
                    "razao_social": dados.get("razao_social"),
                    "situacao": dados.get("descricao_situacao_cadastral"),
                }
            self._salvar_cache()
        return SituacaoCnpj(cnpj=formatar_cnpj(digitos), **self._cache[chave])
