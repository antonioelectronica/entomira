"""
Lanzador de Servidor_Trampas.exe.

Abre la app que redirige al dashboard de la RPi (Tailscale con fallback a IP local),
usando el pythonw.exe del venv de ENTOMIRA. Ver Docs_Obsidian/08_Despliegue.md.
"""
import os
import subprocess
import sys
from pathlib import Path

VENV_PYW = Path(r"C:\ento_ml\venv\Scripts\pythonw.exe")
VENV_PY = Path(r"C:\ento_ml\venv\Scripts\python.exe")
WORKER_DIR = Path(r"C:\entomira\worker")
TARGET = WORKER_DIR / "run_servidor.py"


def _error(msg: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("ENTOMIRA · Servidor de Trampas", msg)
        r.destroy()
    except Exception:
        pass
    sys.exit(1)


def main() -> None:
    py = VENV_PYW if VENV_PYW.exists() else VENV_PY
    if not py.exists():
        _error(f"No se encontró el entorno de Python:\n{VENV_PYW}")
    if not TARGET.exists():
        _error(f"No se encontró la app:\n{TARGET}")
    creationflags = 0x08000000 if os.name == "nt" else 0
    subprocess.Popen([str(py), str(TARGET)], cwd=str(WORKER_DIR),
                     creationflags=creationflags, close_fds=True)


if __name__ == "__main__":
    main()
