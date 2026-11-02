"""Motor de base de datos (SQLite + SQLModel, con WAL)."""
from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from . import config

log = logging.getLogger("entomira.db")

# EL LIMITE DE CONEXIONES, ESCRITO.
#
# Hasta el 2026-09-15 estos dos numeros no estaban en ninguna parte del codigo: eran el valor
# de fabrica de SQLAlchemy (5 + 10). Y resulta que **es el numero que tumbo el servidor
# entero**: en el ensayo con 20 trampas ficticias, las 15 primeras se quedaron con todas las
# conexiones durante su retencion y a partir de ahi no respondio NADA — ni `/health`, ni la
# web, ni la telemetria que le dice a la trampa que emita. El servicio seguia `active` y el
# log simplemente dejaba de escribir.
#
# Un limite que decide si el sistema vive o muere no puede ser un implicito heredado. Se deja
# escrito con su porque, y aqui esta el sitio donde subirlo el dia que haga falta.
#
# No se sube hoy a proposito: el problema no era que 15 fueran pocas, era que una peticion
# retenida se quedaba con una durante 120 s. Eso ya esta arreglado soltando la sesion antes de
# esperar (`_fase_bd`, `_ficha_para_el_relay`, `_id_por_device`). Subir el pool habria tapado
# el sintoma y dejado la causa, que es justo lo que no se hace aqui.
POOL_CONEXIONES = 5
POOL_DESBORDE = 10          # techo real = POOL_CONEXIONES + POOL_DESBORDE = 15

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_size=POOL_CONEXIONES,
    max_overflow=POOL_DESBORDE,
    echo=False,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


# columnas añadidas después de la primera versión → migración aditiva (SQLite ADD COLUMN)
_MIGRATIONS = {
    "invernadero": {
        "email": "TEXT DEFAULT ''", "tipo": "TEXT DEFAULT ''",
        "cultivo": "TEXT DEFAULT ''", "notas": "TEXT DEFAULT ''",
        "client_token": "TEXT DEFAULT ''",
    },
    "trampa": {
        "linea": "INTEGER", "bateria": "INTEGER", "owner_id": "INTEGER",
        "voltaje": "REAL", "rssi": "INTEGER", "crate": "REAL",
        "fw_version": "TEXT",
        # calibracion optica de la trampa (px/mm y distancia medida a la tarjeta)
        "px_mm": "REAL", "distancia_cm": "REAL",
        "red": "TEXT", "operador": "TEXT",
        "buffer_pend": "INTEGER",
        "despertar_confirmado": "DATETIME", "despertar_origen": "TEXT",
        "despertar_informado": "DATETIME",
        "recorte_tarjeta": "TEXT",
    },
    "imagen": {
        "config_version": "INTEGER", "resolucion": "TEXT",
        "px_mm": "REAL", "tarjeta_id": "INTEGER",
        "ev": "REAL", "ruta_recorte": "TEXT", "recorte_version": "TEXT",
        "reprocesar": "BOOLEAN DEFAULT 0",
    },
    # OJO AL ANADIR AQUI ALGO QUE ESTE EN `_RETIRADAS`: se recrearia en cada arranque y la
    # conversion de escala volveria a correr sobre valores ya convertidos. Medido el
    # 2026-09-18 con estas mismas listas: al segundo arranque, `contraste` y `saturacion`
    # pasaron de 1,0 a 1,25, y al tercero habrian seguido subiendo. Las dos listas tienen
    # que ser disjuntas, y hay una prueba que lo comprueba.
    "configcamara": {
        "deep_sleep": "INTEGER DEFAULT 0", "ventana_despierto_seg": "INTEGER DEFAULT 15",
        "hora_despertar": "TEXT DEFAULT '08:00'",
        "horas_despertar": "TEXT",
        # Mandos de libcamera (2026-09-18). Ver `_RETIRADAS` para los del ESP32 que sustituyen.
        "ancho": "INTEGER DEFAULT 4608", "alto": "INTEGER DEFAULT 2592",
        "ev": "REAL DEFAULT 0", "balance": "TEXT DEFAULT 'auto'",
        "enfoque": "TEXT DEFAULT 'auto'", "lente_dioptrias": "REAL DEFAULT 0",
    },
    "confighistorial": {
        "hora_despertar": "TEXT DEFAULT '08:00'",
        "horas_despertar": "TEXT",
        "ancho": "INTEGER DEFAULT 0", "alto": "INTEGER DEFAULT 0",
        "ev": "REAL DEFAULT 0", "balance": "TEXT DEFAULT 'auto'",
        "enfoque": "TEXT DEFAULT 'auto'", "lente_dioptrias": "REAL DEFAULT 0",
    },
}


