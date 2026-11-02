"""
Lanzador de Worker_IA.exe.

Arranca la app GUI del worker con el pythonw.exe del venv de ENTOMIRA (sin consola),
que ya tiene torch+CUDA+ultralytics. Empaquetar todo el stack de ML dentro del .exe
sería frágil y de varios GB; este lanzador es pequeño y fiable. Ver
Docs_Obsidian/08_Despliegue.md.
"""
import os
import subprocess
import sys
from pathlib import Path

VENV_PYW = Path(r"C:\ento_ml\venv\Scripts\pythonw.exe")
VENV_PY = Path(r"C:\ento_ml\venv\Scripts\python.exe")
WORKER_DIR = Path(r"C:\entomira\worker")
TARGET = WORKER_DIR / "run_worker.py"


def _error(msg: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("ENTOMIRA · Worker IA", msg)
        r.destroy()
    except Exception:
        pass
    sys.exit(1)


def main() -> None:
    py = VENV_PYW if VENV_PYW.exists() else VENV_PY
    if not py.exists():
        _error(f"No se encontró el entorno de Python del worker:\n{VENV_PYW}\n\n"
               "Revisa la instalación en C:\\ento_ml\\venv.")
    if not TARGET.exists():
        _error(f"No se encontró la app del worker:\n{TARGET}")
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    subprocess.Popen([str(py), str(TARGET)], cwd=str(WORKER_DIR),
                     creationflags=creationflags, close_fds=True)


if __name__ == "__main__":
    main()
