# -*- coding: utf-8 -*-
"""Arranca el Worker de IA SIN interfaz grafica, para poder lanzarlo en remoto.

Por que hace falta: los lanzadores existentes (`run_worker.py`, `Worker_IA.exe`) abren una
ventana, y una GUI lanzada desde una sesion remota acaba en un escritorio que no ve nadie
--gotcha 12 de este proyecto--. Ademas, si el worker solo se puede arrancar a mano y con
raton, basta con que alguien no lo abra para que el sistema deje de analizar fotos **sin que
nada falle**: paso, y se descubrio el 2026-09-19 con 317 imagenes en `pendiente` y cero
procesadas, camino de borrarse a los 7 dias sin que nadie las hubiera mirado.

    C:\\ento_ml\\venv\\Scripts\\python.exe worker/run_worker_consola.py [--una-pasada]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from entomira_worker import config                                 # noqa: E402
from entomira_worker.client import ServerClient                          # noqa: E402
from entomira_worker.loop import WorkerLoop, YaHayOtroWorker                # noqa: E402


def estado(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--una-pasada", action="store_true",
                    help="comprueba la conexion y sale, sin quedarse procesando")
    args = ap.parse_args()

    # El candado ya NO esta aqui: vive en `WorkerLoop.run_forever`, que es por donde
    # pasan tambien las dos interfaces graficas. Tenerlo solo en este lanzador dejaba
    # esas dos puertas abiertas, y son justamente por las que entraron los dos workers
    # que se bloquearon en la GPU el 2026-09-19.

    estado("servidor: %s" % config.SERVER_BASE_URL)
    estado("modelo:   %s" % config.MODEL_PATH)
    if not Path(config.MODEL_PATH).exists():
        estado("EL MODELO NO EXISTE. Sin el no se puede inferir nada.")
        return 1

    # Comprobar la conexion ANTES de entrar al bucle. Si la clave esta mal, el bucle se pasa
    # la vida reintentando en silencio y desde fuera parece que no hay trabajo pendiente.
    cli = ServerClient()
    if not cli.ping():
        estado("NO SE LLEGA AL SERVIDOR. Revisa SERVER_BASE_URL y que la API este viva.")
        return 1
    pendientes = cli.pending_images()
    estado("conexion OK. Pendientes ahora mismo: %d" % len(pendientes))
    if args.una_pasada:
        return 0

    estado("entrando al bucle (Ctrl-C para parar)")
    try:
        WorkerLoop(on_status=estado).run_forever()
    except YaHayOtroWorker:
        # Ya lo ha dicho por pantalla el propio bucle. Aqui lo que importa es SALIR CON 1:
        # quien lanza esto desde un script lee el codigo, no el texto.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