# ---------------------------------------------------------------- retirada de los mandos ESP32
#
# El cambio de hardware del 2026-09-18 dejo estas columnas sin nadie que las obedezca. No se
# dejan puestas «por si acaso»: una columna que la web ofrece y la trampa ignora es peor que
# no tenerla, porque se toca creyendo que hace algo. `gain_ceiling` y `lenc` no tienen
# equivalente en libcamera; `wb_mode`/`awb` los sustituye `balance`; `ae_level`, `ev`; y
# `resolucion` (el nombre de framesize) lo sustituyen `ancho` y `alto` en pixeles, porque
# `QSXGA` decia 2592x1944 mientras las fotos eran de 4608x2592.
_RETIRADAS = {
    "configcamara": ("resolucion", "wb_mode", "ae_level", "gain_ceiling", "lenc", "awb"),
    "confighistorial": ("resolucion", "wb_mode", "ae_level", "gain_ceiling", "lenc", "awb"),
    "imagen": ("ae_level",),
}

# LO QUE NO ES UN SIMPLE RENOMBRE. `brillo`, `contraste` y `saturacion` sobreviven con el mismo
# nombre pero **no con el mismo significado**: iban de -2 a 2 con el 0 como «no toques nada», y
# en libcamera son multiplicadores donde contraste=0 y saturacion=0 dan una imagen gris y
# plana. Copiar los valores tal cual habria dejado la camara disparando en blanco y negro sin
# que fallara nada ni lo dijera ningun log.
#
# Se convierte una sola vez, y el guardia es la columna vieja: mientras `ae_level` siga en la
# tabla, los valores son de la escala vieja; cuando se retire, esto ya no vuelve a correr.
_CONVERSIONES = {
    "configcamara": [
        "UPDATE configcamara SET ev = ae_level",
        "UPDATE configcamara SET brillo = brillo * 0.25",
        "UPDATE configcamara SET contraste = 1.0 + contraste * 0.25",
        "UPDATE configcamara SET saturacion = 1.0 + saturacion * 0.25",
        # `resolucion` era un nombre de framesize; el unico que se uso de verdad es el maximo.
        "UPDATE configcamara SET ancho = 4608, alto = 2592",
        # CALIDAD. La escala se da la vuelta: antes era 10 (mejor) .. 63 (peor), el parametro
        # del driver del ESP32; ahora es 1 (peor) .. 100 (mejor), la `-q` de `rpicam-still`.
        # Sin este UPDATE el 10 de las trampas reales — que era calidad MAXIMA — se quedaba
        # tal cual en 10/100, o sea la peor posible, sin error y sin una linea de log: las
        # fotos habrian empezado a salir basura y nadie se habria enterado hasta mirarlas.
        # No se convierte proporcionalmente: Antonio decidio el 2026-09-19 usar **siempre la
        # maxima calidad**, asi que se fija a 100 y se acabo la escala.
        "UPDATE configcamara SET calidad_jpeg = 100",
        # BALANCE. `wb_mode` era un entero del driver (0=auto, 1=soleado, 2=nublado,
        # 3=oficina, 4=casa) y `balance` es el nombre que `--awb` espera. Sin esto, la columna
        # nueva se quedaba en su valor por defecto y un «nublado» elegido a mano volvia a
        # «auto» en silencio. En la base real las tres trampas estaban en 0, asi que hoy no
        # cambia nada; se convierte igual para que no dependa de la suerte.
        "UPDATE configcamara SET balance = CASE wb_mode WHEN 1 THEN 'daylight' WHEN 2 THEN 'cloudy'"
        " WHEN 3 THEN 'fluorescent' WHEN 4 THEN 'indoor' ELSE 'auto' END",
    ],
    "confighistorial": [
        "UPDATE confighistorial SET ev = ae_level",
        "UPDATE confighistorial SET brillo = brillo * 0.25",
        "UPDATE confighistorial SET contraste = 1.0 + contraste * 0.25",
        "UPDATE confighistorial SET saturacion = 1.0 + saturacion * 0.25",
        "UPDATE confighistorial SET ancho = 4608, alto = 2592",
        # CALIDAD. La escala se da la vuelta: antes era 10 (mejor) .. 63 (peor), el parametro
        # del driver del ESP32; ahora es 1 (peor) .. 100 (mejor), la `-q` de `rpicam-still`.
        # Sin este UPDATE el 10 de las trampas reales — que era calidad MAXIMA — se quedaba
        # tal cual en 10/100, o sea la peor posible, sin error y sin una linea de log: las
        # fotos habrian empezado a salir basura y nadie se habria enterado hasta mirarlas.
        # No se convierte proporcionalmente: Antonio decidio el 2026-09-19 usar **siempre la
        # maxima calidad**, asi que se fija a 100 y se acabo la escala.
        "UPDATE confighistorial SET calidad_jpeg = 100",
        # BALANCE. `wb_mode` era un entero del driver (0=auto, 1=soleado, 2=nublado,
        # 3=oficina, 4=casa) y `balance` es el nombre que `--awb` espera. Sin esto, la columna
        # nueva se quedaba en su valor por defecto y un «nublado» elegido a mano volvia a
        # «auto» en silencio. En la base real las tres trampas estaban en 0, asi que hoy no
        # cambia nada; se convierte igual para que no dependa de la suerte.
        "UPDATE confighistorial SET balance = CASE wb_mode WHEN 1 THEN 'daylight' WHEN 2 THEN 'cloudy'"
        " WHEN 3 THEN 'fluorescent' WHEN 4 THEN 'indoor' ELSE 'auto' END",
    ],
    "imagen": ["UPDATE imagen SET ev = ae_level WHERE ae_level IS NOT NULL"],
}


