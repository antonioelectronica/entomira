"""
Servidor_Trampas — acceso directo al dashboard de ENTOMIRA.

Abre el navegador en la web de la Raspberry Pi. Prueba primero la IP de Tailscale
(acceso remoto) y cae a la IP local si no responde. Muestra una ventana breve de
estado mientras decide.

Se empaqueta a Servidor_Trampas.exe con PyInstaller (--noconsole).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

# URLs: se leen de urls.json (junto al ejecutable/paquete) para poder cambiar la IP de
# Tailscale sin recompilar; con fallback a variables de entorno y a la IP local.
_URLS_FILE = Path(__file__).resolve().parents[1] / "urls.json"
_urls = {}
try:
    if _URLS_FILE.exists():
        _urls = json.loads(_URLS_FILE.read_text(encoding="utf-8"))
except Exception:
    _urls = {}

TAILSCALE_URL = (_urls.get("tailscale_url")
                 or os.environ.get("RPI_TAILSCALE_URL", "")).strip()
LOCAL_URL = (_urls.get("local_url")
             or os.environ.get("RPI_LOCAL_URL", "http://127.0.0.1:8000")).strip()

VERDE_OSC = "#1B4332"
VERDE_CLR = "#52B788"
AMBAR = "#F7A93B"
FONDO = "#0F1E17"


def _reachable(url: str, timeout: float = 2.0) -> bool:
    if not url:
        return False
    p = urlparse(url)
    host, port = p.hostname, p.port or (443 if p.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pick_url() -> str:
    for url in (TAILSCALE_URL, LOCAL_URL):
        if _reachable(url):
            return url
    # Si ninguna responde, abre igualmente la local (mostrará el error del navegador).
    return LOCAL_URL


def main() -> None:
    root = tk.Tk()
    root.title("ENTOMIRA · Servidor de Trampas")
    root.configure(bg=FONDO)
    root.geometry("420x180")
    root.resizable(False, False)

    tk.Label(root, text="ENTOMIRA", bg=FONDO, fg="white",
             font=("Montserrat", 20, "bold")).pack(pady=(24, 2))
    tk.Label(root, text="Abriendo el panel de control…", bg=FONDO, fg=VERDE_CLR,
             font=("Segoe UI", 10)).pack()
    status = tk.StringVar(value="Buscando servidor…")
    tk.Label(root, textvariable=status, bg=FONDO, fg=AMBAR,
             font=("Consolas", 9)).pack(pady=14)

    def work():
        url = _pick_url()
        via = "Tailscale" if url == TAILSCALE_URL and TAILSCALE_URL else "red local"
        status.set(f"Conectando vía {via}: {url}")
        webbrowser.open(url)
        root.after(1200, root.destroy)

    threading.Thread(target=work, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
