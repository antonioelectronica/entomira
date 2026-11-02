"""
Condiciones de captura: con qué se tomó cada foto.

**Por qué existe este módulo.** El hallazgo central del proyecto es que el tamaño aparente
del objetivo —y por tanto la resolución, la distancia y la exposición— fija el techo de
precisión del detector. Hasta ahora la tabla `imagen` guardaba `ancho`, `alto` y el conteo,
pero **ninguna condición de captura**: cada foto que entraba perdía para siempre el contexto
que explica su resultado, y el corpus que se está acumulando no se iba a poder estratificar
por lo único que de verdad lo explica.

**Cómo se resuelve.** Dos piezas complementarias:

1. `instantanea()` congela la configuración de cámara **cada vez que cambia de versión**.
   Es barato (una fila por cambio, no por foto) y guarda los catorce parámetros.
2. La `Imagen` guarda la **versión que reportó la placa**, no la que tenga el servidor: entre
   que se edita la configuración y la placa la aplica pasan hasta un intervalo entero, y la
   que vale es la que se usó de verdad. Junto a ella se copian resolución y exposición
   desnormalizadas para poder filtrar la galería sin un JOIN.

`px_mm` viene de la trampa, no de la config: depende de la geometría del brazo, y es el dato
que convierte un conteo en una medida comparable entre trampas.
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..models.models import ConfigCamara, ConfigHistorial, Imagen, Trampa

# Los campos que se congelan. Están explícitos, y no sacados de la tabla por reflexión,
# para que añadir un ajuste al sensor sea una decisión consciente: si mañana aparece un
# parámetro nuevo hay que decidir si forma parte de las condiciones de captura o no.
CAMPOS = ("ancho", "alto", "calidad_jpeg", "brillo", "contraste", "saturacion", "ev",
          "balance", "enfoque", "lente_dioptrias", "intervalo_seg", "deep_sleep",
          "hora_despertar", "horas_despertar",
          "ventana_despierto_seg")


def instantanea(session: Session, cfg: ConfigCamara) -> ConfigHistorial:
    """Congela `cfg` en el histórico. Idempotente: una fila por (trampa, versión)."""
    ya = session.exec(
        select(ConfigHistorial).where(ConfigHistorial.trampa_id == cfg.trampa_id,
                                      ConfigHistorial.version == cfg.version)).first()
    if ya:
        return ya
    h = ConfigHistorial(trampa_id=cfg.trampa_id, version=cfg.version,
                        **{c: getattr(cfg, c) for c in CAMPOS})
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


def nombre_resolucion(ancho: int, alto: int) -> str:
    """La resolución de una foto, en píxeles y sin apodos.

    **La resolución NO se copia de la configuración del servidor: se deduce de los píxeles de
    la foto.** Motivo medido en la Pi: la placa puede estar disparando con una configuración
    distinta de la que el servidor cree (arranque en frío, config no aplicada todavía), y
    copiar la del servidor habría escrito una condición FALSA en la imagen — peor que no
    escribir ninguna.

    Antes esto devolvía nombres de framesize del driver del ESP32 (`SVGA`, `QSXGA`...). Se
    quitaron con la cámara que los usaba: `QSXGA` significaba 2592x1944 y las fotos de la
    Camera Module 3 son de 4608x2592, así que el apodo decía justo lo contrario que los
    píxeles que describía.
    """
    return f"{ancho}x{alto}"


def anotar_imagen(session: Session, img: Imagen, cfg: ConfigCamara | None,
                  trap: Trampa | None, version_placa: int | None) -> None:
    """Rellena en la imagen las condiciones con que se tomó. Se llama en la ingesta.

    `version_placa` es la que reporta la PCB. Si no la manda (fotos del móvil, o un firmware
    viejo) se deja a None: es preferible un hueco honesto a atribuirle la config del servidor,
    que puede no ser la que se usó.

    La exposición se copia solo si esa versión existe en el histórico, es decir, solo si de
    verdad se sabe con qué se disparó. La resolución sale de los píxeles de la foto y por eso
    es siempre fiable.
    """
    img.config_version = version_placa
    img.resolucion = nombre_resolucion(img.ancho, img.alto)
    if version_placa is not None:
        h = session.exec(
            select(ConfigHistorial).where(ConfigHistorial.trampa_id == img.trampa_id,
                                          ConfigHistorial.version == version_placa)).first()
        if h:
            img.ev = h.ev
        elif cfg and cfg.version == version_placa:
            img.ev = cfg.ev
    if trap is not None:
        img.px_mm = trap.px_mm


def condiciones_de(session: Session, img: Imagen) -> dict | None:
    """Reconstruye las condiciones completas de una foto, o None si no se guardaron.

    Busca en el histórico la versión que la placa reportó. Si esa versión no está registrada
    (fotos anteriores a este cambio), devuelve lo poco que sí se copió en la imagen, marcado
    como `completa: False` para que nadie lo confunda con un registro fiable.
    """
    if img.config_version is None and img.resolucion is None:
        return None
    base = {"config_version": img.config_version, "resolucion": img.resolucion,
            "ev": img.ev, "px_mm": img.px_mm, "completa": False}
    if img.config_version is None:
        return base
    h = session.exec(
        select(ConfigHistorial).where(ConfigHistorial.trampa_id == img.trampa_id,
                                      ConfigHistorial.version == img.config_version)).first()
    if not h:
        return base
    base.update({c: getattr(h, c) for c in CAMPOS})
    # La resolución de la imagen manda sobre la del histórico: si la placa disparó con otra
    # cosa, los píxeles no mienten y la configuración sí puede.
    base["resolucion"] = img.resolucion or nombre_resolucion(h.ancho, h.alto)
    base["completa"] = True
    return base
