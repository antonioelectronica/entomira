"""Sensores en directo: lo que la trampa mide AHORA, para el panel, con cámara o sin ella.

**Por qué existe.** El panel de control sacaba la batería y el consumo de la ficha de la
trampa (que solo se repinta al anunciarse un cambio) y de las ventanas de 5 min del INA226.
Y lo que hacía informar deprisa a la trampa era el latido del VÍDEO: sin cámara, el visor se
rinde a los 40 s, para el latido y la trampa vuelve a su ritmo lento. Antonio, 2026-09-25:
«a veces si no hay cámara no actualiza el estado de los sensores» y «si estoy en el panel
quiero ver los datos instantáneos de voltaje y corriente».

Así que aquí va un latido PROPIO de los sensores, que no pide vídeo ni enciende la cámara:
mientras alguien mira, la trampa informa a su suelo (2 s) y cada informe trae la última lectura
del INA226 y del MAX17043.

**Todo en memoria**, igual que `live.py`: es una señal instantánea, no un histórico (el
histórico ya son las ventanas de `LecturaConsumo`). Si el servidor reinicia, se vuelve a llenar
con el siguiente informe y no hay nada que migrar.

**La estimación de la celda.** El INA226 está a la SALIDA del elevador de 5 V: mide lo que la
trampa gasta, no lo que sale de la batería. Con la tensión real de la celda (MAX17043) se puede
estimar lo segundo:

    P_celda = P_salida / η          I_celda = P_celda / V_celda

y con el SOC y la capacidad, cuánto le queda. Pero hay dos números que no se miden aquí —el
rendimiento del elevador y la capacidad de la celda— y se dicen como SUPUESTOS en la respuesta.
Sin capacidad configurada no se da tiempo restante: inventarla sería dar horas falsas con
cara de medida. Y si la celda está CARGANDO no se estima nada: parte de lo que gasta la trampa
lo pone entonces el cargador, y la fórmula daría una descarga que no existe.
"""
from __future__ import annotations

import math
import os
import time

from . import bateria_estimada as bat_est

# Cuánto se mantiene el latido de sensores desde la última pregunta del panel. Corto: si la
# pestaña se cierra, la trampa tiene que volver a su ritmo lento enseguida.
SENSORES_S = 15
# Una lectura más vieja que esto no es «instantánea». Con el latido activo llegan cada ~2 s.
FRESCO_S = 30.0
# Separación máxima entre la lectura del INA y la de la celda para cruzarlas en la estimación.
# Viajan en el MISMO informe, así que normalmente es 0: más que esto es que uno de los dos dejó
# de contestar y se estaría dividiendo una corriente vieja por una tensión nueva. Codex, 26/09.
SINCRONIA_S = 5.0

# SUPUESTOS DE LA ESTIMACIÓN, configurables sin tocar código.
# Rendimiento del elevador del DFR0559 (3,0-4,2 V -> 5 V). No está medido en esta trampa: se
# mide poniendo un INA también en la celda. Hasta entonces, un valor típico de elevador síncrono
# a estas corrientes, y se dice que es supuesto.
ELEVADOR_ETA = float(os.environ.get("ENTOMIRA_ELEVADOR_ETA", "0.85"))
# Capacidad NOMINAL de la celda en mAh: la de la etiqueta de las dos 18650 compradas el
# 2026-07-05 (3300 mAh; `Contexto_Obsidian/Fuentes/Contexto_TrapEye_Historico.md`), la misma que
# usa la memoria. Es nominal, no medida: una 18650 barata suele dar menos, así que la autonomía
# sale optimista. `ENTOMIRA_CELDA_MAH` la cambia; vacía, no se da tiempo restante.
_mah = os.environ.get("ENTOMIRA_CELDA_MAH", "3300").strip()
CELDA_MAH: float | None = float(_mah) if _mah else None

_ultimo: dict[int, dict] = {}
_pedido: dict[int, float] = {}


