"""Extração de texto de PDFs."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pdfplumber

log = logging.getLogger(__name__)


def extrair_paginas(pdf: str | Path | bytes) -> list[str]:
    """Texto de cada página do PDF (caminho ou bytes), preservando o layout das colunas."""
    origem = io.BytesIO(pdf) if isinstance(pdf, bytes) else pdf
    try:
        with pdfplumber.open(origem) as doc:
            return [pagina.extract_text(layout=True) or "" for pagina in doc.pages]
    except Exception as erro:  # PDF corrompido, protegido por senha etc.
        log.warning("Não foi possível ler o PDF: %s", erro)
        return []


def extrair_texto(pdf: str | Path | bytes) -> str:
    """Texto de todas as páginas do PDF."""
    return "\n".join(extrair_paginas(pdf))
