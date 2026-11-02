"""Migración de `imagen` a AUTOINCREMENT (17/10/2026): sin él SQLite reutilizaba los ids.

Se prueba sobre una BD temporal con el esquema VIEJO (INTEGER PRIMARY KEY a secas), como la de
producción: datos y detecciones intactos, índices y FK conservados, la secuencia por encima de lo
ya usado, borrar el máximo e insertar no reutiliza, y una segunda pasada no hace nada.
"""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, event

from app.core import db


def _motor(ruta):
    m = create_engine(f"sqlite:///{ruta}")

    @event.listens_for(m, "connect")
    def _fk(con, _):
        con.execute("PRAGMA foreign_keys=ON")
    return m


def _esquema_viejo(ruta):
    """El esquema de producción, simplificado pero con las columnas del modelo y una FK real."""
    from sqlalchemy.schema import CreateTable
    from app.models.models import Deteccion, Imagen
    m = _motor(ruta)
    ddl = str(CreateTable(Imagen.__table__).compile(m)).replace(" AUTOINCREMENT", "")
    assert "AUTOINCREMENT" not in ddl.upper()
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("CREATE TABLE trampa (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE tarjeta (id INTEGER PRIMARY KEY)")
    con.execute(ddl)
    con.execute(str(CreateTable(Deteccion.__table__).compile(m)))
    con.execute("CREATE INDEX ix_imagen_trampa_id ON imagen (trampa_id)")
    con.execute("CREATE INDEX ix_imagen_capturada ON imagen (capturada)")
    con.execute("INSERT INTO trampa (id) VALUES (10)")
    for i in range(1, 6):
        con.execute("INSERT INTO imagen (id, trampa_id, ruta_fichero, ruta_thumbnail, estado, num_insectos, "
                    "modelo_version, es_demo, ancho, alto, capturada) VALUES "
                    "(?, 10, ?, '', 'procesada', 1, '', 0, 10, 10, '2026-10-17 08:00:00')", (i, f"/f/{i}.jpg"))
        con.execute("INSERT INTO deteccion (imagen_id, clase, confianza, x, y, w, h) "
                    "VALUES (?, 'WF', 0.9, 0.5, 0.5, 0.1, 0.1)", (i,))
    con.commit()
    con.close()
    return m


def test_migra_conserva_todo_y_no_reutiliza(tmp_path):
    ruta = tmp_path / "vieja.db"
    m = _esquema_viejo(ruta)
    assert db._autoincremento_imagen(m) is True

    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys=ON")
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='imagen'").fetchone()[0]
    assert "AUTOINCREMENT" in sql.upper()
    assert con.execute("SELECT count(*) FROM imagen").fetchone()[0] == 5
    assert con.execute("SELECT count(*) FROM deteccion").fetchone()[0] == 5
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    # La FK de deteccion sigue apuntando a «imagen», no a un nombre temporal.
    assert {r[2] for r in con.execute("PRAGMA foreign_key_list(deteccion)")} == {"imagen"}
    indices = {r[1] for r in con.execute("PRAGMA index_list(imagen)")}
    assert {"ix_imagen_trampa_id", "ix_imagen_capturada"} <= indices
    # Por encima de lo ya usado (66), aunque el máximo actual sea 5.
    assert con.execute("SELECT seq FROM sqlite_sequence WHERE name='imagen'").fetchone()[0] == \
        db.IMAGEN_ID_MAXIMO_CONOCIDO
    # Borrar el máximo e insertar: no se reutiliza.
    con.execute("DELETE FROM deteccion WHERE imagen_id=5")
    con.execute("DELETE FROM imagen WHERE id=5")
    con.execute("INSERT INTO imagen (trampa_id, ruta_fichero, ruta_thumbnail, estado, num_insectos, modelo_version, "
                "es_demo, ancho, alto, capturada) VALUES (10, '/f/n.jpg', '', 'pendiente', 0, '', 0, 1, 1, "
                "'2026-10-17 09:00:00')")
    assert con.execute("SELECT max(id) FROM imagen").fetchone()[0] == db.IMAGEN_ID_MAXIMO_CONOCIDO + 1
    con.commit()
    con.close()

    # Segunda pasada: ya está, no toca nada.
    assert db._autoincremento_imagen(m) is False


def test_no_rebaja_una_secuencia_mayor(tmp_path):
    ruta = tmp_path / "vieja.db"
    m = _esquema_viejo(ruta)
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO imagen (id, trampa_id, ruta_fichero, ruta_thumbnail, estado, num_insectos, "
                "modelo_version, es_demo, ancho, alto, capturada) VALUES "
                "(500, 10, '/f/500.jpg', '', 'procesada', 0, '', 0, 1, 1, '2026-10-17 08:00:00')")
    con.commit()
    con.close()
    assert db._autoincremento_imagen(m) is True
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT seq FROM sqlite_sequence WHERE name='imagen'").fetchone()[0] == 500


def test_con_columnas_desconocidas_no_migra(tmp_path):
    ruta = tmp_path / "vieja.db"
    m = _esquema_viejo(ruta)
    con = sqlite3.connect(ruta)
    con.execute("ALTER TABLE imagen ADD COLUMN columna_rara TEXT")
    con.commit()
    con.close()
    assert db._autoincremento_imagen(m) is False
    con = sqlite3.connect(ruta)
    assert "AUTOINCREMENT" not in con.execute(
        "SELECT sql FROM sqlite_master WHERE name='imagen'").fetchone()[0].upper()
    assert con.execute("SELECT count(*) FROM imagen").fetchone()[0] == 5


def test_la_bd_de_las_pruebas_nace_con_autoincrement():
    con = sqlite3.connect(str(db.config.DB_PATH))
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='imagen'").fetchone()[0]
    assert "AUTOINCREMENT" in sql.upper()


def test_con_vistas_no_migra(tmp_path):
    """Hallazgo de Codex: una vista sobre imagen podría bloquear el renombrado; no se arriesga."""
    ruta = tmp_path / "vieja.db"
    m = _esquema_viejo(ruta)
    con = sqlite3.connect(ruta)
    con.execute("CREATE VIEW v_fotos AS SELECT id FROM imagen")
    con.commit()
    con.close()
    assert db._autoincremento_imagen(m) is False
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT count(*) FROM imagen").fetchone()[0] == 5