# ------------------------------------------------------------------ el latido
def pedir(trap_id: int, segundos: int = SENSORES_S) -> None:
    """Alguien tiene el panel de sensores abierto: que la trampa informe deprisa."""
    _pedido[trap_id] = time.time() + segundos


def quiere(trap_id: int) -> bool:
    hasta = _pedido.get(trap_id, 0)
    if hasta and hasta < time.time():
        _pedido.pop(trap_id, None)
        return False
    return bool(hasta)


# ------------------------------------------------------------------ las lecturas
def _num(x, lo: float, hi: float) -> float | None:
    """Un número dentro de lo físicamente posible, o hueco. Nunca un cero inventado."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if not math.isfinite(x) or not lo <= x <= hi:
        return None
    return x


# Resistencia interna aproximada de la 18650 más su cableado. El INA219 mide con la Zero
# consumiendo; sin compensar, una descarga de 300 mA restaría unos 30 mV y algún tramo de 5 %.
R_CELDA_OHM = 0.1


def _soc_ina219(v, ma) -> int | None:
    """Porcentaje orientativo; positivo = carga, así que la tensión en reposo es v - I·R."""
    if v is None or ma is None:   # sin corriente no se compensa: aquí un cero no es cero
        return None
    return bat_est.estimar_por_tension(v - ma / 1000.0 * R_CELDA_OHM)


def anotar(trap_id: int, ina_ya, voltaje, bateria, ina219=None) -> None:
    """Guarda lo que trae un informe. Lo llama `/telemetry`.

    Cada campo se valida por separado y un campo raro se queda en hueco: la telemetría no se
    rechaza nunca por esto (un 422 la trampa lo repetiría en cada informe y parecería caída).
    """
    d = _ultimo.get(trap_id, {}).copy()
    t = time.time()
    if isinstance(ina_ya, dict):
        # El shunt tiene signo (módulo cableado al revés = corriente negativa) y techo de
        # ±819 mA con el R100; la tensión de raíl, de 0 a 36 V según la hoja del INA226.
        d["rail_v"] = _num(ina_ya.get("v"), 0.0, 36.0)
        d["ma"] = _num(ina_ya.get("ma"), -1000.0, 1000.0)
        d["t_ina"] = t
    if isinstance(ina219, dict):
        # La Zero manda esta lectura en cada telemetría; el histórico de LecturaHardware
        # se guarda solo una vez por minuto y no sirve para mostrar corriente en directo.
        d["ina219_v"] = _num(ina219.get("v"), 2.5, 5.5)
        d["ina219_ma"] = _num(ina219.get("ma"), -35000.0, 35000.0)
        d["t_ina219"] = t
    v = _num(voltaje, 2.5, 4.5)
    if v is not None:
        d["celda_v"] = v
        d["soc"] = _num(bateria, 0.0, 100.0)
        d["t_celda"] = t
    if d:
        _ultimo[trap_id] = d


def estimar(rail_v: float | None, ma: float | None, celda_v: float | None,
            soc: float | None, estado: str | None,
            eta: float = ELEVADOR_ETA, mah: float | None = CELDA_MAH) -> dict:
    """Corriente que sale de la celda y tiempo que le queda, con sus supuestos a la vista.

    `estado` es el veredicto de `services/bateria.py` (cargando, descargando, reposo, midiendo).
    Devuelve siempre los supuestos y, cuando no se puede estimar, `motivo` dice por qué.

    **EL TIEMPO QUE QUEDA SOLO CON DESCARGA COMPROBADA.** Antes se anulaba solo con «cargando»,
    y ese veredicto llega tarde: recién enchufado el cargador, o con la celda cargando a tensión
    casi quieta, sale «reposo» o «midiendo», y se daba por descarga lo que en parte ponía el
    cargador. Hallazgo de Codex, 26/09, y era justo lo que enseñaba el panel: «reposo» con la
    celda al 0 % y una autonomía. La corriente sí se da en esos casos, como «si va de batería».
    """
    out = {"eta_supuesta": eta, "celda_mah": mah, "p_salida_w": None, "i_celda_ma": None,
           "horas": None, "motivo": None}
    if rail_v is None or ma is None:
        out["motivo"] = "sin lectura del medidor de consumo"
        return out
    p_salida = rail_v * ma / 1000.0
    out["p_salida_w"] = round(p_salida, 3)
    if celda_v is None:
        out["motivo"] = "sin tensión de la celda"
        return out
    if estado == "cargando":
        out["motivo"] = "la celda está cargando: parte del consumo lo pone el cargador"
        return out
    if p_salida <= 0:
        # Corriente nula o negativa a la salida: la trampa no está tirando del elevador (va
        # alimentada por otro camino, o el INA está cableado al revés). No es «autonomía
        # infinita».
        out["motivo"] = "el medidor no ve consumo a la salida del elevador"
        return out
    i_celda = p_salida / eta / celda_v * 1000.0
    out["i_celda_ma"] = round(i_celda, 1)
    if estado != "descargando":
        out["motivo"] = ("no consta que vaya de batería (la tensión de la celda no baja): la "
                         "corriente es la que saldría si fuera así, y la autonomía no se calcula")
    elif mah is None:
        out["motivo"] = "falta la capacidad de la celda (ENTOMIRA_CELDA_MAH)"
    elif soc is None:
        out["motivo"] = "sin estado de carga de la celda"
    else:
        out["horas"] = round(soc / 100.0 * mah / i_celda, 2)
    return out


def ahora(trap_id: int, estado: str | None) -> dict:
    """Lo último medido, con su edad, y la estimación de la celda."""
    d = _ultimo.get(trap_id, {})
    t = time.time()

    def _edad(k):
        return round(t - d[k], 1) if k in d else None

    edad_ina, edad_celda = _edad("t_ina"), _edad("t_celda")
    edad_ina219 = _edad("t_ina219")
    ina219_ok = edad_ina219 is not None and edad_ina219 <= FRESCO_S
    # Lo viejo no se mezcla con lo nuevo: una corriente de hace diez minutos dividida por la
    # tensión de ahora es un número que no ha existido nunca.
    ina_ok = edad_ina is not None and edad_ina <= FRESCO_S
    celda_ok = edad_celda is not None and edad_celda <= FRESCO_S
    rail_v = d.get("rail_v") if ina_ok else None
    ma = d.get("ma") if ina_ok else None
    celda_v = d.get("celda_v") if celda_ok else None
    soc = d.get("soc") if celda_ok else None
    # Cada lectura puede ser fresca y aun así no ser del mismo instante que la otra.
    desfase = (abs(d["t_ina"] - d["t_celda"])
               if ina_ok and celda_ok else None)
    if desfase is not None and desfase > SINCRONIA_S:
        est = estimar(rail_v, ma, None, None, estado)
        est["motivo"] = ("las lecturas del medidor de consumo y de la celda son de momentos "
                         "distintos (%.0f s): no se cruzan" % desfase)
    else:
        est = estimar(rail_v, ma, celda_v, soc, estado)
    return {
        "ina219_v": d.get("ina219_v") if ina219_ok else None,
        "ina219_ma": d.get("ina219_ma") if ina219_ok else None,
        "edad_hw_s": edad_ina219,
        "soc_estimado": _soc_ina219(d.get("ina219_v"), d.get("ina219_ma")) if ina219_ok else None,
        "rail_v": rail_v, "ma": ma,
        "mw": round(rail_v * ma, 1) if rail_v is not None and ma is not None else None,
        "edad_ina_s": edad_ina,
        "celda_v": celda_v, "soc": soc, "edad_celda_s": edad_celda,
        "estimacion": est,
    }


def olvidar() -> None:
    """Para las pruebas."""
    _ultimo.clear()
    _pedido.clear()