# Indices que no vienen del modelo. `imagen.capturada` es el ORDER BY de la galeria: sin
# indice, cada pagina ordena la tabla entera. Con 300 imagenes no se nota; con 50.000, si.
_INDICES = [
    ("ix_imagen_capturada", "imagen", "capturada"),
    ("ix_imagen_es_demo", "imagen", "es_demo"),
    # Condiciones de captura: se consultan filtrando la galeria por resolucion y agrupando
    # por version de config, que es como se estratifica el corpus para el analisis.
    ("ix_imagen_config_version", "imagen", "config_version"),
    ("ix_imagen_resolucion", "imagen", "resolucion"),
    ("ix_confighistorial_trampa", "confighistorial", "trampa_id"),
    ("ix_imagen_tarjeta", "imagen", "tarjeta_id"),
    # La ingesta busca por (trampa, capturada) en CADA foto que entra para descartar
    # duplicados: sin indice, eso es un escaneo de la tabla entera en cada captura.
    ("ix_imagen_trampa_capturada", "imagen", "trampa_id, capturada"),
    ("ix_tarjeta_trampa", "tarjeta", "trampa_id"),
    # El contador de datos moviles se consulta por (trampa, dia) en cada peticion del
    # dispositivo: sin indice seria un escaneo por cada foto que entra.
    ("ix_consumo_trampa_dia", "consumo", "trampa_id, dia"),
]


def _crear_indices() -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        for nombre, tabla, col in _INDICES:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {nombre} ON {tabla}({col})"))


def _retirar_columnas_muertas() -> None:
    """Convierte los valores a la escala nueva y borra las columnas del ESP32.

    El orden importa y no es intercambiable: **primero se convierte y despues se borra**, y la
    presencia de la columna vieja es justo lo que dice si la conversion ya se hizo. Al reves,
    o sin ese guardia, una segunda pasada multiplicaria otra vez por 0,25 y cada arranque del
    servidor dejaria la camara un poco mas apagada.
    """
    from sqlalchemy import text
    with engine.begin() as conn:
        for tabla, columnas in _RETIRADAS.items():
            hay = {r[1] for r in conn.execute(text(f"PRAGMA table_info({tabla})"))}
            if not hay:
                continue                      # la tabla aun no existe: nada que retirar
            pendientes = [c for c in columnas if c in hay]
            if not pendientes:
                continue
            for sql in _CONVERSIONES.get(tabla, []):
                conn.execute(text(sql))
            for col in pendientes:
                # SQLite sabe DROP COLUMN desde la 3.35 (2021). Si una version vieja no
                # pudiera, es preferible dejar la columna muerta que abortar el arranque del
                # servidor entero: los datos ya estan convertidos.
                try:
                    conn.execute(text(f"ALTER TABLE {tabla} DROP COLUMN {col}"))
                except Exception as e:        # noqa: BLE001
                    log.warning("no se pudo retirar %s.%s: %s", tabla, col, e)


