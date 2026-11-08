"""Servidor HTTP local para o ERP simulado (erp_simulado/index.html).

Útil para testar o robô contra um endereço http:// de verdade, como seria com um
sistema web real, e para abrir o ERP no navegador sem depender de internet.

Uso: python -m nf_reader erp [--porta 8000]
"""

from __future__ import annotations

import functools
import logging
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

PASTA_ERP = Path(__file__).resolve().parent.parent / "erp_simulado"


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, formato, *args):  # registra os acessos no log do projeto
        log.info("ERP http: %s", formato % args)


def criar_servidor(porta: int = 8000, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = functools.partial(_Handler, directory=str(PASTA_ERP))
    return ThreadingHTTPServer((host, porta), handler)


def iniciar_em_segundo_plano(porta: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Sobe o servidor numa thread (porta 0 = qualquer porta livre). Devolve o servidor e a URL."""
    servidor = criar_servidor(porta)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, f"http://127.0.0.1:{servidor.server_address[1]}/"


def servir(porta: int = 8000) -> None:
    servidor = criar_servidor(porta)
    url = f"http://localhost:{servidor.server_address[1]}/"
    print(f"ERP simulado em {url}  (usuário: rpa.financeiro · senha: demo123)")
    print(f"Para o robô usar: python -m nf_reader rpa pasta dados/cenarios/principal --erp --url-erp {url}")
    print("Ctrl+C para parar.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        servidor.server_close()
