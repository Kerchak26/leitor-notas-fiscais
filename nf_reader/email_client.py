"""Leitura de e-mails via IMAP e download dos anexos PDF."""

from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Iterator

from .config import Config

log = logging.getLogger(__name__)

MESES_IMAP = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class AnexoPDF:
    nome: str
    conteudo: bytes


@dataclass
class EmailRecebido:
    message_id: str
    assunto: str
    remetente: str
    anexos: list[AnexoPDF]

    @property
    def descricao(self) -> str:
        return f"{self.remetente} | {self.assunto}"


def _decodificar(valor: str | None) -> str:
    return str(make_header(decode_header(valor))) if valor else ""


def _nome_seguro(nome: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", nome).strip() or "anexo.pdf"


def anexos_pdf(msg: Message) -> list[AnexoPDF]:
    """PDFs do e-mail, inclusive os que estão dentro de e-mails encaminhados como anexo."""
    anexos = []
    # walk() já entra nas partes message/rfc822 (encaminhado "como anexo")
    for parte in msg.walk():
        if parte.is_multipart():
            continue
        nome = _decodificar(parte.get_filename())
        if parte.get_content_type() == "application/pdf" or nome.lower().endswith(".pdf"):
            conteudo = parte.get_payload(decode=True)
            if conteudo:
                anexos.append(AnexoPDF(nome or "anexo.pdf", conteudo))
        elif nome.lower().endswith(".eml"):  # .eml enviado como arquivo comum
            conteudo = parte.get_payload(decode=True)
            if conteudo:
                anexos.extend(anexos_pdf(email.message_from_bytes(conteudo)))
    return anexos


class ClienteEmail:
    def __init__(self, config: Config):
        self.config = config
        self.imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "ClienteEmail":
        if not self.config.usuario or not self.config.senha:
            raise RuntimeError("Defina EMAIL_USUARIO e EMAIL_SENHA no arquivo .env")
        self.imap = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        self.imap.login(self.config.usuario, self.config.senha)
        self.imap.select(self.config.pasta_email)
        log.info("Conectado a %s como %s", self.config.imap_host, self.config.usuario)
        return self

    def __exit__(self, *_):
        if self.imap:
            try:
                self.imap.close()
            finally:
                self.imap.logout()

    def _criterio_busca(self) -> str:
        criterios = []
        if d := self.config.data_inicio:
            criterios.append(f"SINCE {d.day:02d}-{MESES_IMAP[d.month - 1]}-{d.year}")
        remetentes = [f'FROM "{r}"' for r in self.config.filtro_remetente]
        if remetentes:
            # IMAP só tem OR binário: OR a (OR b c)
            filtro = remetentes[-1]
            for r in reversed(remetentes[:-1]):
                filtro = f"OR {r} {filtro}"
            criterios.append(f"({filtro})" if len(remetentes) > 1 else filtro)
        return " ".join(criterios) or "ALL"

    def buscar_emails(self, ja_processados: set[str]) -> Iterator[EmailRecebido]:
        """E-mails ainda não processados que têm pelo menos um PDF anexado."""
        assert self.imap is not None
        status, dados = self.imap.uid("SEARCH", None, self._criterio_busca())
        if status != "OK":
            raise RuntimeError(f"Falha na busca IMAP: {dados}")
        uids = dados[0].split()
        log.info("%d e-mail(s) encontrados na pasta %s", len(uids), self.config.pasta_email)

        for uid in uids:
            # BODY.PEEK não marca o e-mail como lido
            status, partes = self.imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not partes or partes[0] is None:
                continue
            msg = email.message_from_bytes(partes[0][1])
            message_id = msg.get("Message-ID") or f"uid-{uid.decode()}"
            if message_id in ja_processados:
                continue
            assunto = _decodificar(msg.get("Subject"))
            filtro = self.config.filtro_assunto
            if filtro and not any(p in assunto.lower() for p in filtro):
                continue
            anexos = anexos_pdf(msg)
            if not anexos:
                continue

            yield EmailRecebido(message_id, assunto, _decodificar(msg.get("From")), anexos)

            if self.config.marcar_como_lido:
                self.imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")


def pasta_do_email(msg: EmailRecebido, base: Path) -> Path:
    """Subpasta própria para os anexos de cada e-mail (mantém nota e boleto juntos)."""
    sufixo = hashlib.sha1(msg.message_id.encode()).hexdigest()[:8]
    assunto = re.sub(r"\s+", " ", _nome_seguro(msg.assunto))[:60].strip() or "sem assunto"
    return base / f"{assunto} [{sufixo}]"


def salvar_anexo(anexo: AnexoPDF, pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    original = pasta / _nome_seguro(anexo.nome)
    destino, contador = original, 1
    # Não sobrescreve um arquivo diferente que tenha o mesmo nome
    while destino.exists() and destino.read_bytes() != anexo.conteudo:
        destino = pasta / f"{original.stem}_{contador}{original.suffix}"
        contador += 1
    destino.write_bytes(anexo.conteudo)
    return destino
