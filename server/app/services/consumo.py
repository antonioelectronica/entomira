"""Datos móviles gastados por cada trampa: cuánto, en qué y desde cuándo.

**Por qué existe.** Con una tarifa de datos, «cuánto llevo gastado» es tan importante como la
batería, y el sistema no lo sabía. El 2026-09-11 se midió que la telemetría cada 10 s se comía
~1 GB/mes —más que las propias fotos— y nadie lo veía: el gasto no se notaba hasta que llegara
la factura o se cortara el plan.

**Por qué en el servidor y no en la placa.** El módulo A7670E no expone un contador fiable, y
el servidor sí conoce el tamaño exacto de cada cosa que entra y sale.

**Qué se cuenta y qué se estima**, que no es lo mismo y no se mezclan:

- `bytes` — carga útil **medida**: el cuerpo de cada petición y de cada respuesta.
- `conexiones` — **cuántas veces** se abrió una conexión nueva. Cada una cuesta un saludo TLS
  que no aparece en ninguna carga útil, y con peticiones pequeñas (telemetría: 150 bytes) **es
  la mayor parte del gasto**. Se guarda aparte para que el total estimado se pueda recalcular
  el día que se mida el saludo de verdad, sin falsear el histórico.

Solo se cuenta cuando `trampa.red == 'lte'`: por WiFi o por el cable USB no se gasta plan.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..models.models import Consumo, Trampa

# Coste ESTIMADO de abrir una conexión TLS con el extremo del dispositivo: saludo completo
# (ClientHello/ServerHello, certificado RSA-2048, intercambio de claves) más el arranque y
# cierre de TCP, en los dos sentidos. Es una estimación declarada, no una medida: el día que se
# mida de verdad basta con cambiar esta constante, porque las conexiones se guardan contadas.
COSTE_CONEXION_B = 4096

CONCEPTOS = ("ingesta", "telemetria", "vivo", "ota", "config")


def _hoy() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ACUMULA EN MEMORIA Y VUELCA CADA POCO.
#
# La primera versión escribía en la base de datos en CADA apunte, y eso incluye cada fotograma
# de vídeo: a 6,5 fps son seis `commit` por segundo sobre la microSD de la Pi. Medido: una tanda
# de vídeo dejó 14,5 MB contados en ~2.200 escrituras. Contar el gasto no puede salir más caro
# que el gasto que cuenta, ni desgastar la tarjeta donde vive todo el histórico.
#
# Lo que se pierde a cambio: hasta `VOLCADO_S` segundos de cuenta si el servidor se para de
# golpe. Es un contador de datos móviles, no una caja registradora.
_pendiente: dict[tuple[int, str, str], list[int]] = {}
_ultimo_volcado = 0.0
VOLCADO_S = 30


def volcar(session: Session) -> None:
    """Escribe lo acumulado en memoria. Se llama sola, y también antes de leer el resumen."""
    global _ultimo_volcado
    _ultimo_volcado = time.time()
    if not _pendiente:
        return
    pendiente = dict(_pendiente)
    _pendiente.clear()
    try:
        for (trap_id, dia, concepto), (b, c) in pendiente.items():
            fila = session.exec(
                select(Consumo).where(Consumo.trampa_id == trap_id, Consumo.dia == dia,
                                      Consumo.concepto == concepto)).first()
            if fila is None:
                fila = Consumo(trampa_id=trap_id, dia=dia, concepto=concepto,
                               bytes=0, conexiones=0)
            fila.bytes += b
            fila.conexiones += c
            session.add(fila)
        session.commit()
    except Exception:                                # noqa: BLE001 - contar no puede romper nada
        session.rollback()


def anotar(session: Session, trap: Trampa, concepto: str,
           bytes_: int = 0, conexiones: int = 1) -> None:
    """Apunta gasto de hoy de esta trampa. No falla nunca hacia fuera.

    Se llama desde el camino caliente de la ingesta y del vídeo: si algo va mal aquí, lo que NO
    puede pasar es que se pierda la foto por culpa del contador.
    """
    try:
        if trap is None or (trap.red or "") != "lte":
            return                                   # por WiFi o cable no se gasta plan
        clave = (trap.id, _hoy(), concepto)
        acc = _pendiente.setdefault(clave, [0, 0])
        acc[0] += max(0, int(bytes_ or 0))
        acc[1] += max(0, int(conexiones or 0))
        if time.time() - _ultimo_volcado >= VOLCADO_S:
            volcar(session)
    except Exception:                                # noqa: BLE001
        pass


def resumen(session: Session, trap_id: int, dias: int = 30) -> dict:
    """Gasto de los últimos `dias`, por concepto, con el total estimado.

    `estimado_b` = carga útil medida + conexiones × `COSTE_CONEXION_B`. Se devuelven las tres
    cifras por separado para que la web pueda enseñar de dónde sale el número: un total sin
    desglose no deja decidir qué recortar.
    """
    volcar(session)                 # que lo de los últimos segundos también cuente
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d")
    filas = session.exec(
        select(Consumo).where(Consumo.trampa_id == trap_id, Consumo.dia >= desde)).all()

    por_concepto: dict[str, dict] = {}
    por_dia: dict[str, int] = {}
    bytes_tot = conex_tot = 0
    for f in filas:
        c = por_concepto.setdefault(f.concepto, {"bytes": 0, "conexiones": 0})
        c["bytes"] += f.bytes
        c["conexiones"] += f.conexiones
        bytes_tot += f.bytes
        conex_tot += f.conexiones
        por_dia[f.dia] = por_dia.get(f.dia, 0) + f.bytes + f.conexiones * COSTE_CONEXION_B

    for c in por_concepto.values():
        c["estimado_b"] = c["bytes"] + c["conexiones"] * COSTE_CONEXION_B

    return {
        "dias": dias,
        "bytes": bytes_tot,                          # medido
        "conexiones": conex_tot,                     # contado
        "coste_conexion_b": COSTE_CONEXION_B,        # estimado, declarado
        "estimado_b": bytes_tot + conex_tot * COSTE_CONEXION_B,
        "por_concepto": por_concepto,
        "por_dia": dict(sorted(por_dia.items())),
    }


def estimado_mes(session: Session, trap_id: int) -> int:
    """Bytes estimados en los últimos 30 días. Es lo que se compara con el plan contratado."""
    return resumen(session, trap_id, 30)["estimado_b"]