def _migrate() -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        for table, cols in _MIGRATIONS.items():
            existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
            if not existing:
                continue          # la tabla aun no existe: nada que migrar (igual que al retirar)
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))


# El id más alto que se sabe usado y borrado (las fotos de prueba del 17/10/2026). La secuencia
# arranca por encima para no volver a dar esos números.
IMAGEN_ID_MAXIMO_CONOCIDO = 66


def _autoincremento_imagen(motor=None) -> bool:
    """Pasa `imagen` a AUTOINCREMENT si aún no lo tiene. Devuelve True si migró.

    Procedimiento de SQLite para cambiar una tabla (crear la nueva, copiar, borrar la vieja,
    renombrar): así la FK de `deteccion` sigue apuntando a «imagen». Todo en UNA conexión, con
    las FK desactivadas antes de la transacción y reactivadas pase lo que pase. Acordado con
    Codex (17/10/2026): aborta si la tabla real tiene columnas que el modelo no conoce (copiar
    solo las comunes las perdería), recrea TODOS los índices, triggers y vistas que dependen de
    ella, comprueba las FK antes de confirmar y nunca rebaja la secuencia.
    """
    from sqlalchemy.schema import CreateTable

    from ..models.models import Imagen
    motor = motor or engine
    raw = motor.raw_connection()
    try:
        cur = raw.cursor()
        fila = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='imagen'").fetchone()
        if not fila or "AUTOINCREMENT" in (fila[0] or "").upper():
            return False
        reales = [r[1] for r in cur.execute("PRAGMA table_info(imagen)")]
        del_modelo = [c.name for c in Imagen.__table__.columns]
        extra = set(reales) - set(del_modelo)
        if extra:
            log.error("imagen tiene columnas que el modelo no conoce (%s): no se migra a "
                      "AUTOINCREMENT", ", ".join(sorted(extra)))
            return False
        # Una vista que consulte `imagen` no figura con tbl_name='imagen' y podría impedir el
        # renombrado (hallazgo de Codex). Aquí no hay ninguna (comprobado en producción): si
        # algún día aparece, no se migra en vez de arriesgar el arranque.
        if cur.execute("SELECT count(*) FROM sqlite_master WHERE type='view'").fetchone()[0]:
            log.error("hay vistas en la BD: no se migra imagen a AUTOINCREMENT")
            return False
        comunes = ", ".join(c for c in reales if c in del_modelo)
        dependientes = [r[0] for r in cur.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='imagen' AND type IN ('index','trigger','view') "
            "AND sql IS NOT NULL")]
        ddl = str(CreateTable(Imagen.__table__).compile(motor)).strip()
        assert ddl.upper().startswith("CREATE TABLE IMAGEN"), ddl[:60]
        ddl = "CREATE TABLE imagen_nueva" + ddl[len("CREATE TABLE imagen"):]
        cur.execute("PRAGMA foreign_keys=OFF")
        assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        try:
            cur.execute("BEGIN IMMEDIATE")
            n_antes = cur.execute("SELECT count(*) FROM imagen").fetchone()[0]
            cur.execute(ddl)
            cur.execute(f"INSERT INTO imagen_nueva ({comunes}) SELECT {comunes} FROM imagen")
            cur.execute("DROP TABLE imagen")
            cur.execute("ALTER TABLE imagen_nueva RENAME TO imagen")
            for sql in dependientes:
                cur.execute(sql)
            maximo = cur.execute("SELECT coalesce(max(id), 0) FROM imagen").fetchone()[0]
            previa = cur.execute("SELECT seq FROM sqlite_sequence WHERE name='imagen'").fetchone()
            seq = max(maximo, IMAGEN_ID_MAXIMO_CONOCIDO, previa[0] if previa else 0)
            cur.execute("DELETE FROM sqlite_sequence WHERE name='imagen'")
            cur.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('imagen', ?)", (seq,))
            n_despues = cur.execute("SELECT count(*) FROM imagen").fetchone()[0]
            fallos = cur.execute("PRAGMA foreign_key_check").fetchall()
            if n_despues != n_antes or fallos:
                raise RuntimeError(f"migración incoherente: {n_antes}->{n_despues} filas, FK {fallos[:3]}")
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            cur.execute("PRAGMA foreign_keys=ON")
        log.warning("imagen migrada a AUTOINCREMENT: %s filas, secuencia en %s", n_despues, seq)
        return True
    finally:
        raw.close()


