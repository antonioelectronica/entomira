"""El worker analiza 10 min después de cada despertar (17/10/2026). Sin GPU ni servidor."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from entomira_worker import loop as lp                 # noqa: E402

MAD = ZoneInfo("Europe/Madrid")
HORAS = ["09:40", "11:10", "12:40", "14:10", "15:40", "17:10"]


def t(dia, hh, mm):
    return datetime(2026, 10, dia, hh, mm, tzinfo=MAD)


def test_franja_tras_la_hora_y_solo_una_vez():
    inicio = t(17, 8, 0)
    assert lp.franja_pendiente(t(17, 9, 39), HORAS, inicio, set()) is None
    clave = lp.franja_pendiente(t(17, 9, 41), HORAS, inicio, set())
    assert clave == "2026-10-17 09:40"
    assert lp.franja_pendiente(t(17, 9, 45), HORAS, inicio, {clave}) is None


def test_lo_anterior_al_arranque_no_cuenta():
    # Abierto a las 13:00: 09:40, 11:10 y 12:40 las cubre el análisis de arranque.
    assert lp.franja_pendiente(t(17, 13, 0), HORAS, t(17, 13, 0), set()) is None
    assert lp.franja_pendiente(t(17, 14, 15), HORAS, t(17, 13, 0), set()) == "2026-10-17 14:10"


def test_al_dia_siguiente_vuelve_a_tocar():
    hechas = {f"2026-10-17 {h}" for h in HORAS}
    assert lp.franja_pendiente(t(18, 9, 41), HORAS, t(17, 8, 0), hechas) == "2026-10-18 09:40"


def test_proxima_franja_y_vuelta_a_la_primera():
    assert lp.proxima_franja(t(17, 12, 0), HORAS) == "12:40"
    assert lp.proxima_franja(t(17, 18, 0), HORAS) == "09:40"


class ClienteFalso:
    """Cuenta cuántas veces se piden pendientes; nunca hay ninguno."""

    def __init__(self):
        self.pedidas = 0
        self.active_base = "falso"

    def ping(self):
        return True

    def push_stats(self, *_):
        return True

    def get_params(self):
        return {"intervalo": 0, "horas_analisis": HORAS}

    def pending_images(self):
        self.pedidas += 1
        return []


def test_al_arrancar_mira_la_cola_una_vez_y_luego_espera_a_la_franja(monkeypatch):
    w = lp.WorkerLoop()
    w.client = ClienteFalso()
    vueltas = {"n": 0}

    def dormir(_):
        vueltas["n"] += 1
        if vueltas["n"] > 20:
            w.stop()
    monkeypatch.setattr(lp.time, "sleep", dormir)
    # Sin franjas vencidas: el reloj está entre dos horas.
    monkeypatch.setattr(lp, "franja_pendiente", lambda *a: None)
    w._run_forever()
    assert w.client.pedidas == 1, "fuera de franja no debe consultar la cola"
