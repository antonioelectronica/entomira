"""Recorte y enderezado de la zona de la tarjeta, fijo por trampa.

Orden de Antonio (15/10/2026): cada foto de una trampa se recorta al portatarjetas y se le
corrige el ángulo con una calibración fija, medida sobre una foto real. Y la IA ve SOLO el
recorte: «lo de fuera no le aporta información».

El ORIGINAL se guarda intacto, byte a byte (la recompresión ya costó caro: ver
`images.save_capture`). El recorte es un derivado: si la cámara se mueve se vuelve a medir y
las fotos nuevas salen con la corrección nueva; cada foto anota la versión que se le aplicó.
"""
from __future__ import annotations

import hashlib
import json

from PIL import Image as PILImage

CALIDAD = 95


def validar(cal: dict) -> dict:
    """{"res": [ancho, alto], "quad": [sup-izq, sup-der, inf-der, inf-izq]} en px del original."""
    try:
        res = [int(cal["res"][0]), int(cal["res"][1])]
        quad = [[float(x), float(y)] for x, y in cal["quad"]]
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise ValueError("calibración ilegible: hace falta res [ancho, alto] y quad de 4 puntos") from e
    if len(quad) != 4 or min(res) <= 0:
        raise ValueError("quad debe tener 4 esquinas y res dos dimensiones positivas")
    if any(not (0 <= x <= res[0] and 0 <= y <= res[1]) for x, y in quad):
        raise ValueError("alguna esquina cae fuera de la imagen")
    w, h = _tamano(quad)
    if w < 100 or h < 100:
        raise ValueError("el recorte saldría de menos de 100 px")
    return {"res": res, "quad": quad}


def version(cal: dict) -> str:
    return hashlib.sha1(json.dumps(validar(cal), sort_keys=True).encode()).hexdigest()[:8]


def _tamano(quad) -> tuple[int, int]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    return round(((x1 - x0) + (x2 - x3)) / 2), round(((y3 - y0) + (y2 - y1)) / 2)


def aplicar(img: PILImage.Image, cal: dict | None) -> PILImage.Image | None:
    """El recorte enderezado, o None si no hay calibración o la foto no es de esa resolución."""
    if not cal:
        return None
    cal = validar(cal)
    if list(img.size) != cal["res"]:
        return None
    sup_izq, sup_der, inf_der, inf_izq = cal["quad"]
    # PIL QUAD toma el origen en este orden: sup-izq, inf-izq, inf-der, sup-der.
    origen = [*sup_izq, *inf_izq, *inf_der, *sup_der]
    return img.transform(_tamano(cal["quad"]), PILImage.QUAD, origen,
                         resample=PILImage.BICUBIC)
