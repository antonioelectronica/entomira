# -*- coding: utf-8 -*-
"""Guardias de la migración a la config de cámara nativa (2026-09-18).

**Por qué hace falta una prueba para esto.** La migración cambió el SIGNIFICADO de tres
columnas que conservan el nombre: `brillo`, `contraste` y `saturacion` iban de -2 a 2 con el 0
como «no toques nada», y en libcamera son multiplicadores donde contraste=0 y saturacion=0 dan
una imagen gris y plana. La conversión, por tanto, no es un renombrado sino un `UPDATE`, y un
`UPDATE` que se repita **estropea un poco más la cámara en cada arranque del servidor** sin
que falle nada ni lo diga ningún log.

Y pasó de verdad: en la primera prueba, `contraste` subió de 1,0 a 1,25 en el segundo arranque
porque las columnas retiradas seguían en la lista de las que se añaden, así que se recreaban y
la conversión volvía a correr. Eso es lo que vigilan estas dos pruebas.

Desde el 2026-09-19 vigilan además las dos conversiones que faltaban y que se descubrieron
contra la base de producción: `calidad_jpeg` (la escala se da la vuelta — el 10 de las
trampas era la calidad MÁXIMA y sin convertir se queda en 10/100, la peor) y `balance`
(el `wb_mode` entero del driver pasa al nombre que espera `--awb`).
"""
from __future__ import annotations

import sqlite3

from app.core import db as db_mod


def test_las_listas_de_anadir_y_retirar_son_disjuntas():
    """Una columna no puede estar a la vez en «añádela» y en «retírala».

    Si lo está, cada arranque la recrea, la conversión la vuelve a aplicar y los valores se
    desplazan. Es exactamente el fallo que se midió.
    """
    for tabla, retiradas in db_mod._RETIRADAS.items():
        se_anaden = set(db_mod._MIGRATIONS.get(tabla, {}))
        choque = se_anaden & set(retiradas)
        assert not choque, (
            f"{tabla}: {sorted(choque)} se recrean en cada arranque y se vuelven a convertir")


def test_la_conversion_no_se_repite(tmp_path, monkeypatch):
    """Tres arranques seguidos dejan la base exactamente igual que uno.

    Se parte de una base con el esquema VIEJO (el del ESP32) y valores en la escala vieja, que
    es la situación real de cualquier instalación que venga de antes del cambio.
    """
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE trampa (id INTEGER PRIMARY KEY, device_id TEXT);
        CREATE TABLE configcamara (
            trampa_id INTEGER PRIMARY KEY, intervalo_seg INTEGER, brillo INTEGER,
            contraste INTEGER, saturacion INTEGER, resolucion TEXT, calidad_jpeg INTEGER,
            version INTEGER, actualizado TEXT, wb_mode INTEGER, ae_level INTEGER,
            gain_ceiling INTEGER, lenc INTEGER, awb INTEGER, deep_sleep INTEGER,
            ventana_despierto_seg INTEGER);
        INSERT INTO trampa VALUES (1, 'vieja-esp32');
        -- contraste 0 y saturacion 0 en la escala VIEJA significan «sin tocar»
        INSERT INTO configcamara VALUES
            (1, 600, 0, 0, 0, 'QSXGA', 10, 4, '2026-09-01', 2, -1, 2, 1, 1, 1, 15);
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(db_mod.config, "DB_PATH", ruta, raising=False)
    motor = db_mod.create_engine(f"sqlite:///{ruta}")
    monkeypatch.setattr(db_mod, "engine", motor)

    def valores():
        c = sqlite3.connect(ruta)
        fila = c.execute("SELECT brillo, contraste, saturacion, ev, ancho, alto,"
                         " calidad_jpeg, balance FROM configcamara WHERE trampa_id=1"
                         ).fetchone()
        c.close()
        return fila

    db_mod._migrate()
    db_mod._retirar_columnas_muertas()
    uno = valores()
    for _ in range(2):
        db_mod._migrate()
        db_mod._retirar_columnas_muertas()

    assert valores() == uno, "la conversión se ha vuelto a aplicar en un arranque posterior"
    # Y que la conversión signifique lo que debe: el «sin tocar» viejo (0) es el «sin tocar»
    # nuevo (1.0), no un gris plano.
    assert uno[1] == 1.0 and uno[2] == 1.0, f"contraste/saturación mal convertidos: {uno}"
    assert uno[3] == -1, f"la exposición no se ha trasladado a `ev`: {uno}"
    # CALIDAD: el 10 viejo era la calidad MAXIMA y la escala se da la vuelta. Si este assert
    # cae, las trampas reales se van a 10/100 y las fotos salen basura sin que falle nada.
    assert uno[6] == 100, f"la calidad no se ha convertido a la escala nueva: {uno}"
    # BALANCE: wb_mode=2 era «nublado». Sin el CASE se queda en el defecto y se pierde.
    assert uno[7] == "cloudy", f"el balance de blancos no se ha traducido: {uno}"

    c = sqlite3.connect(ruta)
    cols = {r[1] for r in c.execute("PRAGMA table_info(configcamara)")}
    c.close()
    assert not cols & {"resolucion", "wb_mode", "ae_level", "gain_ceiling", "lenc", "awb"}
