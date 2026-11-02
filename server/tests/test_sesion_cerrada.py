# -*- coding: utf-8 -*-
"""Guardias contra el fallo que costó 111 respuestas 500 en un día.

**Qué pasó (2026-09-14).** Tres endpoints —la telemetría retenida, el relay MJPEG y el long
polling de `/live`— hablan con la base unos milisegundos y luego **esperan** hasta 120 s (el
vídeo, minutos). Con `Depends(get_session)` la sesión —y con ella una de las 15 conexiones del
pool— vivía hasta que terminaba la respuesta, así que había que llamar a `session.close()` a
mano antes de esperar. Y cerrarla deja los objetos ORM **desligados**, y **caducados** si antes
hubo un `commit()`: tocar cualquiera de ellos después es un `DetachedInstanceError` → 500.

En `telemetry.py` la regla estaba escrita en mayúsculas. Cuatro líneas más abajo estaba escrito
`trap.id`. **Un comentario no impide nada.**

**Cómo está resuelto (2026-09-15).** Esos endpoints ya **no reciben sesión**. La abre y la
cierra la función que habla con la base (`_fase_bd`, `_ficha_para_el_relay`, `_id_por_device`),
con `sesion_corta()`, y de ahí solo salen números. No hay nada que recordar.

**Qué hacen estas pruebas.** Leen el código con `ast`, no lo ejecutan. El fallo real solo saltaba
cuando el contador de datos volcaba —o sea **una de cada tantas peticiones, según el reloj**—, y
un fallo que depende del reloj no se coge ejecutando: se coge impidiéndolo.
"""
from __future__ import annotations

import ast
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "app" / "api"

# Llamadas que devuelven filas de la base. Lo que salga de una de estas es un objeto ORM.
FUENTES_ORM = {
    "get_or_register", "ensure_config", "_trap_or_404", "_trap_por_device",
    "get", "exec", "first", "all", "one", "one_or_none", "scalar",
}

# Esperas que pueden durar decenas de segundos o minutos.
ESPERAS_LARGAS = {"esperar_peticion", "esperar_frame", "sleep"}


def _nombre_llamada(nodo: ast.AST) -> str:
    """`session.get(...)` -> "get"; `traps_svc.ensure_config(...)` -> "ensure_config"."""
    if isinstance(nodo, ast.Call):
        f = nodo.func
        if isinstance(f, ast.Attribute):
            return f.attr
        if isinstance(f, ast.Name):
            return f.id
    return ""


def _funciones():
    """Todas las funciones de `app/api/`, con su fichero."""
    for fichero in sorted(API.glob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        for fn in ast.walk(arbol):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield fichero.name, fn


def _objetos_de_la_base(fn: ast.AST) -> dict[str, int]:
    """Nombres locales que contienen una fila de la base, con la línea donde se asignaron."""
    origen: dict[str, int] = {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assign) or _nombre_llamada(n.value) not in FUENTES_ORM:
            continue
        for destino in n.targets:
            objetivos = (destino.elts if isinstance(destino, (ast.Tuple, ast.List))
                         else [destino])
            for o in objetivos:
                if isinstance(o, ast.Name):
                    origen[o.id] = n.lineno
    return origen


def _linea_del_close(fn: ast.AST) -> int | None:
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "close" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "session"):
            return n.lineno
    return None


def _espera_larga(fn: ast.AST) -> tuple[int, str] | None:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _nombre_llamada(n) in ESPERAS_LARGAS:
            return n.lineno, _nombre_llamada(n)
    return None


def _recibe_sesion(fn: ast.AST) -> bool:
    return any(a.arg == "session" for a in fn.args.args)


# --------------------------------------------------------------------------------------------
# 1 · Nadie que espere puede tener una conexión del pool agarrada
# --------------------------------------------------------------------------------------------

def test_quien_espera_no_recibe_sesion():
    """La regla de ahora, y es más fuerte que «acuérdate de cerrarla».

    `Depends(get_session)` mantiene la sesión viva durante TODA la petición. El pool son 5 + 10
    de desbordamiento = **15** (`core/db.py`). Una petición retenida 60-120 s se queda con una
    de esas 15 todo ese rato, así que a partir de la nº 16 **no responde nada**: ni `/health`,
    ni la web, ni la telemetría que le dice a la trampa que emita. El servicio sigue `active` y
    el log simplemente deja de escribir. Pasó el 2026-09-12 con 20 trampas ficticias.

    Antes esto se arreglaba cerrando a mano, y `GET /traps/{id}/live` se quedó **dos días** sin
    ese cierre porque los otros dos se arreglaron de memoria. Ahora la regla no depende de la
    memoria de nadie: **el que espera no recibe sesión**.

    Esta prueba NO puede pasar en vacío: comprueba abajo que sigue habiendo funciones que
    esperan.
    """
    esperan: list[str] = []
    culpables: list[str] = []

    for fich, fn in _funciones():
        espera = _espera_larga(fn)
        if espera is None:
            continue
        esperan.append(f"{fich}:{fn.name}")
        if _recibe_sesion(fn):
            culpables.append(
                f"{fich}:{espera[0]}  {fn.name}() espera en «{espera[1]}()» "
                f"y recibe `session`: retiene una conexión del pool mientras espera")

    assert esperan, (
        "Esta guardia se ha quedado sin nada que vigilar: ya no hay ninguna función que espere "
        "en app/api/. O se ha movido el long polling a otro sitio (y hay que apuntar esta "
        "prueba allí), o alguien ha renombrado las esperas de `ESPERAS_LARGAS`. Una prueba que "
        "pasa porque no mira nada es peor que no tenerla.")

    assert not culpables, (
        "Hay peticiones que esperan agarrando una conexión del pool. Son 15 para todo el "
        "servidor: con 16 a la vez deja de responder TODO, y el servicio sigue diciendo "
        "«active».\n\n" + "\n".join(culpables)
        + "\n\nArreglo: que el endpoint NO reciba `session`, y que la parte que habla con la "
          "base abra la suya con `sesion_corta()` y devuelva números. Ver `_fase_bd` en "
          "telemetry.py, `_ficha_para_el_relay` en stream.py, `_id_por_device` en live.py.")


