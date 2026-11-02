# -*- coding: utf-8 -*-
"""App de entrenamiento del modelo nuevo de ENTOMIRA (mosca blanca + trips), sin depender de nadie.

Pedido por Antonio el 2026-10-23. Diseño acordado con Codex (consulta del mismo día).

Qué hace:
  1. Junta el dataset original de bichosi (`bichosi_trmask_20261016`: partición por grupos, con
     los trips tapados) con las fotos propias etiquetadas (`entomira_campo`).
  2. Modelo de DOS clases: WF=0, TR=1. Macrolophus y Nesidiocoris de bichosi pasan a fondo (el
     sistema ya no los gestiona). Los datasets originales NO se tocan: se convierten a una copia
     con enlaces duros (no ocupan disco) y etiquetas reescritas.
  3. TEST CIEGO DINÁMICO (regla de Antonio): la foto propia etiquetada MÁS RECIENTE (por fecha de
     captura) queda FUERA del entrenamiento. Si desde el último entrenamiento se ha etiquetado
     otra más reciente, la que fue test la otra vez vuelve sola a train.
  4. Las fotos propias se repiten k veces en train para que pesen frente a ~5900 de bichosi.
  5. Al acabar copia best.pt a C:\\ento_ml\\models\\entomira\\nuevo_<fecha>\\ y lo apunta en
     C:\\ento_ml\\entrenos\\estado.json, que leen el evaluador y el etiquetador (Modelo 2).
     El worker de producción NO cambia de modelo: eso es otro paso, a mano.

Límite que hay que tener presente (Codex): las tarjetas ACUMULAN, así que la foto anterior de la
misma tarjeta comparte casi todos los insectos con la de test. El test ciego mide sobre todo
«¿ve lo que ya ha visto en otra foto?». La casilla «excluir también las del mismo día y trampa»
lo atenúa un poco; la medida limpia exige tarjetas enteras fuera de train.

    C:\\ento_ml\\venv\\Scripts\\python.exe scripts\\entrenar_modelo.py          # la ventana
    C:\\ento_ml\\venv\\Scripts\\python.exe scripts\\entrenar_modelo.py --plan   # solo ver el plan
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entomira_ia as ia  # noqa: E402

PYTHON = Path(r"C:\ento_ml\venv\Scripts\python.exe")
BASE_CONVERTIDA = ia.ML / "datasets" / "bichosi_wftr_20261023"
CERROJO = ia.ENTRENOS / "entrenando.json"
PESOS = {"yolo26m.pt (mediano: aprovecha la GPU)": str(ia.ML / "yolo26m.pt"),
         "último modelo entrenado (afinar)": "ultimo",
         "yolo11n.pt (nano, punto de partida limpio)": str(ia.ML / "yolo11n.pt")}
MAPA_CAMPO = {2: 0, 3: 1}           # MR, NC, WF, TR -> WF=0, TR=1 (MR/NC fuera)
MAPA_BICHOSI = {2: 0, 3: 1}         # bichosi_trmask usa el mismo orden de 4 clases

# Teselas (acordado con Codex el 2026-10-24). A 1280 px y lote 2 la GPU iba al 30-40 %: unas
# pocas fotos con miles de cajas (3686 en una de bichosi, ~600 en las t10_9x) disparan la
# memoria del asignador de Ultralytics (lote × cajas × anclas). Se reescala cada foto a lado
# largo = imgsz, EXACTAMENTE como hace Ultralytics al cargarla (los insectos quedan con los
# mismos píxeles que antes), y se corta en teselas con solape. Se entrena a tamaño de tesela y
# se infiere a la foto entera a imgsz: el tamaño de inferencia va en inferencia.json, al lado
# del best.pt, porque Ultralytics infiere por defecto al imgsz de entrenamiento.
TESELA_SOLAPE = 128                 # px (a escala de entrenamiento); un insecto mide ~15-40
TESELA_AREA_MIN = 0.5               # caja cortada: se queda si conserva >= 50 % de su área
TESELA_VACIAS = 0.15                # fracción de teselas sin cajas que se conserva en train
BASE_TESELAS = ia.ML / "datasets" / "bichosi_wftr_teselas_20261024"


# --------------------------------------------------------------------------- fotos propias
def capturas_conocidas() -> dict[str, dict]:
    """foto -> origen (trampa, imagen_id, capturada) de los manifiestos del etiquetador."""
    out = {}
    if ia.CARPETA_ETIQUETADO.exists():
        for m in ia.CARPETA_ETIQUETADO.glob("*/manifest.json"):
            try:
                datos = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for foto, d in datos.items():
                if d.get("origen"):
                    out[foto] = d["origen"]
    return out


def fotos_propias() -> list[dict]:
    """Las fotos del dataset propio que tienen alguna caja de WF o TR."""
    ds = ia.DATASET_CAMPO
    grupos = {}
    if (ds / "grupos.csv").exists():
        with open(ds / "grupos.csv", encoding="utf-8", newline="") as f:
            grupos = {r["foto"]: r for r in csv.DictReader(f)}
    origen = capturas_conocidas()
    out = []
    for img in sorted((ds / "images").glob("*.jpg")):
        lab = ds / "labels" / (img.stem + ".txt")
        lineas = [x.split() for x in lab.read_text(encoding="utf-8").splitlines()] if lab.exists() else []
        n = {"WF": 0, "TR": 0}
        for p in lineas:
            if p and int(p[0]) in MAPA_CAMPO:
                n[["WF", "TR"][MAPA_CAMPO[int(p[0])]]] += 1
        if not n["WF"] and not n["TR"]:
            continue
        g, o = grupos.get(img.name, {}), origen.get(img.name, {})
        try:
            iid = int(o.get("imagen_id") or g.get("imagen_id") or img.stem.split("_")[-1])
        except ValueError:
            iid = 0
        out.append({"foto": img.name, "imagen_id": iid,
                    "trampa": str(o.get("trampa") or g.get("trampa") or ""),
                    "capturada": o.get("capturada") or g.get("dia") or "",
                    "wf": n["WF"], "tr": n["TR"]})
    return out


def hacer_plan(excluir_mismo_dia: bool = False) -> dict:
    fotos = fotos_propias()
    if not fotos:
        raise SystemExit(f"No hay fotos propias etiquetadas en {ia.DATASET_CAMPO}")
    test = max(fotos, key=lambda f: (f["capturada"], f["imagen_id"]))
    anterior = (ia.leer_estado().get("ultimo") or {}).get("test") or {}
    fuera = {test["foto"]}
    if excluir_mismo_dia:
        fuera |= {f["foto"] for f in fotos if f["trampa"] == test["trampa"]
                  and f["capturada"][:10] == test["capturada"][:10]}
    train = [f for f in fotos if f["foto"] not in fuera]
    avisos = []
    if anterior.get("foto") and anterior["foto"] != test["foto"]:
        vuelve = any(f["foto"] == anterior["foto"] for f in train)
        avisos.append(f"La foto de test anterior ({anterior['foto']}) "
                      + ("vuelve a train." if vuelve else "no vuelve a train (excluida)."))
    elif anterior.get("foto") == test["foto"]:
        avisos.append("No hay fotos etiquetadas nuevas: el test es el mismo que la otra vez.")
    previas = [f for f in train if f["trampa"] == test["trampa"]]
    if previas:
        avisos.append(f"Ojo: {len(previas)} foto(s) de train son de la misma trampa que el test; "
                      "si comparten tarjeta, comparten insectos (el test no es del todo ciego).")
    return {"test": test, "train_campo": train, "excluidas": sorted(fuera - {test["foto"]}),
            "test_anterior": anterior, "avisos": avisos}


# --------------------------------------------------------------------------- datasets
def _enlazar(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)                 # mismo disco: no ocupa sitio y no toca el original
    except OSError:
        shutil.copyfile(src, dst)


def _reescribir(src: Path, dst: Path, mapa: dict[int, int]) -> int:
    lineas = []
    if src.exists():
        for x in src.read_text(encoding="utf-8").splitlines():
            p = x.split()
            if len(p) == 5 and int(p[0]) in mapa:
                lineas.append(" ".join([str(mapa[int(p[0])])] + p[1:]))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_text("\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8", newline="\n")
    os.replace(tmp, dst)
    return len(lineas)


def _etiqueta_de(img: Path) -> Path:
    partes = list(img.parts)
    i = len(partes) - 1 - partes[::-1].index("images")
    partes[i] = "labels"
    return Path(*partes).with_suffix(".txt")


def preparar_base(log=print) -> tuple[list[str], list[str]]:
    """bichosi en 2 clases (una vez; se reutiliza). Devuelve las listas de train y val."""
    hecho = BASE_CONVERTIDA / "listo.json"
    if hecho.exists():
        d = json.loads(hecho.read_text(encoding="utf-8"))
        return d["train"], d["val"]
    log(f"Convirtiendo bichosi a 2 clases en {BASE_CONVERTIDA} (solo la primera vez)…")
    listas = {}
    for parte, fichero in (("train", "train_g_sin_campo.txt"), ("val", "val_g.txt")):
        out = []
        for linea in (ia.BASE_BICHOSI / fichero).read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            src = Path(linea.strip())
            sub = src.parts[len(ia.BASE_BICHOSI.parts)]          # train, valid, new_train…
            dst = BASE_CONVERTIDA / parte / "images" / f"{sub}__{src.name}"
            _enlazar(src, dst)
            _reescribir(_etiqueta_de(src), BASE_CONVERTIDA / parte / "labels" / (dst.stem + ".txt"),
                        MAPA_BICHOSI)
            out.append(str(dst))
        listas[parte] = out
        log(f"  {parte}: {len(out)} imágenes")
    hecho.write_text(json.dumps(listas), encoding="utf-8", newline="\n")
    return listas["train"], listas["val"]


def tamano_tesela(imgsz: int) -> int:
    """Lo justo para que el lado largo salga en 2 teselas con TESELA_SOLAPE (1280 -> 704)."""
    return -(-(imgsz // 2 + TESELA_SOLAPE // 2) // 32) * 32


def _cortes(lado: int, t: int) -> list[int]:
    """Orígenes de las teselas en un eje, repartidos para cubrir hasta el borde."""
    if lado <= t:
        return [0]
    n = -(-(lado - TESELA_SOLAPE) // (t - TESELA_SOLAPE))
    return [round(i * (lado - t) / (n - 1)) for i in range(n)]


def _conservar_vacia(nombre: str, prob: float) -> bool:
    """Muestreo determinista: la misma tesela sale o no sale siempre, sin depender del orden."""
    import hashlib
    return int(hashlib.sha1(nombre.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF < prob


def teselar(img: Path, lab: Path, salida: Path, imgsz: int, prob_vacias: float) -> tuple[list[str], dict]:
    """Corta una foto en teselas (imágenes + etiquetas YOLO). Devuelve las rutas y un recuento."""
    import cv2
    import numpy as np
    t = tamano_tesela(imgsz)
    cuenta = {"teselas": 0, "vacias_fuera": 0, "cajas": 0, "cajas_cortadas_fuera": 0}
    im = cv2.imdecode(np.fromfile(str(img), np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        return [], cuenta
    h0, w0 = im.shape[:2]
    r = imgsz / max(h0, w0)                 # = ultralytics.data.base.BaseDataset.load_image
    if r != 1:
        w, h = min(int(np.ceil(w0 * r)), imgsz), min(int(np.ceil(h0 * r)), imgsz)
        im = cv2.resize(im, (w, h), interpolation=cv2.INTER_LINEAR if r > 1 else cv2.INTER_AREA)
    h, w = im.shape[:2]
    cajas = []
    if lab.exists():
        for x in lab.read_text(encoding="utf-8").splitlines():
            p = x.split()
            if len(p) == 5:
                c, cx, cy, bw, bh = int(p[0]), *(float(v) for v in p[1:])
                cajas.append((c, (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w,
                              (cy + bh / 2) * h))
    salida.joinpath("images").mkdir(parents=True, exist_ok=True)
    salida.joinpath("labels").mkdir(parents=True, exist_ok=True)
    rutas = []
    for y0 in _cortes(h, t):
        for x0 in _cortes(w, t):
            x1, y1 = min(x0 + t, w), min(y0 + t, h)
            tw, th = x1 - x0, y1 - y0
            lineas = []
            for c, a, b, cc, d in cajas:
                ia_, ib, ic, id_ = max(a, x0), max(b, y0), min(cc, x1), min(d, y1)
                if ic <= ia_ or id_ <= ib:
                    continue
                if (ic - ia_) * (id_ - ib) < TESELA_AREA_MIN * (cc - a) * (d - b):
                    cuenta["cajas_cortadas_fuera"] += 1
                    continue
                lineas.append(f"{c} {((ia_ + ic) / 2 - x0) / tw:.6f} {((ib + id_) / 2 - y0) / th:.6f} "
                              f"{(ic - ia_) / tw:.6f} {(id_ - ib) / th:.6f}")
            nombre = f"{img.stem}__y{y0}x{x0}"
            if not lineas and not _conservar_vacia(nombre, prob_vacias):
                cuenta["vacias_fuera"] += 1
                continue
            destino = salida / "images" / (nombre + ".jpg")
            ok, buf = cv2.imencode(".jpg", im[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                continue
            buf.tofile(str(destino))
            (salida / "labels" / (nombre + ".txt")).write_text(
                "".join(x + "\n" for x in lineas), encoding="utf-8", newline="\n")
            cuenta["teselas"] += 1
            cuenta["cajas"] += len(lineas)
            rutas.append(str(destino))
    return rutas, cuenta


def teselar_lista(imagenes: list[str], salida: Path, imgsz: int, prob_vacias: float,
                  log=print) -> tuple[list[str], dict]:
    from concurrent.futures import ThreadPoolExecutor   # cv2 suelta el GIL: con hilos basta
    total = {"fotos": len(imagenes), "teselas": 0, "vacias_fuera": 0, "cajas": 0,
             "cajas_cortadas_fuera": 0}
    rutas = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r, c in ex.map(lambda s: teselar(Path(s), _etiqueta_de(Path(s)), salida, imgsz,
                                             prob_vacias), imagenes):
            rutas += r
            for k, v in c.items():
                total[k] += v
    log(f"  {salida.name}: {total['fotos']} fotos -> {total['teselas']} teselas, "
        f"{total['cajas']} cajas ({total['cajas_cortadas_fuera']} cortadas fuera, "
        f"{total['vacias_fuera']} teselas vacías fuera)")
    return rutas, total


def preparar_base_teselas(imgsz: int, log=print) -> tuple[list[str], list[str]]:
    """bichosi en teselas (una vez por imgsz; se reutiliza). Val conserva todo el fondo."""
    train_base, val_base = preparar_base(log)
    carpeta = BASE_TESELAS / f"i{imgsz}_t{tamano_tesela(imgsz)}_v{int(TESELA_VACIAS * 100)}"
    hecho = carpeta / "listo.json"
    if hecho.exists():
        d = json.loads(hecho.read_text(encoding="utf-8"))
        return d["train"], d["val"]
    log(f"Cortando bichosi en teselas en {carpeta} (solo la primera vez, unos minutos)…")
    tr, ctr = teselar_lista(train_base, carpeta / "train", imgsz, TESELA_VACIAS, log)
    va, cva = teselar_lista(val_base, carpeta / "val", imgsz, 1.0, log)
    hecho.write_text(json.dumps({"train": tr, "val": va, "recuento": {"train": ctr, "val": cva}}),
                     encoding="utf-8", newline="\n")
    return tr, va


def preparar_entreno(plan: dict, opciones: dict, log=print) -> Path:
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    carpeta = ia.ENTRENOS / sello
    carpeta.mkdir(parents=True, exist_ok=False)
    train_base, val_base = preparar_base(log)
    campo = []
    for f in plan["train_campo"]:
        src = ia.DATASET_CAMPO / "images" / f["foto"]
        dst = carpeta / "campo" / "images" / f["foto"]
        _enlazar(src, dst)
        _reescribir(ia.DATASET_CAMPO / "labels" / (src.stem + ".txt"),
                    carpeta / "campo" / "labels" / (src.stem + ".txt"), MAPA_CAMPO)
        campo.append(str(dst))
    test = plan["test"]["foto"]
    assert all(Path(x).name != test for x in campo + train_base), "¡la foto de test está en train!"
    if opciones.get("teselas"):
        train_base, val_base = preparar_base_teselas(int(opciones["imgsz"]), log)
        campo, _ = teselar_lista(campo, carpeta / "campo_teselas", int(opciones["imgsz"]),
                                 TESELA_VACIAS, log)
        assert all(not Path(x).name.startswith(Path(test).stem + "__") for x in campo), \
            "¡la foto de test está en train!"
    k = max(1, int(opciones["repetir"]))
    (carpeta / "train.txt").write_text("\n".join(train_base + campo * k) + "\n",
                                      encoding="utf-8", newline="\n")
    (carpeta / "val.txt").write_text("\n".join(val_base) + "\n", encoding="utf-8", newline="\n")
    (carpeta / "data.yaml").write_text(
        f"path: {carpeta.as_posix()}\ntrain: train.txt\nval: val.txt\nnc: 2\n"
        f"names: {ia.CLASES_NUEVO}\n", encoding="utf-8", newline="\n")
    (carpeta / "plan.json").write_text(json.dumps({"plan": plan, "opciones": opciones},
                                                  ensure_ascii=False, indent=1),
                                       encoding="utf-8", newline="\n")
    log(f"Train: {len(train_base)} de bichosi + {len(campo)} propias ×{k}. Val: {len(val_base)}.")
    log(f"Test ciego: {test} (capturada {plan['test']['capturada']}).")
    return carpeta


# --------------------------------------------------------------------------- entrenar
def ejecutar(carpeta: Path) -> int:
    """Lo corre el proceso hijo. Escribe en entreno.log; la ventana solo lo lee."""
    import torch
    from ultralytics import YOLO
    d = json.loads((carpeta / "plan.json").read_text(encoding="utf-8"))
    op, plan = d["opciones"], d["plan"]
    pesos = op["pesos"]
    if pesos == "ultimo":
        pesos = str(ia.modelo_actual() or (ia.ML / "yolo11n.pt"))
    elif not Path(pesos).exists() and Path(pesos).name.startswith("yolo"):
        pesos = Path(pesos).name             # sin ruta, Ultralytics lo descarga
    print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NINGUNA (CPU)",
          flush=True)
    print("Pesos iniciales:", pesos, flush=True)
    imgsz = int(op["imgsz"])
    imgsz_entreno = tamano_tesela(imgsz) if op.get("teselas") else imgsz
    print(f"Entrena a {imgsz_entreno} px; infiere a {imgsz} px.", flush=True)
    YOLO(pesos).train(
        data=str(carpeta / "data.yaml"), imgsz=imgsz_entreno, batch=int(op["batch"]),
        epochs=int(op["epochs"]), patience=int(op["epochs"]), seed=0, deterministic=True,
        device=0 if torch.cuda.is_available() else "cpu", workers=8 if op.get("teselas") else 4,
        project=str(carpeta), name="yolo", exist_ok=False, verbose=True, plots=True,
        fraction=float(op.get("fraccion", 1.0)))     # <1 solo para la prueba de humo
    best = carpeta / "yolo" / "weights" / "best.pt"
    if not best.exists():
        print("ERROR: no se ha generado best.pt", flush=True)
        return 1
    destino = ia.ML / "models" / "entomira" / f"nuevo_{carpeta.name}" / "best.pt"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, destino)
    (destino.parent / ia.INFERENCIA_JSON).write_text(
        json.dumps({"imgsz_inferencia": imgsz, "imgsz_entreno": imgsz_entreno,
                    "teselas": bool(op.get("teselas"))}), encoding="utf-8", newline="\n")
    metricas = _metricas_val(carpeta / "yolo" / "results.csv")
    estado = ia.leer_estado()
    ultimo = {"modelo": str(destino), "carpeta": str(carpeta),
              "fecha": datetime.now().isoformat(timespec="seconds"),
              "test": plan["test"], "train_campo": [f["foto"] for f in plan["train_campo"]],
              "opciones": op, "metricas_val": metricas}
    estado.setdefault("historial", []).append(ultimo)
    estado["ultimo"] = ultimo
    ia.guardar_estado(estado)
    print(f"ENTRENAMIENTO TERMINADO. Modelo: {destino}", flush=True)
    print(f"Métricas en val (bichosi): {metricas}", flush=True)
    return 0


def _metricas_val(results: Path) -> dict:
    try:
        with open(results, encoding="utf-8") as f:
            filas = [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(f)]
        mejor = max(filas, key=lambda r: float(r.get("metrics/mAP50(B)", 0)))
        return {"epoch": int(float(mejor["epoch"])),
                "mAP50": round(float(mejor["metrics/mAP50(B)"]), 4),
                "mAP50_95": round(float(mejor["metrics/mAP50-95(B)"]), 4),
                "precision": round(float(mejor["metrics/precision(B)"]), 4),
                "recall": round(float(mejor["metrics/recall(B)"]), 4)}
    except (OSError, ValueError, KeyError):
        return {}


def proceso_vivo(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except Exception:
        return False


def en_marcha() -> dict | None:
    try:
        d = json.loads(CERROJO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if proceso_vivo(int(d.get("pid", 0))) else None


def lanzar(carpeta: Path) -> int:
    log = open(carpeta / "entreno.log", "ab")
    flags = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen([str(PYTHON), "-u", str(Path(__file__).resolve()), "--ejecutar",
                          str(carpeta)], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags, cwd=str(carpeta))
    ia.ENTRENOS.mkdir(parents=True, exist_ok=True)
    CERROJO.write_text(json.dumps({"pid": p.pid, "carpeta": str(carpeta),
                                   "inicio": datetime.now().isoformat(timespec="seconds")}),
                       encoding="utf-8", newline="\n")
    return p.pid


# --------------------------------------------------------------------------- ventana
class Ventana:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.r = tk.Tk()
        self.r.title("ENTOMIRA · entrenar el modelo nuevo (mosca blanca + trips)")
        self.r.geometry("1000x720")
        arriba = tk.Frame(self.r, padx=10, pady=8)
        arriba.pack(fill="x")
        self.v = {"epochs": tk.StringVar(value="60"), "imgsz": tk.StringVar(value="1280"),
                  "batch": tk.StringVar(value="16"), "repetir": tk.StringVar(value="10")}
        for fila, (k, txt) in enumerate((("epochs", "Épocas"), ("imgsz", "Tamaño (px)"),
                                         ("batch", "Lote"), ("repetir", "Repetir fotos propias ×"))):
            tk.Label(arriba, text=txt).grid(row=fila, column=0, sticky="w")
            tk.Entry(arriba, textvariable=self.v[k], width=8).grid(row=fila, column=1, sticky="w")
        tk.Label(arriba, text="Pesos iniciales").grid(row=0, column=2, sticky="w", padx=(30, 4))
        self.pesos = tk.StringVar(value=next(iter(PESOS)))
        ttk.Combobox(arriba, textvariable=self.pesos, values=list(PESOS), state="readonly",
                     width=36).grid(row=0, column=3, sticky="w")
        self.excluir = tk.BooleanVar(value=False)
        tk.Checkbutton(arriba, text="Excluir también las fotos de la misma trampa y día que el test",
                       variable=self.excluir, command=self.ver_plan).grid(
            row=1, column=2, columnspan=2, sticky="w", padx=(30, 0))
        self.teselas = tk.BooleanVar(value=True)
        tk.Checkbutton(arriba, text="Entrenar en teselas (más rápido; desmarcar = lote 2 como antes)",
                       variable=self.teselas).grid(row=2, column=2, columnspan=2, sticky="w",
                                                   padx=(30, 0))
        tk.Label(arriba, text="«Tamaño» es el de inferencia (la foto entera). Medido el 24/10:\n"
                              "yolo26m en teselas, lote 16 -> GPU al 80-100 %, 11 GB, ~7-8 min por\n"
                              "época. Puedes cerrar esta ventana: el entrenamiento sigue.",
                 fg="#555", justify="left").grid(row=3, column=2, columnspan=2, sticky="w",
                                                 padx=(30, 0))
        botones = tk.Frame(self.r, padx=10)
        botones.pack(fill="x")
        self.b_ir = tk.Button(botones, text="▶ Entrenar", font=("Segoe UI", 12, "bold"),
                              bg="#16a34a", fg="white", padx=16, command=self.entrenar)
        self.b_ir.pack(side="left")
        self.b_parar = tk.Button(botones, text="■ Parar", padx=12, command=self.parar)
        self.b_parar.pack(side="left", padx=8)
        tk.Button(botones, text="Abrir el evaluador", padx=12,
                  command=lambda: subprocess.Popen([str(PYTHON), str(Path(__file__).with_name(
                      "evaluar_modelo.py"))])).pack(side="right")
        self.texto = tk.Text(self.r, font=("Consolas", 9), bg="#111", fg="#ddd", wrap="none")
        self.texto.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_actual: Path | None = None
        self.leido = 0
        self.ver_plan()
        self.r.after(1000, self.seguir_log)

    def escribir(self, s: str) -> None:
        self.texto.insert("end", s if s.endswith("\n") else s + "\n")
        self.texto.see("end")

    def ver_plan(self) -> None:
        self.texto.delete("1.0", "end")
        marcha = en_marcha()
        if marcha:
            self.escribir(f"YA HAY UN ENTRENAMIENTO EN MARCHA (pid {marcha['pid']}, desde "
                          f"{marcha['inicio']}). Se muestra su registro.\n")
            self.log_actual, self.leido = Path(marcha["carpeta"]) / "entreno.log", 0
            return
        try:
            p = hacer_plan(self.excluir.get())
        except SystemExit as e:
            self.escribir(str(e))
            return
        t = p["test"]
        self.escribir(f"Fotos propias etiquetadas: {len(p['train_campo']) + 1 + len(p['excluidas'])}")
        self.escribir(f"TEST CIEGO: {t['foto']} · trampa {t['trampa']} · capturada {t['capturada']}"
                      f" · {t['wf']} WF, {t['tr']} TR")
        if p["excluidas"]:
            self.escribir(f"Excluidas además: {', '.join(p['excluidas'])}")
        wf = sum(f["wf"] for f in p["train_campo"])
        tr = sum(f["tr"] for f in p["train_campo"])
        self.escribir(f"Propias en train: {len(p['train_campo'])} fotos · {wf} WF, {tr} TR")
        for a in p["avisos"]:
            self.escribir("· " + a)
        u = ia.leer_estado().get("ultimo")
        if u:
            self.escribir(f"\nÚltimo modelo: {u['modelo']} ({u['fecha']}); val {u.get('metricas_val')}")

    def entrenar(self) -> None:
        from tkinter import messagebox
        if en_marcha():
            messagebox.showwarning("ENTOMIRA", "Ya hay un entrenamiento en marcha.")
            return
        try:
            op = {k: int(v.get()) for k, v in self.v.items()}
        except ValueError:
            messagebox.showerror("ENTOMIRA", "Épocas, tamaño, lote y repetir tienen que ser números.")
            return
        op["pesos"] = PESOS[self.pesos.get()]
        op["excluir_mismo_dia"] = self.excluir.get()
        op["teselas"] = self.teselas.get()
        plan = hacer_plan(self.excluir.get())
        if not messagebox.askyesno("ENTOMIRA", f"Entrenar con test ciego {plan['test']['foto']}?\n"
                                               f"{op['epochs']} épocas a {op['imgsz']} px."):
            return
        self.texto.delete("1.0", "end")
        carpeta = preparar_entreno(plan, op, log=lambda s: (self.escribir(s), self.r.update()))
        pid = lanzar(carpeta)
        self.escribir(f"Lanzado (pid {pid}). Registro: {carpeta / 'entreno.log'}")
        self.log_actual, self.leido = carpeta / "entreno.log", 0

    def parar(self) -> None:
        from tkinter import messagebox
        m = en_marcha()
        if not m:
            return
        if messagebox.askyesno("ENTOMIRA", "¿Parar el entrenamiento? Lo hecho hasta ahora se pierde "
                                           "(queda last.pt en su carpeta)."):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(m["pid"])], capture_output=True)
            self.escribir("Parado.")

    def seguir_log(self) -> None:
        if self.log_actual and self.log_actual.exists():
            with open(self.log_actual, "rb") as f:
                f.seek(self.leido)
                trozo = f.read()
                self.leido = f.tell()
            if trozo:
                # ultralytics pinta la barra de progreso con \r: se queda la última versión.
                lineas = trozo.decode("utf-8", "replace").replace("\r\n", "\n").split("\n")
                limpio = "\n".join(x.split("\r")[-1] for x in lineas)
                self.texto.insert("end", limpio)
                self.texto.see("end")
        self.b_ir.config(state="disabled" if en_marcha() else "normal")
        self.r.after(2000, self.seguir_log)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ejecutar", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--plan", action="store_true", help="solo enseñar el plan (sin ventana)")
    a = ap.parse_args()
    if a.ejecutar:
        try:
            return ejecutar(a.ejecutar)
        finally:
            try:
                # Por la carpeta y no por el pid: el python.exe del venv es un lanzador y el
                # proceso que entrena tiene otro pid.
                if Path(json.loads(CERROJO.read_text(encoding="utf-8")).get("carpeta", ""))                         == a.ejecutar:
                    CERROJO.unlink()
            except (OSError, ValueError):
                pass
    if a.plan:
        print(json.dumps(hacer_plan(), ensure_ascii=False, indent=1))
        return 0
    Ventana().r.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
