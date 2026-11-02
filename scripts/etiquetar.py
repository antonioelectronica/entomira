# -*- coding: utf-8 -*-
"""Etiquetador manual de fotos de trampa, en formato YOLO, para añadirlas al dataset.

Herramienta local (Tkinter + Pillow, sin dependencias nuevas). Diseño acordado con Codex el
2026-10-16 (ver BITACORA): insectos de ~15x4 px en recortes de 1705x2392, así que el zoom manda.
Antonio quitó después las revisiones: lo etiquetado pasa al dataset con el botón «Guardar en el
dataset».

    # bajar del servidor los recortes de la trampa 10 a una carpeta de trabajo
    C:\\ento_ml\\venv\\Scripts\\python.exe scripts\\etiquetar.py C:\\ento_ml\\datasets\\_etiquetado\\t10 --descargar 10
    # etiquetar lo que haya en esa carpeta
    C:\\ento_ml\\venv\\Scripts\\python.exe scripts\\etiquetar.py C:\\ento_ml\\datasets\\_etiquetado\\t10

Carpeta de trabajo (se guarda sola en cada cambio):
  labels/<foto>.txt  YOLO: «clase cx cy w h», normalizado 0..1.
  classes.txt        orden FIJO: MR, NC, WF, TR (índices de bichosi + TR=3).
  manifest.json      por foto: origen (trampa, id, capturada), quién y cuándo.

Botón «Guardar en el dataset» -> C:\\ento_ml\\datasets\\entomira_campo (o --dataset):
  images/, labels/, data.yaml (4 clases) y grupos.csv (foto, trampa, día) para partir por grupos.
  Solo pasan las fotos CON alguna caja: una foto sin cajas puede ser una foto sin mirar, y como
  negativo enseñaría al modelo a ignorar insectos (aviso de Codex). Volver a guardar sobrescribe;
  si a una foto ya guardada se le quitan todas las cajas, sale del dataset.
Juntar entomira_campo con bichosi es OTRO paso, con partición por trampa y día.

Solo mosca blanca (WF) y trips (TR) desde el 2026-10-23: la fauna auxiliar ya no se etiqueta. El
formato de los .txt sigue siendo de 4 clases (MR, NC, WF, TR) para no romper lo ya etiquetado.

Sugerencias en discontinuo, según el desplegable «Sugerencias» (2026-10-23, orden de Antonio):
  Modelo 1 · bichosi (servidor): mosca blanca de lo que vio el modelo de producción en esa foto
      (GET /inference/{id}/detections; si el servidor no responde o la foto aún no está analizada,
      se infiere aquí con el mismo bichosi). Los trips, de la foto ANTERIOR etiquetada de la
      misma trampa (las tarjetas acumulan).
  Modelo 2 · nuevo (local): mosca blanca y trips de la red entrenada con entrenar_modelo.py.
  Ninguna.
Doble clic acepta una; Supr la rechaza (y no vuelve); X rechaza todas las pendientes. No hay
«aceptar todas»: los errores del modelo van juntos y se colarían de golpe (Codex). Una sugerencia
que cae sobre una caja definitiva no se enseña. Las no aceptadas se guardan en
sugerencias/<foto>.json (con su fuente y las rechazadas) y nunca entran en el dataset.

Teclas:
  rueda              zoom centrado en el ratón
  botón derecho      arrastrar = desplazar
  botón izquierdo    arrastrar = caja nueva · sobre la caja elegida: moverla · en su esquina: tamaño
  Mayús + clic       caja del tamaño por defecto de la clase activa (ajústala después)
  1 / 2              clase activa: 1 mosca blanca, 2 trips (y la de la caja elegida)
  Supr / Retroceso   borrar la caja elegida
  Ctrl+Z             deshacer
  A / D, ← / →       foto anterior / siguiente
  N                  ir a la siguiente zona sin ver (ayuda para recorrer la foto)
  G                  mostrar / ocultar las zonas sin ver
  H                  ocultar / mostrar las cajas
  B                  bloquear la clase activa: todo lo que etiquetes es de esa clase y 1/2 no la
                     cambian (ni a la caja elegida) hasta que vuelvas a pulsar B
  Ctrl+S             guardar en el dataset (lo mismo que el botón)
  doble clic         sugerencia -> definitiva
  Supr sobre una     rechazarla (no vuelve a salir)
  X                  rechazar todas las sugerencias pendientes
  Mayús + flechas    mover TODAS las sugerencias pendientes (trampa desplazada)
  S                  volver a calcular las sugerencias (recupera las rechazadas)
"""
from __future__ import annotations

import argparse
import copy
import csv
import getpass
import hashlib
import json
import os
import queue
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entomira_ia as ia  # noqa: E402

CLASES = ["MR", "NC", "WF", "TR"]
NOMBRES = {"MR": "Macrolophus", "NC": "Nesidiocoris", "WF": "mosca blanca", "TR": "trips"}
COLORES = {"MR": "#3b82f6", "NC": "#a855f7", "WF": "#f8fafc", "TR": "#ef4444"}
CAJA_RAPIDA = {"MR": 40, "NC": 40, "WF": 22, "TR": 18}   # px de imagen; punto de partida
EXTS = {".jpg", ".jpeg", ".png"}
TESELA = 256          # zonas de la cuadrícula de «vistas», en px de imagen
ZOOM_TRABAJO = 2.0    # una zona cuenta como vista si se ha mostrado entera a este zoom o más
ASA = 6               # px de pantalla para agarrar una esquina
DATASET = Path(r"C:\ento_ml\datasets\entomira_campo")
AYUDA = """CONTROLES

Ratón
  rueda: zoom
  botón dcho. arrastrar: mover
  botón izdo. arrastrar: caja nueva
  clic en caja: elegirla
    arrastrar dentro: moverla
    arrastrar esquina: tamaño
  Mayús + clic: caja rápida

Teclado
  1: mosca blanca · 2: trips
  B: bloquear la clase
  Supr: borrar caja elegida
  Ctrl+Z: deshacer
  A / D, ← / →: otra foto
  N: siguiente zona sin ver
  G: ver zonas sin ver
  H: ocultar cajas
  Ctrl+S: guardar en el dataset

Sugerencias (discontinuas)
  elige la fuente arriba
  doble clic: aceptarla
  arrastrar: moverla antes
  Supr: rechazarla
  X: rechazar todas
  Mayús+flechas: mover todas
  S: recalcularlas
  las no aceptadas NO van
  al dataset

Se guarda solo en cada cambio
(en la carpeta de trabajo).
Al dataset solo van las fotos
con alguna caja."""