# --------------------------------------------------------------------------------------------
# 2 · Ninguna fila se escapa de la sesión que la creó
# --------------------------------------------------------------------------------------------

def test_ninguna_fila_sale_del_bloque_que_abrio_la_sesion():
    """Lo que sale de `sesion_corta()` son números, nunca filas.

    Fuera del `with`, la sesión está cerrada: la fila queda **desligada**, y con los atributos
    **caducados** si dentro hubo un `commit()`. Devolverla compila, pasa las pruebas normales y
    revienta en producción el día que el commit y la lectura caigan del mismo lado — que es
    exactamente lo que pasó: 111 respuestas 500, todas en la petición que sostiene el directo.

    Aquí se comprueba que ningún `return` dentro de un bloque `sesion_corta()` devuelve un
    objeto de la base a pelo. `return trap.id` vale; `return trap` no.
    """
    culpables: list[str] = []
    vigiladas = 0

    for fich, fn in _funciones():
        # SOLO quien abre su propia sesion. Un ayudante que la RECIBE puede devolver la fila
        # tranquilamente: el que llama sigue teniendo la sesion viva, asi que la fila no esta
        # desligada. Meterlos aqui daba cinco falsos positivos (`_trap_or_404`, `_trampa`...)
        # y un falso positivo enseña a ignorar la prueba, que es la peor forma de perderla.
        bloques = [n for n in ast.walk(fn) if isinstance(n, ast.With)
                   and any(_nombre_llamada(it.context_expr) == "sesion_corta"
                           for it in n.items)]
        if not bloques:
            continue
        vigiladas += 1
        orm = _objetos_de_la_base(fn)
        if not orm:
            continue
        for bloque in bloques:
          for n in ast.walk(bloque):
            if not isinstance(n, ast.Return) or n.value is None:
                continue
            devueltos = (n.value.elts if isinstance(n.value, (ast.Tuple, ast.List))
                         else [n.value])
            for v in devueltos:
                if isinstance(v, ast.Name) and v.id in orm:
                    culpables.append(
                        f"{fich}:{n.lineno}  {fn.name}() devuelve «{v.id}», que es una fila de "
                        f"la base (asignada en la línea {orm[v.id]})")

    assert vigiladas, (
        "Esta guardia se ha quedado sin nada que vigilar: ya nadie usa `sesion_corta()`. Si el "
        "patrón se ha movido, hay que apuntar la prueba al sitio nuevo. Una prueba que pasa "
        "porque no mira nada es peor que no tenerla.")

    assert not culpables, (
        "Hay funciones que sacan una fila de la base fuera del bloque que abrió la sesión. "
        "Fuera del `with` esa fila está desligada y con los atributos caducados: el primer "
        "`fila.loquesea` es un DetachedInstanceError → 500.\n\n" + "\n".join(culpables)
        + "\n\nArreglo: devolver los valores que hagan falta (`fila.id`, `fila.ip`), no la fila.")


# --------------------------------------------------------------------------------------------
# 3 · Y si alguien vuelve a cerrar a mano, que al menos no toque nada después
# --------------------------------------------------------------------------------------------

def test_ningun_atributo_de_la_base_sobrevive_a_un_cierre_a_mano():
    """Red de seguridad para el día que vuelva a aparecer un `session.close()`.

    Hoy no hay ninguno —por eso esta prueba no encuentra nada— y ese es el estado bueno. Se deja
    puesta porque el patrón vuelve solo: el 2026-09-14 se arregló así, y bastó con que una
    variable siguiera en el ámbito para que el fallo volviera cuatro líneas más abajo.
    """
    culpables: list[str] = []

    for fich, fn in _funciones():
        cierre = _linea_del_close(fn)
        if cierre is None:
            continue
        orm = _objetos_de_la_base(fn)
        for n in ast.walk(fn):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id in orm and n.lineno > cierre):
                culpables.append(
                    f"{fich}:{n.lineno}  «{n.value.id}.{n.attr}» en {fn.name}() "
                    f"— la sesión se cierra en la línea {cierre}")

    assert not culpables, (
        "Hay atributos de la base usados DESPUÉS de cerrar la sesión. Eso es un 500 esperando "
        "al reloj: solo revienta cuando un commit previo haya caducado los atributos.\n\n"
        + "\n".join(culpables)
        + "\n\nArreglo: no cerrar a mano. Sacar la parte que habla con la base a su propia "
          "función con `sesion_corta()` y devolver números.")