def init_db() -> None:
    # importa los modelos para que se registren en metadata
    from ..models import models  # noqa: F401
    SQLModel.metadata.create_all(engine)
    _migrate()
    # Despues de `_migrate()` y no antes: la conversion escribe en las columnas nuevas, que
    # acaban de crearse ahi.
    _retirar_columnas_muertas()
    _autoincremento_imagen()
    _crear_indices()
    _recontar_sin_auxiliares()
    _seed_demo_user()


def _recontar_sin_auxiliares(motor=None) -> int:
    """`num_insectos` pasa a contar solo las clases gestionadas (ver `services/clases.py`).

    Idempotente: solo toca las fotos procesadas CON detecciones cuyo número no cuadra. Las
    detecciones no se borran, así que es reversible. Una foto procesada sin ninguna detección
    se deja como está (su cero, o el número que trajo, no depende de las clases).
    """
    from sqlalchemy import text

    from ..services.clases import GESTIONADAS
    marcas = ",".join(f"'{c}'" for c in GESTIONADAS)
    cuenta = (f"(SELECT COUNT(*) FROM deteccion d WHERE d.imagen_id = imagen.id "
              f"AND d.clase IN ({marcas}))")
    with (motor or engine).begin() as conn:
        res = conn.execute(text(
            f"UPDATE imagen SET num_insectos = {cuenta} WHERE estado = 'procesada' "
            f"AND EXISTS (SELECT 1 FROM deteccion d WHERE d.imagen_id = imagen.id) "
            f"AND num_insectos <> {cuenta}"))
        n = res.rowcount or 0
    if n:
        log.warning("num_insectos recontado sin fauna auxiliar en %d foto(s)", n)
    return n


def _seed_demo_user() -> None:
    """Crea un agricultor de demostración ligado al primer invernadero (si no existe).

    Solo en modo local: fuera de él, una cuenta con contraseña conocida sería una puerta abierta.
    """
    from sqlmodel import select

    from . import config
    if not config.MODO_LOCAL:
        return

    from ..models.models import Invernadero, Usuario
    from . import passwords
    with Session(engine) as s:
        exists = s.exec(select(Usuario).where(Usuario.username == "agricultor")).first()
        if exists:
            return
        gh = s.exec(select(Invernadero).order_by(Invernadero.id)).first()
        s.add(Usuario(
            username="agricultor",
            password_hash=passwords.hash_password("agricultor"),
            rol="agricultor",
            invernadero_id=gh.id if gh else None,
            nombre="Agricultor de demostración",
        ))
        s.commit()


def get_session():
    """Sesion por peticion, para los endpoints que NO esperan.

    Ojo: la sesion —y con ella **una conexion del pool**— vive hasta que la respuesta termina.
    Para una peticion normal eso son milisegundos. Para una retenida son 120 s, y para un MJPEG
    son minutos: ahi hay que usar `sesion_corta()` en vez de esto.
    """
    with Session(engine) as session:
        yield session


@contextmanager
def sesion_corta():
    """Una sesion que dura lo que dure el bloque, para las peticiones que despues ESPERAN.

    Existe por un fallo que costo 111 respuestas 500 en un dia (gotcha G15). Tres endpoints
    —la telemetria retenida, el relay MJPEG y el long polling de `/live`— hablan con la base
    unos milisegundos y luego esperan hasta 120 s. Con `Depends(get_session)` la conexion se
    quedaba agarrada toda la espera, asi que habia que acordarse de llamar a `session.close()`
    a mano… y acordarse no basta: el dia que se escribio esa regla en un comentario en
    mayusculas, cuatro lineas mas abajo ya estaba incumplida.

    Con esto no hay nada que recordar. La sesion se cierra al salir del `with`, y los objetos
    ORM no salen de ahi porque el bloque devuelve numeros:

        datos = leer_lo_que_haga_falta()   # usa sesion_corta() por dentro
        await esperar(...)                 # aqui ya no hay ni sesion ni filas

    Lo que **no** se puede hacer es devolver una fila desde dentro del bloque: fuera estaria
    desligada, y con los atributos caducados si hubo un commit. Devolver enteros y cadenas.
    """
    with Session(engine) as session:
        yield session