# --------------------------------------------------------------------------- ficheros
def escribir_atomico(ruta: Path, texto: str) -> None:
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8", newline="\n")
    os.replace(tmp, ruta)


def huella(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def validar_clases(carpeta: Path) -> None:
    f = carpeta / "classes.txt"
    if f.exists():
        leidas = [x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        if leidas != CLASES:
            sys.exit(f"classes.txt de {carpeta} es {leidas}, se esperaba {CLASES}: no se toca.")
    else:
        escribir_atomico(f, "\n".join(CLASES) + "\n")


def leer_cajas(ruta: Path, ancho: int, alto: int) -> list[list]:
    cajas = []
    if not ruta.exists():
        return cajas
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        p = linea.split()
        if not p:
            continue
        if len(p) != 5 or not p[0].isdigit() or int(p[0]) >= len(CLASES):
            sys.exit(f"{ruta}:{n}: etiqueta desconocida o mal formada: {linea!r}")
        c, cx, cy, w, h = int(p[0]), *map(float, p[1:])
        cajas.append([c, (cx - w / 2) * ancho, (cy - h / 2) * alto,
                      (cx + w / 2) * ancho, (cy + h / 2) * alto])
    return cajas


def solape(a: list, b: list) -> float:
    """IoU de dos cajas [clase, x1, y1, x2, y2, ...]."""
    ix = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    iy = max(0.0, min(a[4], b[4]) - max(a[2], b[2]))
    inter = ix * iy
    union = (a[3] - a[1]) * (a[4] - a[2]) + (b[3] - b[1]) * (b[4] - b[2]) - inter
    return inter / union if union > 0 else 0.0


def texto_yolo(cajas: list[list], ancho: int, alto: int) -> str:
    lineas = []
    for c, x1, y1, x2, y2, *_ in cajas:
        x1, x2 = sorted((max(0.0, min(ancho, x1)), max(0.0, min(ancho, x2))))
        y1, y2 = sorted((max(0.0, min(alto, y1)), max(0.0, min(alto, y2))))
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        lineas.append(f"{c} {(x1 + x2) / 2 / ancho:.6f} {(y1 + y2) / 2 / alto:.6f} "
                      f"{(x2 - x1) / ancho:.6f} {(y2 - y1) / alto:.6f}")
    return "\n".join(lineas) + ("\n" if lineas else "")


# --------------------------------------------------------------------------- descarga
def descargar(carpeta: Path, trampa: int, limite: int) -> None:
    """Baja por SSH desde el servidor los recortes de una trampa (no pide la clave del panel)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pi_ssh import PiClient
    carpeta.mkdir(parents=True, exist_ok=True)
    manifiesto = _cargar_manifiesto(carpeta)
    consulta = ("import sqlite3,json;c=sqlite3.connect('file:/home/entomira/entomira/data/entomira.db"
                "?mode=ro',uri=True);print(json.dumps(c.execute('select id,capturada,"
                "coalesce(ruta_recorte,ruta_fichero),ruta_recorte is not null from imagen where "
                f"trampa_id={int(trampa)} and es_demo=0 order by id desc limit {int(limite)}').fetchall()))")
    with PiClient(host=os.environ.get("ENTOMIRA_PI_HOST", "127.0.0.1")) as p:
        code, o, e = p.run(f'python3 -c "{consulta}"')
        if code != 0:
            sys.exit("no se pudo consultar el servidor: " + e.strip())
        nuevas = 0
        for id_, capturada, ruta, recortada in json.loads(o):
            nombre = f"t{trampa}_{id_}.jpg"
            if (carpeta / nombre).exists():
                continue
            p.get(ruta, str(carpeta / nombre))
            manifiesto.setdefault(nombre, {})["origen"] = {
                "trampa": trampa, "imagen_id": id_, "capturada": capturada,
                "recorte": bool(recortada)}
            nuevas += 1
    _guardar_manifiesto(carpeta, manifiesto)
    print(f"{nuevas} fotos nuevas en {carpeta}")


def _cargar_manifiesto(carpeta: Path) -> dict:
    f = carpeta / "manifest.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _guardar_manifiesto(carpeta: Path, m: dict) -> None:
    escribir_atomico(carpeta / "manifest.json", json.dumps(m, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- al dataset
CAMPOS_GRUPOS = ["foto", "trampa", "dia", "imagen_id", "etiquetador"]


def exportar(carpeta: Path, destino: Path) -> dict:
    """Copia al dataset las fotos de la carpeta que tengan alguna caja. Devuelve un resumen."""
    (destino / "images").mkdir(parents=True, exist_ok=True)
    (destino / "labels").mkdir(parents=True, exist_ok=True)
    yaml = destino / "data.yaml"
    if yaml.exists():
        if f"names: {CLASES}" not in yaml.read_text(encoding="utf-8"):
            raise ValueError(f"{yaml} tiene otras clases: no se mezcla")
    else:
        escribir_atomico(yaml, f"path: {destino.as_posix()}\ntrain: images\nval: images\n"
                               f"nc: {len(CLASES)}\nnames: {CLASES}\n")
    manifiesto = _cargar_manifiesto(carpeta)
    ruta_grupos = destino / "grupos.csv"
    grupos: dict[str, dict] = {}
    if ruta_grupos.exists():
        with open(ruta_grupos, encoding="utf-8", newline="") as f:
            grupos = {r["foto"]: r for r in csv.DictReader(f)}
    hechas, quitadas, vacias = 0, 0, 0
    for foto in sorted(p for p in carpeta.iterdir() if p.suffix.lower() in EXTS):
        etiquetas = carpeta / "labels" / (foto.stem + ".txt")
        texto = etiquetas.read_text(encoding="utf-8") if etiquetas.exists() else ""
        img_dst = destino / "images" / foto.name
        lab_dst = destino / "labels" / (foto.stem + ".txt")
        if not texto.strip():
            vacias += 1
            if img_dst.exists() or lab_dst.exists():      # tenía cajas y se le quitaron todas
                # Primero la imagen: si se corta aquí, queda una etiqueta huérfana, que YOLO ignora.
                # Al revés quedaría una imagen sin etiqueta, que YOLO toma por negativo.
                img_dst.unlink(missing_ok=True)
                lab_dst.unlink(missing_ok=True)
                grupos.pop(foto.name, None)
                quitadas += 1
            continue
        # Los dos temporales se preparan antes de publicar nada; luego la etiqueta y, al final, la
        # imagen. Así una imagen nunca queda en el dataset sin su etiqueta (que YOLO tomaría por
        # negativo), y si algo se corta, el siguiente «Guardar» lo rehace todo (es idempotente).
        tmp_img = img_dst.with_name(img_dst.name + ".tmp")
        tmp_lab = lab_dst.with_name(lab_dst.name + ".tmp")
        shutil.copyfile(foto, tmp_img)
        tmp_lab.write_text(texto, encoding="utf-8", newline="\n")
        os.replace(tmp_lab, lab_dst)
        os.replace(tmp_img, img_dst)
        m = manifiesto.get(foto.name, {})
        o = m.get("origen") or {}
        grupos[foto.name] = {"foto": foto.name, "trampa": o.get("trampa", ""),
                             "dia": (o.get("capturada") or "")[:10],
                             "imagen_id": o.get("imagen_id", ""),
                             "etiquetador": m.get("etiquetador", "")}
        hechas += 1
    tmp = ruta_grupos.with_name("grupos.csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_GRUPOS, lineterminator="\n")
        w.writeheader()
        for k in sorted(grupos):
            w.writerow(grupos[k])
    os.replace(tmp, ruta_grupos)
    return {"guardadas": hechas, "quitadas": quitadas, "sin_cajas": vacias,
            "total_dataset": len(grupos), "destino": str(destino)}


# --------------------------------------------------------------------------- sugerencias
FUENTES = {"m1": "Modelo 1 · bichosi (servidor): mosca blanca",
           "m2": "Modelo 2 · nuevo (local): mosca blanca y trips",
           "no": "Ninguna"}


def cubierta(n: list, otras: list) -> bool:
    """La sugerencia `n` ya está: solapa con otra caja o su centro cae dentro de ella."""
    cx, cy = (n[1] + n[3]) / 2, (n[2] + n[4]) / 2
    return any(solape(o, n) > 0.3 or (o[1] <= cx <= o[3] and o[2] <= cy <= o[4]) for o in otras)


# --------------------------------------------------------------------------- la herramienta
class Etiquetador:
    def __init__(self, carpeta: Path, quien: str, dataset: Path = DATASET) -> None:
        import tkinter as tk
        from PIL import Image, ImageTk
        self.tk, self.Image, self.ImageTk = tk, Image, ImageTk
        self.carpeta, self.quien, self.dataset = carpeta, quien, dataset
        (carpeta / "labels").mkdir(exist_ok=True)
        self.fotos = sorted(p for p in carpeta.iterdir() if p.suffix.lower() in EXTS)
        if not self.fotos:
            sys.exit(f"no hay imágenes en {carpeta}")
        self.manifiesto = _cargar_manifiesto(carpeta)
        self.clase = CLASES.index("TR")
        # Sugerencias de la IA: un solo hilo infiere (el modelo no se comparte entre hilos) y el
        # resultado vuelve por una cola; se descarta si ya se ha cambiado de foto o de fuente.
        self.fuente = "m1"
        self.modelo2 = ia.modelo_actual()
        self.aviso_ia = ""
        self._ficha = 0
        self._trabajos: queue.Queue = queue.Queue()
        self._hechos: queue.Queue = queue.Queue()
        threading.Thread(target=self._hilo_ia, daemon=True).start()
        self.ver_cajas, self.ver_zonas = True, False
        self.bloqueada = False                   # sigue puesta al cambiar de foto
        self._modo, self._toco, self._nueva = None, False, False
        self.dataset = dataset
        # Se empieza por la primera foto que aún no está en el dataset: las ya guardadas siguen
        # disponibles con A/D para corregirlas, pero no hacen perder el sitio (petición de Antonio).
        self.i = next((k for k, p in enumerate(self.fotos) if self._en_dataset(p) == "no"), 0)

        self.raiz = tk.Tk()
        self.raiz.title("ENTOMIRA · etiquetador")
        self.raiz.geometry("1400x900")
        # Botones de clase arriba: mosca blanca y trips primero, que son las de este invernadero.
        botonera = tk.Frame(self.raiz, bg="#111")
        botonera.pack(fill="x")
        self.botones = {}
        for tecla, nombre in (("1", "WF"), ("2", "TR")):
            k = CLASES.index(nombre)
            b = tk.Button(botonera, text=f"{tecla} · {NOMBRES[nombre]}", relief="raised", bd=2,
                          font=("Segoe UI", 12, "bold"), padx=14, pady=4, takefocus=0,
                          command=lambda k=k: self.elegir_clase(k))
            b.pack(side="left", padx=4, pady=4)
            self.botones[k] = b
        self.boton_bloqueo = tk.Button(botonera, text="🔓 Bloquear clase (B)", font=("Segoe UI", 11),
                                       padx=10, pady=4, takefocus=0, command=self.alternar_bloqueo)
        self.boton_bloqueo.pack(side="left", padx=(20, 4), pady=4)
        tk.Label(botonera, text="Sugerencias:", bg="#111", fg="#ddd",
                 font=("Segoe UI", 10)).pack(side="left", padx=(20, 2))
        self.var_fuente = tk.StringVar(value=FUENTES[self.fuente])
        menu = tk.OptionMenu(botonera, self.var_fuente, *FUENTES.values(),
                             command=lambda v: self.cambiar_fuente(
                                 next(k for k, x in FUENTES.items() if x == v)))
        menu.config(font=("Segoe UI", 10), takefocus=0)
        menu.pack(side="left", padx=2, pady=4)
        tk.Button(botonera, text="Elegir modelo 2…", font=("Segoe UI", 9), takefocus=0,
                  command=self.elegir_modelo2).pack(side="left", padx=4, pady=4)
        self.boton_guardar = tk.Button(botonera, text="💾 Guardar en el dataset",
                                       font=("Segoe UI", 12, "bold"), bg="#16a34a", fg="white",
                                       activebackground="#22c55e", padx=14, pady=4, takefocus=0,
                                       command=self.guardar_dataset)
        self.boton_guardar.pack(side="right", padx=8, pady=4)
        centro = tk.Frame(self.raiz, bg="#222")
        centro.pack(fill="both", expand=True)
        # Controles siempre a la vista (petición de Antonio).
        tk.Label(centro, text=AYUDA, justify="left", anchor="nw", font=("Consolas", 10),
                 bg="#161616", fg="#ccc", padx=10, pady=10).pack(side="right", fill="y")
        self.lienzo = tk.Canvas(centro, bg="#222", highlightthickness=0, cursor="crosshair")
        self.lienzo.pack(side="left", fill="both", expand=True)
        self.barra = tk.Label(self.raiz, anchor="w", font=("Segoe UI", 10), bg="#111", fg="#eee")
        self.barra.pack(fill="x")
        for ev, f in (("<MouseWheel>", self.rueda), ("<ButtonPress-3>", self.desplazar_inicio),
                      ("<B3-Motion>", self.desplazar), ("<ButtonPress-1>", self.pulsar),
                      ("<B1-Motion>", self.arrastrar), ("<ButtonRelease-1>", self.soltar),
                      ("<Shift-ButtonPress-1>", self.caja_rapida), ("<Configure>", lambda e: self.pintar())):
            self.lienzo.bind(ev, f)
        self.raiz.bind("<Key>", self.tecla)
        self.raiz.bind("<Control-z>", lambda e: self.deshacer())
        self.raiz.bind("<Control-s>", lambda e: self.guardar_dataset())
        self.lienzo.bind("<Double-Button-1>", self.confirmar)
        for flecha, (dx, dy) in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)), ("Down", (0, 1))):
            self.raiz.bind(f"<Shift-{flecha}>", lambda e, dx=dx, dy=dy: self.mover_sugerencias(dx, dy))
        self.raiz.protocol("WM_DELETE_WINDOW", self.salir)
        self.cargar()

    # --- datos de la foto actual
    def _ruta_etiquetas(self, foto: Path) -> Path:
        return self.carpeta / "labels" / (foto.stem + ".txt")

    def _ruta_sugerencias(self, foto: Path) -> Path:
        return self.carpeta / "sugerencias" / (foto.stem + ".json")

    def _clave(self, foto: Path) -> tuple:
        """(trampa, orden): por captura si el manifiesto la tiene; si no, por el id del nombre."""
        o = self.manifiesto.get(foto.name, {}).get("origen") or {}
        partes = foto.stem.split("_")
        trampa = o.get("trampa", partes[0] if len(partes) > 1 else None)
        try:
            num = int(partes[-1])
        except ValueError:
            num = 0
        return str(trampa), (o.get("capturada") or "", num)

    def anterior(self, foto: Path) -> Path | None:
        """La foto etiquetada más reciente de la misma trampa antes que esta. Una foto sin cajas
        definitivas no corta la cadena: se salta (ajuste de Codex)."""
        trampa, orden = self._clave(foto)
        mejor = None
        for p in self.fotos:
            t, o = self._clave(p)
            if p == foto or t != trampa or o >= orden:
                continue
            f = self._ruta_etiquetas(p)
            if not (f.exists() and f.read_text(encoding="utf-8").strip()):
                continue
            if mejor is None or o > mejor[0]:
                mejor = (o, p)
        return mejor[1] if mejor else None

    def _sugerencias_de_anterior(self, foto: Path) -> list[list]:
        previa = self.anterior(foto)
        if previa is None:
            return []
        # YOLO va normalizado: sirve aunque la foto anterior tuviera otro tamaño.
        return [c + ["sug"] for c in leer_cajas(self._ruta_etiquetas(previa), self.W, self.H)]

    def pendientes(self) -> int:
        return sum(1 for c in self.cajas if c[5] != "def")

    def _en_dataset(self, foto: Path) -> str:
        """'sí', 'con cambios sin guardar' o 'no'."""
        dst = self.dataset / "labels" / (foto.stem + ".txt")
        if not dst.exists():
            return "no"
        mio = self._ruta_etiquetas(foto)
        actual = mio.read_text(encoding="utf-8") if mio.exists() else ""
        return "sí" if dst.read_text(encoding="utf-8") == actual else "con cambios sin guardar"

    def cargar(self) -> None:
        foto = self.fotos[self.i]
        self.img = self.Image.open(foto).convert("RGB")
        self.W, self.H = self.img.size
        # Cada caja lleva su estado: "def" (definitiva), "sug" (sugerencia) o "sugm" (sugerencia
        # que ya se ha movido, redimensionado o reclasificado: S pregunta antes de pisarla).
        self.cajas = [c + ["def"] for c in leer_cajas(self._ruta_etiquetas(foto), self.W, self.H)]
        self.rechazadas: list[list] = []
        self.fuente_cajas = None                 # de qué fuente son las sugerencias de ahora
        ruta_sug = self._ruta_sugerencias(foto)
        guardadas = json.loads(ruta_sug.read_text(encoding="utf-8")) if ruta_sug.exists() else {}
        self.rechazadas = [list(c) for c in guardadas.get("rechazadas", [])]
        # Un fichero de antes del 2026-10-23 no dice su fuente: era la foto anterior.
        if guardadas and guardadas.get("fuente", "anterior") == self.clave_fuente():
            self.cajas += [list(c) for c in guardadas["cajas"]]
            self.fuente_cajas = self.clave_fuente()
        else:
            # Lo que el usuario ya movió o cambió es trabajo suyo: se queda aunque cambie la fuente.
            self.cajas += [list(c) for c in guardadas.get("cajas", []) if c[5] == "sugm"]
            self.pedir_sugerencias()
        self.historial: list[list] = []
        self.elegida: int | None = None
        m = self.manifiesto.setdefault(foto.name, {})
        self.vistas = set(map(tuple, m.get("vistas", [])))
        self.nx, self.ny = -(-self.W // TESELA), -(-self.H // TESELA)
        self.s, self.ox, self.oy = 1.0, 0.0, 0.0
        self._encajar = True                     # se encaja en cuanto el lienzo tenga tamaño real
        self.pintar()

    def encajar(self) -> bool:
        cw, ch = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        if cw < 50 or ch < 50:                   # la ventana aún no se ha dibujado
            return False
        self.s = min(cw / self.W, ch / self.H)
        self.ox = -(cw / self.s - self.W) / 2
        self.oy = -(ch / self.s - self.H) / 2
        self._encajar = False
        return True

    def guardar(self) -> None:
        foto = self.fotos[self.i]
        texto = texto_yolo([c for c in self.cajas if c[5] == "def"], self.W, self.H)
        escribir_atomico(self._ruta_etiquetas(foto), texto)
        # Las sugerencias se guardan aparte (aunque no quede ninguna: así no se vuelven a proponer
        # solas las que se descartaron) y nunca pasan al dataset.
        sug = [c for c in self.cajas if c[5] != "def"]
        ruta_sug = self._ruta_sugerencias(foto)
        if sug or self.rechazadas or ruta_sug.exists():
            ruta_sug.parent.mkdir(exist_ok=True)
            escribir_atomico(ruta_sug, json.dumps({"fuente": self.fuente_cajas, "cajas": sug,
                                                   "rechazadas": self.rechazadas}))
        m = self.manifiesto.setdefault(foto.name, {})
        m.update(huella=huella(texto), etiquetador=self.quien,
                 actualizado=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 vistas=sorted(self.vistas), tamano=[self.W, self.H])
        _guardar_manifiesto(self.carpeta, self.manifiesto)

    def cambio(self) -> None:
        """Toda edición se guarda al momento en la carpeta de trabajo."""
        self.guardar()
        self.pintar()

    def guardar_dataset(self) -> None:
        from tkinter import messagebox
        self.guardar()
        try:
            r = exportar(self.carpeta, self.dataset)
        except (OSError, ValueError) as e:
            messagebox.showerror("ENTOMIRA", f"No se ha podido guardar en el dataset:\n{e}")
            return
        texto = (f"Guardadas {r['guardadas']} fotos con etiquetas en\n{r['destino']}\n\n"
                 f"El dataset tiene ahora {r['total_dataset']} fotos.")
        if r["sin_cajas"]:
            texto += f"\n{r['sin_cajas']} sin ninguna caja: no se guardan."
        if r["quitadas"]:
            texto += f"\n{r['quitadas']} retiradas del dataset porque ya no tienen cajas."
        self.ultimo_resumen = r
        messagebox.showinfo("ENTOMIRA", texto)
        self.pintar()

    def apuntar(self) -> None:
        self.historial.append(copy.deepcopy(self.cajas))
        del self.historial[:-200]

    def deshacer(self) -> None:
        if self.historial:
            self.cajas = self.historial.pop()
            self.elegida = None
            self.cambio()

    # --- geometría
    def a_img(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + x / self.s, self.oy + y / self.s

    def a_lienzo(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.ox) * self.s, (y - self.oy) * self.s

    def marcar_vistas(self) -> None:
        if self.s < ZOOM_TRABAJO:
            return
        cw, ch = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        x0, y0 = self.a_img(0, 0)
        x1, y1 = self.a_img(cw, ch)
        for tx in range(self.nx):
            for ty in range(self.ny):
                ax, ay = tx * TESELA, ty * TESELA
                bx, by = min(ax + TESELA, self.W), min(ay + TESELA, self.H)
                if ax >= x0 and ay >= y0 and bx <= x1 and by <= y1:
                    self.vistas.add((tx, ty))

    # --- dibujo
    def pintar(self) -> None:
        if not hasattr(self, "img"):
            return
        if self._encajar and not self.encajar():
            return
        cw, ch = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        x0, y0 = self.a_img(0, 0)
        x1, y1 = self.a_img(cw, ch)
        caja = (max(0, int(x0)), max(0, int(y0)), min(self.W, int(x1) + 1), min(self.H, int(y1) + 1))
        self.lienzo.delete("all")
        if caja[2] > caja[0] and caja[3] > caja[1]:
            trozo = self.img.crop(caja)
            tam = (max(1, round((caja[2] - caja[0]) * self.s)), max(1, round((caja[3] - caja[1]) * self.s)))
            modo = self.Image.NEAREST if self.s >= 3 else self.Image.BILINEAR
            self.tkimg = self.ImageTk.PhotoImage(trozo.resize(tam, modo))
            px, py = self.a_lienzo(caja[0], caja[1])
            self.lienzo.create_image(px, py, anchor="nw", image=self.tkimg)
        self.marcar_vistas()
        if self.ver_zonas:
            for tx in range(self.nx):
                for ty in range(self.ny):
                    if (tx, ty) not in self.vistas:
                        a = self.a_lienzo(tx * TESELA, ty * TESELA)
                        b = self.a_lienzo(min((tx + 1) * TESELA, self.W), min((ty + 1) * TESELA, self.H))
                        self.lienzo.create_rectangle(*a, *b, outline="#f59e0b", stipple="gray25",
                                                     fill="#f59e0b")
        if self.ver_cajas:
            for k, (c, bx1, by1, bx2, by2, est) in enumerate(self.cajas):
                a, b = self.a_lienzo(bx1, by1), self.a_lienzo(bx2, by2)
                color = COLORES[CLASES[c]]
                self.lienzo.create_rectangle(*a, *b, outline=color, width=3 if k == self.elegida else 1,
                                             dash=(5, 3) if est != "def" else None)
                if k == self.elegida:
                    for qx, qy in ((a[0], a[1]), (b[0], a[1]), (a[0], b[1]), (b[0], b[1])):
                        self.lienzo.create_rectangle(qx - 3, qy - 3, qx + 3, qy + 3, fill=color, outline="")
        self.estado()

    def estado(self) -> None:
        self.pintar_botones()
        foto = self.fotos[self.i]
        cuenta = {n: 0 for n in ("WF", "TR")}
        for c, *_, est in self.cajas:
            if est == "def":
                cuenta[CLASES[c]] = cuenta.get(CLASES[c], 0) + 1
        faltan = self.nx * self.ny - len(self.vistas)
        self.barra.config(text=(
            f"  {self.i + 1}/{len(self.fotos)}  {foto.name}   ·   "
            f"en el dataset: {self._en_dataset(foto)}   ·   "
            f"zonas sin ver: {faltan}   ·   zoom {self.s:.1f}x   ·   "
            f"clase activa: {NOMBRES[CLASES[self.clase]]}"
            f"{' · BLOQUEADA (B para soltar)' if self.bloqueada else ''}   ·   "
            + "  ".join(f"{k} {v}" for k, v in cuenta.items())
            + (f"   ·   SUGERENCIAS PENDIENTES: {self.pendientes()}" if self.pendientes() else "")
            + (f"   ·   {self.aviso_ia}" if self.aviso_ia else "")))

    # --- ratón
    def rueda(self, e) -> None:
        f = 1.25 if e.delta > 0 else 0.8
        ix, iy = self.a_img(e.x, e.y)
        self.s = max(0.05, min(40.0, self.s * f))
        self.ox, self.oy = ix - e.x / self.s, iy - e.y / self.s
        self.pintar()

    def desplazar_inicio(self, e) -> None:
        self._arr = (e.x, e.y, self.ox, self.oy)

    def desplazar(self, e) -> None:
        x, y, ox, oy = self._arr
        self.ox, self.oy = ox - (e.x - x) / self.s, oy - (e.y - y) / self.s
        self.pintar()

    def _caja_en(self, x: float, y: float) -> int | None:
        ix, iy = self.a_img(x, y)
        mejor = None
        for k, (_, x1, y1, x2, y2, _e) in enumerate(self.cajas):
            m = ASA / self.s
            if x1 - m <= ix <= x2 + m and y1 - m <= iy <= y2 + m:
                if mejor is None or (x2 - x1) * (y2 - y1) < self._area(mejor):
                    mejor = k
        return mejor

    def _area(self, k: int) -> float:
        _, x1, y1, x2, y2, _e = self.cajas[k]
        return (x2 - x1) * (y2 - y1)

    def pulsar(self, e) -> None:
        self.lienzo.focus_set()
        ix, iy = self.a_img(e.x, e.y)
        self._modo, self._toco, self._nueva = None, False, False
        if self.elegida is not None:
            _, x1, y1, x2, y2, _e = self.cajas[self.elegida]
            esquinas = {"x1y1": (x1, y1), "x2y1": (x2, y1), "x1y2": (x1, y2), "x2y2": (x2, y2)}
            for nombre, (qx, qy) in esquinas.items():
                cx, cy = self.a_lienzo(qx, qy)
                if abs(cx - e.x) <= ASA and abs(cy - e.y) <= ASA:
                    self._modo = ("esquina", nombre)
                    break
        if self._modo is None:
            k = self._caja_en(e.x, e.y)
            if k is not None:
                self.elegida = k
                self._modo = ("mover", ix, iy, list(self.cajas[k]))
            else:
                self.elegida = None
                self._modo = ("nueva", ix, iy)
        self.pintar()

    def arrastrar(self, e) -> None:
        if not self._modo:
            return
        ix, iy = self.a_img(e.x, e.y)
        if not self._toco:
            self.apuntar()
            self._toco = True
            if self._modo[0] == "nueva":
                _, ax, ay = self._modo
                self.cajas.append([self.clase, ax, ay, ax, ay, "def"])
                self.elegida = len(self.cajas) - 1
                self._modo, self._nueva = ("esquina", "x2y2"), True
        caja = self.cajas[self.elegida]
        if self._modo[0] == "esquina":
            nombre = self._modo[1]
            caja[1 if "x1" in nombre else 3] = ix
            caja[2 if "y1" in nombre else 4] = iy
        elif self._modo[0] == "mover":
            _, ax, ay, orig = self._modo
            dx, dy = ix - ax, iy - ay
            caja[1:5] = [orig[1] + dx, orig[2] + dy, orig[3] + dx, orig[4] + dy]
        self.pintar()

    def soltar(self, e) -> None:
        if self._toco and self.elegida is not None:
            c, x1, y1, x2, y2, est = self.cajas[self.elegida]
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            # Un clic accidental no deja una caja: se mide en px de PANTALLA, que es donde tiembla
            # la mano; en px de imagen dependería del zoom.
            if (x2 - x1) * self.s < 3 or (y2 - y1) * self.s < 3 or x2 - x1 < 1 or y2 - y1 < 1:
                if self._nueva:
                    self.cajas.pop(self.elegida)
                    self.elegida = None
                else:                             # un ajuste fallido no borra una caja que ya había
                    self.cajas = self.historial.pop()
            else:
                self.cajas[self.elegida] = [c, x1, y1, x2, y2, "sugm" if est != "def" else est]
            self.cambio()
        self._modo, self._toco = None, False

    def caja_rapida(self, e) -> None:
        ix, iy = self.a_img(e.x, e.y)
        if not (0 <= ix < self.W and 0 <= iy < self.H):
            return "break"
        self.apuntar()
        m = CAJA_RAPIDA[CLASES[self.clase]] / 2
        self.cajas.append([self.clase, ix - m, iy - m, ix + m, iy + m, "def"])
        self.elegida = len(self.cajas) - 1
        self.cambio()
        return "break"                            # que no lo trate también como un clic normal

    # --- teclado
    def tecla(self, e) -> None:
        k = e.keysym
        if k in ("1", "2"):
            self.elegir_clase(CLASES.index("WF" if k == "1" else "TR"))
        elif k in ("b", "B"):
            self.alternar_bloqueo()
        elif k in ("Delete", "BackSpace") and self.elegida is not None:
            self.apuntar()
            caja = self.cajas.pop(self.elegida)
            if caja[5] != "def":                  # una sugerencia rechazada no vuelve a salir
                self.rechazadas.append(caja[:5])
            self.elegida = None
            self.cambio()
        elif k in ("x", "X"):
            self.rechazar_todas()
        elif k in ("a", "A", "Left"):
            self.ir(-1)
        elif k in ("d", "D", "Right"):
            self.ir(1)
        elif k in ("n", "N"):
            self.siguiente_zona()
        elif k in ("g", "G"):
            self.ver_zonas = not self.ver_zonas
            self.pintar()
        elif k in ("h", "H"):
            self.ver_cajas = not self.ver_cajas
            self.pintar()
        elif k in ("s", "S"):
            self.traer_sugerencias()

    def confirmar(self, e) -> None:
        """Doble clic: la sugerencia bajo el ratón pasa a definitiva."""
        k = self._caja_en(e.x, e.y)
        if k is not None and self.cajas[k][5] != "def":
            self.apuntar()
            self.cajas[k][5] = "def"
            self.elegida = k
            self.cambio()

    def mover_sugerencias(self, dx: int, dy: int) -> str:
        pend = [c for c in self.cajas if c[5] != "def"]
        if pend:
            self.apuntar()
            paso = max(1.0, 4 / self.s)           # ~4 px de pantalla, al menos 1 px de imagen
            for c in pend:
                c[1] += dx * paso; c[3] += dx * paso
                c[2] += dy * paso; c[4] += dy * paso
                c[5] = "sugm"
            self.cambio()
        return "break"                            # que la flecha no cambie también de foto

    def traer_sugerencias(self) -> None:
        """S: vuelve a calcular las sugerencias de la fuente elegida, rechazadas incluidas."""
        from tkinter import messagebox
        tocadas = sum(1 for c in self.cajas if c[5] == "sugm")
        if tocadas and not messagebox.askyesno(
                "ENTOMIRA", f"Hay {tocadas} sugerencias que ya has movido o cambiado.\n"
                            "¿Sustituirlas por las recalculadas?"):
            return
        self.apuntar()
        self.cajas = [c for c in self.cajas if c[5] == "def"]
        self.rechazadas = []
        self.elegida = None
        self.pedir_sugerencias()
        self.cambio()

    def rechazar_todas(self) -> None:
        pend = [c for c in self.cajas if c[5] != "def"]
        if not pend:
            return
        self.apuntar()
        self.rechazadas += [c[:5] for c in pend]
        self.cajas = [c for c in self.cajas if c[5] == "def"]
        self.elegida = None
        self.cambio()

    # --- sugerencias de la IA
    def clave_fuente(self) -> str:
        if self.fuente == "m2":
            return "m2|" + (ia.huella_modelo(self.modelo2) if self.modelo2 else "sin-modelo")
        return self.fuente

    def cambiar_fuente(self, fuente: str) -> None:
        self.fuente = fuente
        self.var_fuente.set(FUENTES[fuente])
        if fuente == "m2" and self.modelo2 is None:
            self.elegir_modelo2()
            if self.modelo2 is None:
                return
        self.recalcular_por_fuente()

    def elegir_modelo2(self) -> None:
        from tkinter import filedialog
        inicial = self.modelo2.parent if self.modelo2 else ia.ML / "models" / "entomira"
        r = filedialog.askopenfilename(title="Modelo 2 (best.pt)", initialdir=str(inicial),
                                       filetypes=[("Modelo YOLO", "*.pt")])
        if r:
            self.modelo2 = Path(r)
            if self.fuente == "m2":
                self.recalcular_por_fuente()

    def recalcular_por_fuente(self) -> None:
        """Otra fuente: fuera las sugerencias sin tocar de la anterior; las rechazadas siguen."""
        self.cajas = [c for c in self.cajas if c[5] in ("def", "sugm")]
        self.elegida = None
        self.pedir_sugerencias()
        self.cambio()

    def pedir_sugerencias(self) -> None:
        self._ficha += 1
        self.fuente_cajas = self.clave_fuente()
        if self.fuente == "no":
            self.aviso_ia = ""
            return
        foto = self.fotos[self.i]
        trips = []
        if self.fuente == "m1":
            # Con el Modelo 1 los trips siguen saliendo de la foto anterior de la misma trampa.
            trips = [c for c in self._sugerencias_de_anterior(foto) if CLASES[c[0]] == "TR"]
            self._poner_sugerencias(trips, sustituir=False)
        if self.fuente == "m2" and self.modelo2 is None:
            self.aviso_ia = "Modelo 2: elige un best.pt («Elegir modelo 2…»)"
            return
        self.aviso_ia = "calculando sugerencias de la IA…"
        origen = self.manifiesto.get(foto.name, {}).get("origen") or {}
        self._trabajos.put((self._ficha, self.fuente, self.modelo2, foto, origen, self.W, self.H))
        self.raiz.after(150, self._recoger)

    def _hilo_ia(self) -> None:
        while True:
            ficha, fuente, modelo2, foto, origen, W, H = self._trabajos.get()
            if ficha != self._ficha:              # ya se ha cambiado de foto o de fuente
                continue
            try:
                self._hechos.put((ficha, *self._inferir(fuente, modelo2, foto, origen, W, H)))
            except Exception as e:                # la IA no puede tumbar el etiquetador
                self._hechos.put((ficha, [], f"IA no disponible: {type(e).__name__}: {e}"))

    def _inferir(self, fuente, modelo2, foto, origen, W, H):
        if fuente == "m2":
            return ia.predecir(modelo2, str(foto)), f"Modelo 2 ({modelo2.parent.name})"
        aviso = "Modelo 1: bichosi del servidor"
        iid = origen.get("imagen_id")
        d = ia.detecciones_servidor(iid) if iid else None
        if d is None:
            aviso = "servidor sin respuesta: bichosi inferido aquí"
        elif d.get("estado") != "procesada":
            aviso = f"foto {d.get('estado', '¿?')} en el servidor: bichosi inferido aquí"
        elif bool(d.get("recortada")) != bool(origen.get("recorte", d.get("recortada"))):
            aviso = "el recorte del servidor no es el de esta foto: bichosi inferido aquí"
        else:
            return ia.cajas_servidor(d, W, H, ("WF",)), aviso
        return [c for c in ia.predecir(ia.BICHOSI, str(foto)) if c["clase"] == "WF"], aviso

    def _recoger(self) -> None:
        try:
            ficha, cajas, aviso = self._hechos.get_nowait()
        except queue.Empty:
            if self.aviso_ia.startswith("calculando"):
                self.raiz.after(150, self._recoger)
            return
        if ficha != self._ficha:
            self.raiz.after(150, self._recoger)
            return
        nuevas = [[CLASES.index(c["clase"]), c["x1"], c["y1"], c["x2"], c["y2"], "sug"]
                  for c in cajas]
        puestas = self._poner_sugerencias(nuevas, sustituir=False)
        self.aviso_ia = f"{aviso}: {puestas} sugerencias nuevas de {len(nuevas)}"
        self.cambio()

    def _poner_sugerencias(self, nuevas: list, sustituir: bool) -> int:
        """Añade las que no estén ya (definitivas, otras sugerencias) ni se hayan rechazado.

        Las ya confirmadas no vuelven: duplicarían el insecto en el dataset (Codex). Sin mirar la
        clase, porque una confirmada puede haber cambiado de clase."""
        if sustituir:
            self.cajas = [c for c in self.cajas if c[5] in ("def", "sugm")]
        antes = len(self.cajas)
        for n in nuevas:
            if not cubierta(n, self.cajas) and not cubierta(n, self.rechazadas):
                self.cajas.append(n)
        return len(self.cajas) - antes

    def elegir_clase(self, k: int) -> None:
        """Lo mismo con tecla que con botón: clase activa y, si hay caja elegida, también la suya."""
        if self.bloqueada and k != self.clase:
            self.barra.config(text=f"  Clase bloqueada en {CLASES[self.clase]} "
                                   f"({NOMBRES[CLASES[self.clase]]}): pulsa B o el candado para soltarla.")
            return
        self.clase = k
        if self.elegida is not None and self.cajas[self.elegida][0] != k:
            self.apuntar()
            self.cajas[self.elegida][0] = k
            if self.cajas[self.elegida][5] != "def":
                self.cajas[self.elegida][5] = "sugm"
            self.cambio()
        self.pintar()

    def alternar_bloqueo(self) -> None:
        self.bloqueada = not self.bloqueada
        self.pintar()

    def pintar_botones(self) -> None:
        for k, b in self.botones.items():
            activa = k == self.clase
            color = COLORES[CLASES[k]]
            b.config(relief="sunken" if activa else "raised",
                     bg=color if activa else "#333", fg="#111" if activa else "#ddd",
                     activebackground=color,
                     state="disabled" if self.bloqueada and not activa else "normal")
        self.boton_bloqueo.config(
            text=("🔒 Clase bloqueada (B)" if self.bloqueada else "🔓 Bloquear clase (B)"),
            relief="sunken" if self.bloqueada else "raised",
            bg="#f59e0b" if self.bloqueada else "#333", fg="#111" if self.bloqueada else "#ddd")

    def siguiente_zona(self) -> None:
        for ty in range(self.ny):
            for tx in range(self.nx):
                if (tx, ty) not in self.vistas:
                    cw, ch = self.lienzo.winfo_width(), self.lienzo.winfo_height()
                    # Zoom de trabajo, pero que la zona quepa ENTERA: si no, nunca se marca vista
                    # y N vuelve una y otra vez al mismo sitio (hallazgo de Codex).
                    cabe = 0.9 * min(cw, ch) / TESELA
                    self.s = max(ZOOM_TRABAJO, min(self.s, cabe))
                    cx, cy = (tx + 0.5) * TESELA, (ty + 0.5) * TESELA
                    self.ox, self.oy = cx - cw / 2 / self.s, cy - ch / 2 / self.s
                    self.pintar()
                    return
        self.barra.config(text="  Todas las zonas vistas.")

    def ir(self, paso: int) -> None:
        self.guardar()
        self.i = (self.i + paso) % len(self.fotos)
        self.aviso_ia = ""
        self.cargar()

    def salir(self) -> None:
        self.guardar()
        self.raiz.destroy()

    def correr(self) -> None:
        self.raiz.mainloop()


def main() -> int:
    ap = argparse.ArgumentParser(description="Etiquetador manual YOLO para ENTOMIRA")
    ap.add_argument("carpeta", type=Path)
    ap.add_argument("--descargar", type=int, metavar="TRAMPA",
                    help="antes, bajar del servidor los recortes de esa trampa")
    ap.add_argument("--limite", type=int, default=50, help="cuántas fotos bajar (las más recientes)")
    ap.add_argument("--quien", default=getpass.getuser(), help="quién etiqueta (va al manifiesto)")
    ap.add_argument("--dataset", type=Path, default=DATASET, help="adónde va «Guardar en el dataset»")
    a = ap.parse_args()
    a.carpeta.mkdir(parents=True, exist_ok=True)
    validar_clases(a.carpeta)
    if a.descargar is not None:
        descargar(a.carpeta, a.descargar, a.limite)
    Etiquetador(a.carpeta, a.quien, a.dataset).correr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
