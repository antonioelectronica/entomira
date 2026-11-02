"""Estimación orientativa de carga de una celda Li-ion a partir de su tensión.

La tabla procede de un ejemplo de OCV de Analog Devices, no de una calibración
de la 18650 instalada. El INA219 mide con carga conectada, así que su lectura
puede diferir de la tensión en reposo. No usar este valor para alarmas.
https://www.analog.com/en/resources/design-notes/characterizing-a-lithiumion-li-cell-for-use-with-an-opencircuitvoltage-ocv-based-fuel-gauge.html
"""
from __future__ import annotations

import math


# Voltios de circuito abierto, porcentaje. Puntos de la tabla 2 del artículo.
PERFIL = ((3.305545, 0), (3.674776, 5), (3.686654, 10),
          (3.762185, 25), (3.79325, 40), (3.841219, 55),
          (3.884009, 60), (4.026773, 82.5), (4.177454, 100))


def estimar_por_tension(voltaje: float | None) -> int | None:
    """Porcentaje redondeado a 5 puntos; None si la lectura no parece de una celda."""
    if voltaje is None or not isinstance(voltaje, (int, float)):
        return None
    v = float(voltaje)
    if not math.isfinite(v) or not 2.5 <= v <= 4.3:
        return None
    if v <= PERFIL[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(PERFIL, PERFIL[1:]):
        if v <= v1:
            porcentaje = p0 + (v - v0) * (p1 - p0) / (v1 - v0)
            return int(round(porcentaje / 5) * 5)
    return 100
