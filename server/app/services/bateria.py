"""
Detección RÁPIDA de si la trampa está cargando, a partir de la tensión.

Por qué no basta el `crate` del MAX17048: es una estimación **filtrada** del propio
fuel-gauge y tarda del orden de **5–15 minutos** en asentarse. Medido en esta trampa: al
quitar el USB marcó −10 %/h, luego −20, luego −25 y −34, todo mientras la batería hacía
exactamente lo mismo. Como indicador de «¿está cargando AHORA?» llega tarde.

La tensión, en cambio, reacciona **al instante**: al conectar el cargador se midió un salto
de **3,78 V → 4,14 V**. Así que aquí se guarda una ventana corta de lecturas por trampa y se
calcula la pendiente por mínimos cuadrados. Con telemetría cada 10 s, tres muestras bastan
para tener veredicto en **medio minuto** en vez de en un cuarto de hora.

Todo en memoria a propósito: es una señal instantánea, no un histórico. Si el servidor
reinicia, se vuelve a llenar en 30 segundos y no hay nada que migrar.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque

# Ventana de análisis. Corta para responder rápido, pero con varias muestras para que una
# lectura con ruido no cambie el veredicto.
VENTANA_S = 150.0
# Respaldo para cuando las muestras llegan espaciadas (trampa por USB, sin telemetría).
VENTANA_LARGA_S = 600.0
MIN_MUESTRAS = 3
# Umbral en mV/h. Por debajo de esto se considera reposo: el ruido de la lectura de tensión
# (±2 mV entre muestras consecutivas) puede producir pendientes aparentes de decenas de mV/h.
UMBRAL_MV_H = 80.0

_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=64))


def anotar(trap_id: int, voltaje: float | None) -> None:
    """Registra una lectura de tensión. La llaman /telemetry y /ingest."""
    if voltaje is None or voltaje <= 0:
        return
    _hist[trap_id].append((time.time(), float(voltaje)))


def tendencia_mv_h(trap_id: int) -> float | None:
    """Pendiente de la tensión en mV/hora, o None si no hay datos suficientes."""
    ahora = time.time()
    pts = [(t, v) for t, v in _hist.get(trap_id, ()) if ahora - t <= VENTANA_S]
    if len(pts) < MIN_MUESTRAS:
        # Ventana corta vacía: pasa cuando la trampa va por USB, donde la tensión solo viaja
        # con cada foto (~1/min) en vez de con la telemetría (cada 10 s). Antes que no decir
        # nada, se mira una ventana más ancha: el veredicto llega más tarde pero llega.
        pts = [(t, v) for t, v in _hist.get(trap_id, ()) if ahora - t <= VENTANA_LARGA_S]
    if len(pts) < MIN_MUESTRAS:
        return None
    span = pts[-1][0] - pts[0][0]
    if span < 20:                      # menos de 20 s: la pendiente sería puro ruido
        return None
    # regresión lineal simple (mínimos cuadrados) sobre (segundos, voltios)
    n = len(pts)
    t0 = pts[0][0]
    sx = sy = sxx = sxy = 0.0
    for t, v in pts:
        x = t - t0
        sx += x; sy += v; sxx += x * x; sxy += x * v
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    pendiente_v_s = (n * sxy - sx * sy) / den
    return pendiente_v_s * 3600.0 * 1000.0


# Veredicto que calcula la PROPIA TRAMPA (cliente_zero/carga.py): muestrea cada 2 s y responde
# en ~1 min, cosa que aquí es imposible con telemetría cada 60-600 s. Manda si es reciente.
ESTADOS_TRAMPA = ("cargando", "descargando", "llena", "indeterminado")
TRAMPA_VIGENTE_S = 180.0
_de_trampa: dict[int, tuple[float, dict]] = {}


def anotar_trampa(trap_id: int, dato) -> None:
    """Guarda el informe de carga de la trampa; uno malformado se ignora sin rechazar nada."""
    if not isinstance(dato, dict) or dato.get("estado") not in ESTADOS_TRAMPA:
        return
    def numero(x):
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            return None
        try:
            x = float(x)                  # un entero JSON de cientos de dígitos desborda aquí
        except OverflowError:
            return None
        return x if math.isfinite(x) else None

    edad = numero(dato.get("edad_s"))
    if edad is None or edad < 0:
        edad = 0.0
    limpio = {"estado": dato["estado"],
              "origen": dato.get("origen") if isinstance(dato.get("origen"), str) else None,
              "confianza": dato.get("confianza") if dato.get("confianza") in ("alta", "media", "baja") else "baja",
              "mv_min": numero(dato.get("mv_min"))}
    # La vigencia cuenta desde la ÚLTIMA MUESTRA de la trampa, no desde que llega: si la trampa
    # lleva rato sin muestras válidas (captura, vídeo), su veredicto caduca igual.
    _de_trampa[trap_id] = (time.time() - edad, limpio)


def estado(trap_id: int, crate: float | None) -> dict:
    """Veredicto: primero el de la trampa (rápido y sin picos); si no hay, la tendencia de aquí.

    Se devuelve de dónde sale la conclusión y con qué confianza, para que la interfaz pueda ser
    honesta sobre lo que sabe y lo que todavía no.
    """
    par = _de_trampa.get(trap_id)
    if par is not None and time.time() - par[0] <= TRAMPA_VIGENTE_S:
        d = par[1]
        return {"estado": d["estado"], "fuente": "trampa" + (":" + d["origen"] if d["origen"] else ""),
                "confianza": d["confianza"],
                "tendencia_mv_h": round(d["mv_min"] * 60) if d["mv_min"] is not None else None}

    # Con la nueva placa el signo de la corriente es la fuente del veredicto.
    # Si caduca, se informa sin dato; no se vuelve a deducir carga por tensión.
    if par is not None and par[1]["origen"] == "ina219":
        return {"estado": "midiendo", "fuente": "trampa:ina219 sin dato",
                "tendencia_mv_h": None}

    mv_h = tendencia_mv_h(trap_id)

    if mv_h is not None and mv_h > UMBRAL_MV_H:
        return {"estado": "cargando", "fuente": "tension", "tendencia_mv_h": round(mv_h)}
    if mv_h is not None and mv_h < -UMBRAL_MV_H:
        return {"estado": "descargando", "fuente": "tension", "tendencia_mv_h": round(mv_h)}

    # La tensión no se mueve. Puede ser reposo real o batería llena cargando en mantenimiento
    # (con la celda a tope el cargador reduce corriente y la tensión ya no sube): ahí el
    # `crate`, aunque lento, sí distingue.
    if crate is not None and crate > 0.5:
        return {"estado": "cargando", "fuente": "crate",
                "tendencia_mv_h": round(mv_h) if mv_h is not None else None}
    if crate is not None and crate < -0.5:
        return {"estado": "descargando", "fuente": "crate",
                "tendencia_mv_h": round(mv_h) if mv_h is not None else None}

    if mv_h is None:
        return {"estado": "midiendo", "fuente": "sin datos", "tendencia_mv_h": None}
    return {"estado": "reposo", "fuente": "tension", "tendencia_mv_h": round(mv_h)}
