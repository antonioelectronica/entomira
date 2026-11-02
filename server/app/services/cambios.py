"""
Contador de cambios: permite que la web se entere sola, sin recargar.

El problema: la web pinta lo que había al entrar. Llega una foto nueva, cambia la batería o
se procesa una inferencia, y en pantalla sigue lo viejo hasta que el usuario recarga.

La solución más simple que funciona bien tras el funnel: un **contador**. Cada vez que algo
cambia de verdad, sube. La web pregunta por él cada pocos segundos (una respuesta de ~60
bytes) y solo cuando cambia se repinta. Comparado con WebSocket o SSE:

- no hay conexión que mantener viva ni que reconectar cuando la red va y viene,
- pasa por el funnel de Tailscale sin buffering (es una petición corta, no un flujo),
- y si el servidor reinicia, la web se entera en el siguiente sondeo en vez de quedarse
  colgada de un socket muerto.

El coste es un sondeo cada 4 s por pestaña abierta, que en una Raspberry Pi 3B+ no se nota.
"""
from __future__ import annotations

import time
from threading import Lock

_lock = Lock()
_version = 0
_por_tipo: dict[str, int] = {}
_ultimo = time.time()


def marcar(tipo: str = "dato") -> None:
    """Registra que algo ha cambiado. La llaman ingesta, inferencia y config."""
    global _version, _ultimo
    with _lock:
        _version += 1
        _por_tipo[tipo] = _por_tipo.get(tipo, 0) + 1
        _ultimo = time.time()


def estado() -> dict:
    with _lock:
        return {"v": _version, "tipos": dict(_por_tipo),
                "hace_s": round(time.time() - _ultimo, 1)}
