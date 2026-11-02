"""Disco externo del servidor (HDD exFAT de 150 GB): backup por invernadero y espejo diario.

Pedido por Antonio el 2026-10-23. El disco va siempre conectado; solo se desenchufa para recuperar
algo tras un fallo gordo, y antes se EXPULSA desde la web (o se apaga el servidor desde la web).

exFAT no tiene diario, así que aquí:
  - nada se escribe si el punto de montaje no está montado (si no, se escribiría en la SD);
  - todo fichero se escribe como `.part`, con fsync, y solo al final se renombra;
  - el pack se VERIFICA releyéndolo entero (sha256 de cada fichero) antes de darlo por bueno;
  - mientras se escribe se tiene cogido `data/disco.lock`: el helper root no expulsa con él cogido.

Montar, expulsar y apagar necesitan root: van por `sudo` a dos scripts fijos
(`deploy/pi/entomira-disco`, `deploy/pi/entomira-apagar`), sin argumentos libres.

Usos del disco:
  ENTOMIRA/backups/invernadero_<id>_<nombre>/<desde>_<hasta>.zip   pack de campaña
  ENTOMIRA/espejo/images/...   copia de las fotos (solo se añade; nunca se borra nada)
  ENTOMIRA/espejo/bd/entomira_<fecha>.db   copia diaria de la BD (se guardan las 30 últimas)
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..core import config

PUNTO = Path(os.environ.get("ENTOMIRA_DISCO", "/mnt/entomira_disco"))
RAIZ = "ENTOMIRA"
DATA = Path(config.DATA_DIR)
CERROJO = DATA / "disco.lock"
HELPER = "/usr/local/sbin/entomira-disco"
APAGAR = "/usr/local/sbin/entomira-apagar"
COPIAS_BD = 30

_estado_backup: dict = {"en_curso": False}
_hilo_lock = threading.Lock()


class DiscoError(RuntimeError):
    pass


# --------------------------------------------------------------------------- estado
def montado() -> bool:
    try:
        return os.path.ismount(PUNTO)
    except OSError:
        return False


def raiz() -> Path:
    if not montado():
        raise DiscoError("El disco externo no está montado.")
    r = PUNTO / RAIZ
    r.mkdir(exist_ok=True)
    return r


def estado() -> dict:
    out = {"montado": montado(), "punto": str(PUNTO), "backup": dict(_estado_backup)}
    if out["montado"]:
        try:
            du = shutil.disk_usage(PUNTO)
            out.update(total_gb=round(du.total / 1e9, 1), libre_gb=round(du.free / 1e9, 1),
                       usado_pct=round(du.used / du.total * 100, 1) if du.total else None)
            out["packs"] = packs()
            out["espejo"] = _leer_json(PUNTO / RAIZ / "espejo" / "ultimo.json")
        except OSError as e:
            out["error"] = str(e)
    return out


def _leer_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def packs() -> list[dict]:
    base = PUNTO / RAIZ / "backups"
    if not base.exists():
        return []
    out = []
    for z in sorted(base.glob("*/*.zip")):
        st = z.stat()
        out.append({"invernadero": z.parent.name, "fichero": z.name,
                    "mb": round(st.st_size / 1e6, 1),
                    "fecha": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


# --------------------------------------------------------------------------- root (sudo)
def _sudo(*args: str, timeout: int = 120) -> str:
    r = subprocess.run(["sudo", "-n", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise DiscoError((r.stderr or r.stdout).strip() or f"código {r.returncode}")
    return r.stdout.strip()


def montar() -> str:
    return _sudo(HELPER, "montar", timeout=600)      # fsck de 150 GB puede tardar


def expulsar() -> str:
    if _estado_backup.get("en_curso"):
        raise DiscoError("Hay una copia en curso: espera a que termine.")
    return _sudo(HELPER, "expulsar")


def apagar_servidor() -> str:
    if _estado_backup.get("en_curso"):
        raise DiscoError("Hay una copia en curso: espera a que termine.")
    return _sudo(APAGAR)


# --------------------------------------------------------------------------- cerrojo
@contextlib.contextmanager
def cerrojo():
    """Exclusivo entre procesos (API y espejo) y visible para el helper root (flock)."""
    CERROJO.parent.mkdir(parents=True, exist_ok=True)
    f = open(CERROJO, "a+")
    try:
        try:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DiscoError("Ya hay otra copia escribiendo en el disco.")
        except ImportError:                      # Windows (pruebas): basta con el del hilo
            pass
        yield
    finally:
        f.close()


# --------------------------------------------------------------------------- escritura segura
def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _copiar_seguro(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    with open(src, "rb") as a, open(tmp, "wb") as b:
        shutil.copyfileobj(a, b, 1 << 20)
        b.flush()
        os.fsync(b.fileno())
    os.replace(tmp, dst)


def _nombre(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s or "").strip("_")[:40] or "sin_nombre"


# --------------------------------------------------------------------------- pack por invernadero
def _filas(con, sql, args=()) -> tuple[list[str], list[tuple]]:
    cur = con.execute(sql, args)
    return [d[0] for d in cur.description], cur.fetchall()


def _csv(cols, filas) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(filas)
    return buf.getvalue().encode("utf-8")


def crear_pack(db_path: Path, invernadero_id: int, desde: str | None = None,
               hasta: str | None = None, destino: Path | None = None) -> dict:
    """Un ZIP con TODO lo de las trampas de ese invernadero entre `desde` y `hasta` (UTC).

    Lee la BD en solo lectura. Las fotos van sin recomprimir (ZIP_STORED). Se verifica antes de
    publicarlo. `destino` solo para pruebas; en el servidor es el disco montado.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        gh = con.execute("SELECT id, nombre FROM invernadero WHERE id = ?",
                         (invernadero_id,)).fetchone()
        if not gh:
            raise DiscoError("Invernadero no encontrado.")
        trampas = [r[0] for r in con.execute(
            "SELECT id FROM trampa WHERE invernadero_id = ? ORDER BY id", (invernadero_id,))]
        if not trampas:
            raise DiscoError("Ese invernadero no tiene trampas.")
        marcas = ",".join("?" * len(trampas))
        cond, args = f"trampa_id IN ({marcas}) AND es_demo = 0", list(trampas)
        if desde:
            cond += " AND capturada >= ?"; args.append(desde)
        if hasta:
            cond += " AND capturada <= ?"; args.append(hasta)
        cols_i, imgs = _filas(con, f"SELECT * FROM imagen WHERE {cond} ORDER BY capturada", args)
        if not imgs:
            raise DiscoError("No hay fotos de ese invernadero en ese periodo.")
        ci = {c: k for k, c in enumerate(cols_i)}
        ids = [r[ci["id"]] for r in imgs]
        capt = sorted(str(r[ci["capturada"]]) for r in imgs)
        d0, d1 = capt[0][:10], capt[-1][:10]
        cols_g, fila_g = _filas(con, "SELECT * FROM invernadero WHERE id = ?", (invernadero_id,))
        sin_token = [k for k, c in enumerate(cols_g) if c != "client_token"]   # es una llave
        tablas = {
            "invernadero.csv": ([cols_g[k] for k in sin_token],
                                [tuple(f[k] for k in sin_token) for f in fila_g]),
            "trampas.csv": _filas(con, f"SELECT * FROM trampa WHERE id IN ({marcas})", trampas),
            "imagenes.csv": (cols_i, imgs),
            "tarjetas.csv": _filas(con, f"SELECT * FROM tarjeta WHERE trampa_id IN ({marcas})", trampas),
        }
        det = ([], [])
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            c, f = _filas(con, f"SELECT * FROM deteccion WHERE imagen_id IN "
                               f"({','.join('?' * len(lote))})", lote)
            det = (c, det[1] + f)
        tablas["detecciones.csv"] = det
        for t in ("lecturahardware", "lecturaconsumo"):
            try:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
                if "trampa_id" in cols:
                    tablas[f"{t}.csv"] = _filas(
                        con, f"SELECT * FROM {t} WHERE trampa_id IN ({marcas})", trampas)
            except sqlite3.Error:
                pass
    finally:
        con.close()

    base = destino or (raiz() / "backups")
    carpeta = base / f"invernadero_{invernadero_id}_{_nombre(gh[1])}"
    carpeta.mkdir(parents=True, exist_ok=True)
    final = carpeta / f"{d0}_{d1}.zip"
    tmp = final.with_name(final.name + ".part")
    manifiesto = {"invernadero": {"id": gh[0], "nombre": gh[1]}, "trampas": trampas,
                  "desde": desde or capt[0], "hasta": hasta or capt[-1],
                  "creado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "fotos": len(imgs), "ficheros": {}, "faltan": []}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for nombre, (cols, filas) in tablas.items():
            datos = _csv(cols, filas)
            z.writestr(f"datos/{nombre}", datos)
            manifiesto["ficheros"][f"datos/{nombre}"] = hashlib.sha256(datos).hexdigest()
        for r in imgs:
            tid = r[ci["trampa_id"]]
            for campo in ("ruta_fichero", "ruta_recorte"):
                ruta = r[ci[campo]] if campo in ci else None
                if not ruta:
                    continue
                p = Path(ruta)
                arc = f"trampa_{tid}/{'originales' if campo == 'ruta_fichero' else 'recortes'}/" \
                      f"{r[ci['id']]}_{p.name}"
                if not p.exists():
                    manifiesto["faltan"].append(str(p))
                    continue
                manifiesto["ficheros"][arc] = _sha256(p)
                z.write(p, arc)
        z.writestr("manifest.json", json.dumps(manifiesto, ensure_ascii=False, indent=1))
    with open(tmp, "rb+") as f:
        os.fsync(f.fileno())
    verificar_pack(tmp)
    os.replace(tmp, final)
    _fsync_dir(carpeta)
    return {"fichero": str(final), "fotos": len(imgs), "ficheros": len(manifiesto["ficheros"]),
            "faltan": len(manifiesto["faltan"]), "mb": round(final.stat().st_size / 1e6, 1)}


def verificar_pack(zip_path: Path) -> int:
    """Relee el ZIP del disco y comprueba el sha256 de cada fichero contra el manifiesto."""
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip() is not None:
            raise DiscoError("El pack está dañado (CRC).")
        m = json.loads(z.read("manifest.json"))
        for nombre, h in m["ficheros"].items():
            hh = hashlib.sha256()
            with z.open(nombre) as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    hh.update(b)
            if hh.hexdigest() != h:
                raise DiscoError(f"{nombre} no coincide con el original.")
    return len(m["ficheros"])


def lanzar_pack(db_path: Path, invernadero_id: int, desde=None, hasta=None) -> None:
    """En un hilo: la petición HTTP no espera a copiar cientos de MB."""
    with _hilo_lock:
        if _estado_backup.get("en_curso"):
            raise DiscoError("Ya hay una copia en curso.")
        raiz()                                    # falla ya si no está montado
        _estado_backup.clear()
        _estado_backup.update(en_curso=True, invernadero_id=invernadero_id,
                              inicio=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def trabajo():
        try:
            with cerrojo():
                r = crear_pack(db_path, invernadero_id, desde, hasta)
            _estado_backup.update(resultado=r, error=None)
        except Exception as e:                    # se informa en /disco, no se pierde
            _estado_backup.update(resultado=None, error=f"{type(e).__name__}: {e}")
        finally:
            _estado_backup.update(en_curso=False,
                                  fin=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    threading.Thread(target=trabajo, daemon=True, name="pack-invernadero").start()


# --------------------------------------------------------------------------- espejo diario
def espejo(db_path: Path, images: Path) -> dict:
    """Añade al disco las fotos nuevas y una copia coherente de la BD. Nunca borra fotos."""
    r = raiz()
    dst_img = r / "espejo" / "images"
    nuevas = ya = 0
    with cerrojo():
        for src in images.rglob("*"):
            if not src.is_file() or src.name.endswith((".part", ".tmp")):
                continue
            dst = dst_img / src.relative_to(images)
            st = src.stat()
            if dst.exists() and dst.stat().st_size == st.st_size:
                ya += 1
                continue
            _copiar_seguro(src, dst)
            nuevas += 1
        bd = r / "espejo" / "bd"
        bd.mkdir(parents=True, exist_ok=True)
        sello = datetime.now().strftime("%Y%m%d_%H%M")
        local = DATA / f".espejo_{sello}.db"
        # `with sqlite3.connect()` NO cierra la conexión: se cierran a mano.
        a, b = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True), sqlite3.connect(local)
        try:
            a.backup(b)                           # copia coherente aunque la API esté escribiendo
        finally:
            a.close()
            b.close()
        try:
            _copiar_seguro(local, bd / f"entomira_{sello}.db")
        finally:
            local.unlink(missing_ok=True)
        copias = sorted(bd.glob("entomira_*.db"))
        for viejo in copias[:-COPIAS_BD]:
            viejo.unlink()
        resumen = {"fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "fotos_nuevas": nuevas, "fotos_ya": ya, "bd": f"entomira_{sello}.db"}
        p = r / "espejo" / "ultimo.json"
        tmp = p.with_name(p.name + ".part")
        tmp.write_text(json.dumps(resumen), encoding="utf-8")
        os.replace(tmp, p)
        if hasattr(os, "sync"):
            os.sync()
    return resumen
