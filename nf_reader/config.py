"""Configuração lida do arquivo .env / variáveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    imap_host: str
    imap_port: int
    usuario: str
    senha: str
    pasta_email: str
    filtro_assunto: list[str]
    filtro_remetente: list[str]
    data_inicio: date | None
    marcar_como_lido: bool
    pasta_anexos: Path
    arquivo_saida: Path

    @classmethod
    def carregar(cls) -> "Config":
        load_dotenv()
        data = os.getenv("DATA_INICIO", "").strip()
        return cls(
            imap_host=os.getenv("IMAP_HOST", "imap.gmail.com"),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            usuario=os.getenv("EMAIL_USUARIO", ""),
            senha=os.getenv("EMAIL_SENHA", ""),
            pasta_email=os.getenv("EMAIL_PASTA", "INBOX"),
            filtro_assunto=[p.strip().lower() for p in os.getenv("FILTRO_ASSUNTO", "").split(",") if p.strip()],
            filtro_remetente=[p.strip().lower() for p in os.getenv("FILTRO_REMETENTE", "").split(",") if p.strip()],
            data_inicio=datetime.strptime(data, "%d/%m/%Y").date() if data else None,
            marcar_como_lido=os.getenv("MARCAR_COMO_LIDO", "false").lower() in ("1", "true", "sim"),
            pasta_anexos=Path(os.getenv("PASTA_ANEXOS", "dados/anexos")),
            arquivo_saida=Path(os.getenv("ARQUIVO_SAIDA", "dados/notas_fiscais.xlsx")),
        )
