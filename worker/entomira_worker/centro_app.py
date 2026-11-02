"""
ENTOMIRA · Centro de Control — app de escritorio «todo en uno» (Windows, sin consola).

Reúne en una sola ventana lo que antes obligaba a abrir el Worker por un lado y entrar en
la web del servidor por otro:

  1. Worker IA    — arrancar/parar el bucle de inferencia en GPU, log en vivo, reentrenar.
  2. Trampas      — estado, batería y RITMO DE CARGA de cada trampa + toda la config de
                    cámara (resolución, calidad, brillo, intervalo, sueño profundo…).
  3. Vista en vivo— foto en directo refrescada sola, para APUNTAR la cámara sin la web.
  4. Servidor     — disco, base de datos, temperatura de la Pi y vaciado de galería.

Diseño: Tkinter puro (va con el Python del venv, sin dependencias nuevas) y todas las
llamadas de red en hilos aparte, para que la ventana nunca se congele. La API se habla con
Basic Auth de admin; las credenciales y la URL se guardan en `~/.entomira_centro.json`.

Se lanza con `run_centro.py` (o el .bat del escritorio) usando `pythonw.exe`, que no abre
consola. Ver Docs_Obsidian/08_Despliegue.md.
"""
from __future__ import annotations

import io
import json
import logging
import queue
import threading
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import requests

from . import config as wcfg
from .loop import WorkerLoop

# ---------------------------------------------------------------- identidad visual
VERDE_OSC = "#1B4332"
VERDE_MED = "#2D6A4F"
VERDE_CLR = "#52B788"
AMBAR = "#F7A93B"
ROJO = "#C0483C"
FONDO = "#0F1E17"
PANEL = "#14261D"
TEXTO = "#E8F3EC"
GRIS = "#8AA396"

AJUSTES = Path.home() / ".entomira_centro.json"
LOG_FILE = Path.home() / "entomira_centro.log"

RESOLUCIONES = ["QVGA", "VGA", "SVGA", "XGA", "SXGA", "UXGA", "QXGA", "QSXGA"]


def _ahora() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Ajustes:
    """URL del servidor y credenciales de admin, persistidas en el perfil del usuario."""

    def __init__(self) -> None:
        self.url = wcfg.SERVER_BASE_URL
        self.usuario = "entomira"
        self.password = ""
        self.cargar()

    def cargar(self) -> None:
        try:
            d = json.loads(AJUSTES.read_text(encoding="utf-8"))
            self.url = d.get("url", self.url)
            self.usuario = d.get("usuario", self.usuario)
            self.password = d.get("password", self.password)
        except Exception:
            pass

    def guardar(self) -> None:
        try:
            AJUSTES.write_text(json.dumps(
                {"url": self.url, "usuario": self.usuario, "password": self.password},
                indent=2), encoding="utf-8")
        except Exception as e:
            logging.getLogger("entomira.centro").warning("no se pudo guardar ajustes: %s", e)


class Api:
    """Cliente de la API del servidor. Devuelve None en vez de lanzar: la GUI sigue viva."""

    def __init__(self, ajustes: Ajustes) -> None:
        self.aj = ajustes

    @property
    def base(self) -> str:
        return self.aj.url.rstrip("/") + "/api/v1"

    def _auth(self):
        return (self.aj.usuario, self.aj.password)

    def get(self, path: str, timeout: float = 10, **kw):
        try:
            r = requests.get(f"{self.base}{path}", auth=self._auth(), timeout=timeout, **kw)
            return r if r.ok else None
        except requests.RequestException:
            return None

    def json(self, path: str, timeout: float = 10):
        r = self.get(path, timeout)
        try:
            return r.json() if r is not None else None
        except ValueError:
            return None

    def put(self, path: str, payload: dict, timeout: float = 10):
        try:
            return requests.put(f"{self.base}{path}", json=payload, auth=self._auth(),
                                timeout=timeout)
        except requests.RequestException:
            return None

    def post(self, path: str, payload: dict | None = None, timeout: float = 10):
        try:
            return requests.post(f"{self.base}{path}", json=payload, auth=self._auth(),
                                 timeout=timeout)
        except requests.RequestException:
            return None

    def delete(self, path: str, timeout: float = 60):
        try:
            return requests.delete(f"{self.base}{path}", auth=self._auth(), timeout=timeout)
        except requests.RequestException:
            return None


# ---------------------------------------------------------------- utilidades de UI
def estilo_boton(parent, texto, comando, color=VERDE_MED, fg="white", **kw):
    return tk.Button(parent, text=texto, command=comando, bg=color, fg=fg, relief="flat",
                     font=("Segoe UI", 10, "bold"), padx=12, pady=6,
                     activebackground=color, activeforeground=fg, cursor="hand2", **kw)


def carga_texto(crate) -> tuple[str, str]:
    """Texto y color del ritmo de carga. Ver Docs_Obsidian/06_Firmware.md: vale el SIGNO,
    no el valor exacto (el MAX17048 no mide corriente, estima y filtra)."""
    if crate is None:
        return "ritmo de carga no disponible", GRIS
    v = f"{crate:+.1f} %/h"
    if crate > 0.5:
        return f"⚡ CARGANDO ({v})", VERDE_CLR
    if crate < -0.5:
        return f"🔻 Descargando ({v})", AMBAR
    return f"⏸ En reposo ({v})", GRIS


class CentroApp:
    def __init__(self) -> None:
        self.ajustes = Ajustes()
        self.api = Api(self.ajustes)
        self.msgq: "queue.Queue[str]" = queue.Queue()
        self._setup_logging()

        self.loop = WorkerLoop(on_status=self.msgq.put)
        self.loop_thread: threading.Thread | None = None

        self.trampas: list[dict] = []
        self.trampa_sel: dict | None = None
        self.vista_activa = False
        self._foto_actual: bytes | None = None

        self.root = tk.Tk()
        self.root.title("ENTOMIRA · Centro de Control")
        self.root.configure(bg=FONDO)
        self.root.geometry("1080x720")
        self.root.minsize(940, 620)
        icono = Path(__file__).resolve().parents[1] / "entomira.ico"
        if icono.exists():
            try:
                self.root.iconbitmap(str(icono))
            except Exception:
                pass

        self._build_ui()
        self._check_gpu()
        self.root.after(200, self._drain)
        self.root.after(400, self._refrescar_todo)
        self._start_worker()          # el worker arranca solo, como hacía Worker_IA

    # -- infraestructura --------------------------------------------------
    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")])
        logging.getLogger("entomira").info("=== Centro de Control arrancado ===")

    def _push(self, msg: str) -> None:
        self.msgq.put(msg)

    def _drain(self) -> None:
        try:
            while True:
                msg = self.msgq.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", f"[{_ahora()}] {msg}\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
                self.estado_var.set(msg)
        except queue.Empty:
            pass
        self.root.after(200, self._drain)

    def _hilo(self, fn) -> None:
        """Lanza trabajo de red en segundo plano; la ventana nunca se bloquea."""
        threading.Thread(target=fn, daemon=True).start()

    # -- construcción de la ventana ---------------------------------------
    def _build_ui(self) -> None:
        cab = tk.Frame(self.root, bg=VERDE_OSC)
        cab.pack(fill="x")
        tk.Label(cab, text="ENTOMIRA", bg=VERDE_OSC, fg="white",
                 font=("Montserrat", 18, "bold")).pack(side="left", padx=(16, 6), pady=10)
        tk.Label(cab, text="Centro de Control", bg=VERDE_OSC, fg=VERDE_CLR,
                 font=("Segoe UI", 11)).pack(side="left")
        self.gpu_lbl = tk.Label(cab, text="GPU: …", bg=VERDE_OSC, fg=AMBAR,
                                font=("Consolas", 9))
        self.gpu_lbl.pack(side="right", padx=(6, 16))
        self.srv_lbl = tk.Label(cab, text="Servidor: …", bg=VERDE_OSC, fg=GRIS,
                                font=("Consolas", 9))
        self.srv_lbl.pack(side="right", padx=6)

        est = ttk.Style()
        try:
            est.theme_use("clam")
        except tk.TclError:
            pass
        est.configure("TNotebook", background=FONDO, borderwidth=0)
        est.configure("TNotebook.Tab", background=PANEL, foreground=TEXTO,
                      padding=(16, 8), font=("Segoe UI", 10, "bold"))
        est.map("TNotebook.Tab", background=[("selected", VERDE_MED)],
                foreground=[("selected", "white")])
        est.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                      foreground=TEXTO, rowheight=26, borderwidth=0)
        est.configure("Treeview.Heading", background=VERDE_OSC, foreground="white",
                      font=("Segoe UI", 9, "bold"))
        est.map("Treeview", background=[("selected", VERDE_MED)])

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        self.tab_worker = tk.Frame(self.nb, bg=FONDO)
        self.tab_trampas = tk.Frame(self.nb, bg=FONDO)
        self.tab_vista = tk.Frame(self.nb, bg=FONDO)
        self.tab_servidor = tk.Frame(self.nb, bg=FONDO)
        self.nb.add(self.tab_worker, text="🧠 Worker IA")
        self.nb.add(self.tab_trampas, text="🪤 Trampas y configuración")
        self.nb.add(self.tab_vista, text="📷 Vista en vivo")
        self.nb.add(self.tab_servidor, text="🖥 Servidor")
        self.nb.bind("<<NotebookTabChanged>>", self._cambio_pestana)

        self._build_worker()
        self._build_trampas()
        self._build_vista()
        self._build_servidor()

        self.estado_var = tk.StringVar(value="Inicializando…")
        tk.Label(self.root, textvariable=self.estado_var, bg=FONDO, fg=VERDE_CLR,
                 anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=14, pady=(0, 6))

    # ---------------------------------------------------------------- 1) worker
    def _build_worker(self) -> None:
        f = self.tab_worker
        bar = tk.Frame(f, bg=FONDO)
        bar.pack(fill="x", pady=(10, 6))
        self.btn_start = estilo_boton(bar, "▶ Iniciar worker", self._start_worker)
        self.btn_start.pack(side="left", padx=(4, 6))
        estilo_boton(bar, "■ Detener", self._stop_worker, color="#7A2E2E").pack(side="left")
        estilo_boton(bar, "⟳ Reentrenar modelo", self._retrain, color=AMBAR,
                     fg=VERDE_OSC).pack(side="right", padx=4)
        estilo_boton(bar, "📂 Abrir log", self._abrir_log, color=PANEL,
                     fg=TEXTO).pack(side="right", padx=4)

        info = tk.Frame(f, bg=PANEL)
        info.pack(fill="x", padx=4, pady=(0, 6))
        self.worker_info = tk.Label(info, text="Modelo: …", bg=PANEL, fg=GRIS, anchor="w",
                                    font=("Consolas", 9), justify="left")
        self.worker_info.pack(fill="x", padx=10, pady=6)

        self.txt = scrolledtext.ScrolledText(f, bg="#0A1611", fg=TEXTO, relief="flat",
                                             insertbackground=TEXTO, font=("Consolas", 9),
                                             state="disabled")
        self.txt.pack(fill="both", expand=True, padx=4, pady=(0, 8))

    def _check_gpu(self) -> None:
        def work():
            try:
                import torch
                if torch.cuda.is_available():
                    nombre = torch.cuda.get_device_name(0)
                    self.gpu_lbl.config(text=f"GPU: {nombre} ✓", fg=VERDE_CLR)
                    self._push(f"GPU disponible: {nombre}")
                else:
                    self.gpu_lbl.config(text="GPU: NO DISPONIBLE ✗", fg="#FF6B6B")
                    self._push("AVISO: CUDA no disponible; la inferencia iría por CPU.")
            except Exception as e:
                self.gpu_lbl.config(text="GPU: error", fg="#FF6B6B")
                self._push(f"No se pudo comprobar la GPU: {e}")
            self.worker_info.config(
                text=f"Modelo: {wcfg.MODEL_PATH}\nServidor: {self.ajustes.url}\n"
                     f"Log: {LOG_FILE}")
        self._hilo(work)

    def _start_worker(self) -> None:
        if self.loop_thread and self.loop_thread.is_alive():
            return
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()

    def _stop_worker(self) -> None:
        self.loop.stop()
        self._push("Worker detenido por el usuario.")

    def _retrain(self) -> None:
        if not messagebox.askyesno(
                "Reentrenar modelo",
                "Se reentrenará el modelo en la GPU. Puede tardar bastante y el PC irá más "
                "lento mientras tanto.\n\n¿Continuar?"):
            return
        self._push("Reentrenamiento lanzado (GPU). Esto puede tardar…")

        def work():
            try:
                from .train import train
                resumen = train(data="generic", model=r"C:\ento_ml\yolo11s.pt",
                                epochs=100, imgsz=960, batch=-1, patience=20, name=None)
                self._push(f"Reentrenamiento OK · mAP50={resumen['mAP50']} "
                           f"→ {resumen['version_dir']}")
            except SystemExit as e:
                self._push(f"Reentrenamiento abortado: {e}")
            except Exception as e:
                self._push(f"Error en reentrenamiento: {e}")
        self._hilo(work)

    def _abrir_log(self) -> None:
        import os
        try:
            os.startfile(str(LOG_FILE))            # noqa: S606 (Windows)
        except Exception as e:
            messagebox.showerror("Log", f"No se pudo abrir el log:\n{e}")

    # ---------------------------------------------------------------- 2) trampas
    def _build_trampas(self) -> None:
        f = self.tab_trampas
        izq = tk.Frame(f, bg=FONDO)
        izq.pack(side="left", fill="both", expand=True, padx=(4, 6), pady=8)

        barra = tk.Frame(izq, bg=FONDO)
        barra.pack(fill="x", pady=(0, 6))
        estilo_boton(barra, "⟳ Actualizar", self._refrescar_trampas,
                     color=PANEL, fg=TEXTO).pack(side="left")
        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(barra, text="Auto (15 s)", variable=self.auto_var, bg=FONDO,
                       fg=GRIS, selectcolor=PANEL, activebackground=FONDO,
                       activeforeground=TEXTO, font=("Segoe UI", 9)).pack(side="left", padx=8)

        cols = ("alias", "estado", "bateria", "carga", "wifi", "imgs", "ultimo")
        self.tree = ttk.Treeview(izq, columns=cols, show="headings", height=12)
        for c, txt, w in (("alias", "Trampa", 190), ("estado", "Estado", 90),
                          ("bateria", "Batería", 110), ("carga", "Carga", 150),
                          ("wifi", "WiFi", 110), ("imgs", "Imágenes", 80),
                          ("ultimo", "Visto", 90)):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._seleccion_trampa)

        der = tk.Frame(f, bg=PANEL, width=380)
        der.pack(side="right", fill="y", padx=(0, 4), pady=8)
        der.pack_propagate(False)

        tk.Label(der, text="CONFIGURACIÓN DE LA CÁMARA", bg=PANEL, fg=VERDE_CLR,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        self.cfg_trampa_lbl = tk.Label(der, text="Selecciona una trampa", bg=PANEL,
                                       fg=GRIS, font=("Segoe UI", 9), anchor="w",
                                       justify="left", wraplength=340)
        self.cfg_trampa_lbl.pack(fill="x", padx=12, pady=(0, 8))

        self.campos: dict[str, tk.Variable] = {}
        cuerpo = tk.Frame(der, bg=PANEL)
        cuerpo.pack(fill="x", padx=12)

        def fila(label, widget):
            f2 = tk.Frame(cuerpo, bg=PANEL)
            f2.pack(fill="x", pady=3)
            tk.Label(f2, text=label, bg=PANEL, fg=TEXTO, width=16, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            widget.pack(side="right", fill="x", expand=True)
            return widget

        self.campos["resolucion"] = tk.StringVar(value="SVGA")
        fila("Resolución", ttk.Combobox(cuerpo, textvariable=self.campos["resolucion"],
                                        values=RESOLUCIONES, state="readonly", width=12))
        for clave, etiqueta, desde, hasta in (
                ("calidad_jpeg", "Calidad JPEG", 10, 63),
                ("brillo", "Brillo", -2, 2),
                ("contraste", "Contraste", -2, 2),
                ("saturacion", "Saturación", -2, 2),
                ("ae_level", "Exposición", -2, 2)):
            self.campos[clave] = tk.IntVar(value=0)
            fila(etiqueta, tk.Scale(cuerpo, from_=desde, to=hasta, orient="horizontal",
                                    variable=self.campos[clave], bg=PANEL, fg=TEXTO,
                                    troughcolor=FONDO, highlightthickness=0,
                                    font=("Segoe UI", 8)))

        self.campos["intervalo_seg"] = tk.IntVar(value=600)
        fila("Intervalo (s)", tk.Spinbox(cuerpo, from_=10, to=86400,
                                         textvariable=self.campos["intervalo_seg"],
                                         width=10, bg=FONDO, fg=TEXTO, relief="flat",
                                         insertbackground=TEXTO))
        tk.Label(cuerpo, text="Mínimo 10 s. Con QSXGA, 10 s llenan el disco de la Pi en "
                              "horas (pasó el 31/07).", bg=PANEL, fg=GRIS,
                 font=("Segoe UI", 8), wraplength=330, justify="left").pack(
            anchor="w", pady=(0, 6))

        self.campos["deep_sleep"] = tk.IntVar(value=0)
        tk.Checkbutton(cuerpo, text="🔋 Dormir entre capturas (modo campo)",
                       variable=self.campos["deep_sleep"], bg=PANEL, fg=TEXTO,
                       selectcolor=FONDO, activebackground=PANEL, activeforeground=TEXTO,
                       font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        tk.Label(cuerpo, text="Dormida no responde a nada: la vista en vivo solo va en su "
                              "ventana despierta. Déjalo apagado mientras apuntas la cámara.",
                 bg=PANEL, fg=GRIS, font=("Segoe UI", 8), wraplength=330,
                 justify="left").pack(anchor="w")

        self.campos["ventana_despierto_seg"] = tk.IntVar(value=15)
        fila("Ventana (s)", tk.Spinbox(cuerpo, from_=5, to=300,
                                       textvariable=self.campos["ventana_despierto_seg"],
                                       width=10, bg=FONDO, fg=TEXTO, relief="flat",
                                       insertbackground=TEXTO))

        botones = tk.Frame(der, bg=PANEL)
        botones.pack(fill="x", padx=12, pady=12)
        estilo_boton(botones, "✓ Aplicar a la trampa", self._aplicar_config).pack(fill="x")
        estilo_boton(botones, "📷 Ver en vivo", self._ir_a_vista, color=AMBAR,
                     fg=VERDE_OSC).pack(fill="x", pady=6)

    def _refrescar_trampas(self) -> None:
        def work():
            datos = self.api.json("/traps")
            if datos is None:
                self.srv_lbl.config(text="Servidor: sin conexión", fg="#FF6B6B")
                return
            self.srv_lbl.config(text=f"Servidor: {self.ajustes.url} ✓", fg=VERDE_CLR)
            self.trampas = [t for t in datos if not t.get("es_movil")]
            self.root.after(0, self._pintar_trampas)
        self._hilo(work)

    def _pintar_trampas(self) -> None:
        sel = self.trampa_sel["id"] if self.trampa_sel else None
        for it in self.tree.get_children():
            self.tree.delete(it)
        for t in self.trampas:
            bat = f"{t['bateria']}%" if t.get("bateria") is not None else "—"
            if t.get("voltaje") is not None:
                bat += f" · {t['voltaje']:.2f} V"
            carga, _ = carga_texto(t.get("crate"))
            wifi = (f"{t['wifi_pct']}% · {t['rssi']} dBm"
                    if t.get("rssi") is not None else "—")
            visto = "—"
            if t.get("ultimo_visto"):
                try:
                    dt = datetime.fromisoformat(t["ultimo_visto"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    seg = (datetime.now(timezone.utc) - dt).total_seconds()
                    visto = (f"hace {int(seg)} s" if seg < 90
                             else f"hace {int(seg // 60)} min")
                except ValueError:
                    pass
            self.tree.insert("", "end", iid=str(t["id"]),
                             values=(t.get("alias") or t["device_id"], t["estado"], bat,
                                     carga, wifi, t.get("num_imagenes", 0), visto))
        if sel is not None and self.tree.exists(str(sel)):
            self.tree.selection_set(str(sel))
        elif sel is None and self.trampas:
            # Al abrir, preselecciona una trampa DE VERDAD (las de demo y el propio PC
            # están registradas como dispositivos y saldrían primero por orden de id).
            real = next((t for t in self.trampas if t["device_id"].startswith("esp32")),
                        self.trampas[0])
            self.tree.selection_set(str(real["id"]))
            self._seleccion_trampa()

    def _seleccion_trampa(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        tid = int(sel[0])
        self.trampa_sel = next((t for t in self.trampas if t["id"] == tid), None)
        if not self.trampa_sel:
            return
        t = self.trampa_sel
        carga, _ = carga_texto(t.get("crate"))
        self.cfg_trampa_lbl.config(
            text=f"{t.get('alias') or t['device_id']}  ·  {t['estado']}\n"
                 f"IP {t.get('ip') or '—'}  ·  {carga}")
        cfg = t.get("config") or {}
        for clave, var in self.campos.items():
            if clave in cfg and cfg[clave] is not None:
                var.set(cfg[clave])

    def _aplicar_config(self) -> None:
        if not self.trampa_sel:
            messagebox.showinfo("Configuración", "Selecciona antes una trampa de la lista.")
            return
        t = self.trampa_sel
        payload = {k: v.get() for k, v in self.campos.items()}

        def work():
            r = self.api.put(f"/traps/{t['id']}/config", payload)
            if r is None:
                self._push("No se pudo conectar con el servidor al aplicar la config.")
            elif r.ok:
                ver = r.json().get("version")
                self._push(f"Config aplicada a {t.get('alias') or t['device_id']} "
                           f"(versión {ver}). La placa la coge en su próxima subida.")
                self._refrescar_trampas()
            else:
                self._push(f"El servidor rechazó la config: HTTP {r.status_code} {r.text[:180]}")
        self._hilo(work)

    def _ir_a_vista(self) -> None:
        if not self.trampa_sel:
            messagebox.showinfo("Vista en vivo", "Selecciona antes una trampa.")
            return
        self.nb.select(self.tab_vista)

    # ---------------------------------------------------------------- 3) vista en vivo
    def _build_vista(self) -> None:
        f = self.tab_vista
        barra = tk.Frame(f, bg=FONDO)
        barra.pack(fill="x", pady=(10, 6), padx=4)
        self.btn_vista = estilo_boton(barra, "▶ Empezar", self._toggle_vista)
        self.btn_vista.pack(side="left")
        estilo_boton(barra, "💾 Guardar foto", self._guardar_foto, color=PANEL,
                     fg=TEXTO).pack(side="left", padx=6)
        estilo_boton(barra, "☕ No te duermas", self._keep_awake, color=PANEL,
                     fg=TEXTO).pack(side="left")
        self.vista_info = tk.Label(barra, text="Selecciona una trampa en la pestaña anterior",
                                   bg=FONDO, fg=GRIS, font=("Segoe UI", 9))
        self.vista_info.pack(side="right", padx=6)

        tk.Label(f, text="Para apuntar la trampa: pulsa «Empezar» y ve moviéndola; la imagen "
                         "se refresca sola cada 1,5 s.", bg=FONDO, fg=GRIS,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=8)

        self.lienzo = tk.Label(f, bg="#08110C", fg=GRIS, text="(sin imagen)",
                               font=("Segoe UI", 10))
        self.lienzo.pack(fill="both", expand=True, padx=8, pady=8)

    def _toggle_vista(self) -> None:
        if not self.trampa_sel:
            messagebox.showinfo("Vista en vivo",
                                "Selecciona antes una trampa en «Trampas y configuración».")
            return
        self.vista_activa = not self.vista_activa
        self.btn_vista.config(text="■ Parar" if self.vista_activa else "▶ Empezar")
        if self.vista_activa:
            self._keep_awake(silencioso=True)
            self._tick_vista()

    def _tick_vista(self) -> None:
        if not self.vista_activa or not self.trampa_sel:
            return
        tid = self.trampa_sel["id"]

        def work():
            r = self.api.get(f"/traps/{tid}/snapshot", timeout=12)
            if r is None or not r.content:
                self.root.after(0, lambda: self.vista_info.config(
                    text="sin imagen (¿WiFi de la trampa?)", fg=AMBAR))
            else:
                self._foto_actual = r.content
                self.root.after(0, lambda: self._pintar_foto(r.content))
            if self.vista_activa:
                # la trampa empuja a ~3,7 fps: pedir cada 400 ms aprovecha ese ritmo
                self.root.after(400, self._tick_vista)
        self._hilo(work)

    def _pintar_foto(self, datos: bytes) -> None:
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.lienzo.config(text="Falta Pillow en el entorno (pip install pillow)")
            return
        try:
            im = Image.open(io.BytesIO(datos))
        except Exception as e:
            self.lienzo.config(text=f"imagen ilegible: {e}")
            return
        w = max(self.lienzo.winfo_width(), 320)
        h = max(self.lienzo.winfo_height(), 240)
        escala = min(w / im.width, h / im.height)
        if escala < 1:
            im = im.resize((int(im.width * escala), int(im.height * escala)))
        self._tk_img = ImageTk.PhotoImage(im)      # referencia viva: si no, se borra
        self.lienzo.config(image=self._tk_img, text="")
        self.vista_info.config(text=f"{im.width}×{im.height} · {_ahora()}", fg=VERDE_CLR)

    def _guardar_foto(self) -> None:
        if not self._foto_actual:
            messagebox.showinfo("Guardar foto", "Todavía no hay ninguna imagen.")
            return
        destino = filedialog.asksaveasfilename(
            defaultextension=".jpg", filetypes=[("JPEG", "*.jpg")],
            initialfile=f"entomira_{datetime.now():%Y%m%d_%H%M%S}.jpg")
        if destino:
            Path(destino).write_bytes(self._foto_actual)
            self._push(f"Foto guardada en {destino}")

    def _keep_awake(self, silencioso: bool = False) -> None:
        if not self.trampa_sel:
            return
        tid = self.trampa_sel["id"]

        def work():
            r = self.api.post(f"/traps/{tid}/keep-awake")
            if r is not None and r.ok and not silencioso:
                d = r.json()
                self._push(f"Pedido «no te duermas» {d.get('segundos')} s "
                           f"(duerme={d.get('duerme')}). Si está dormida, puede tardar "
                           f"hasta un intervalo en enterarse.")
        self._hilo(work)

    # ---------------------------------------------------------------- 4) servidor
    def _build_servidor(self) -> None:
        f = self.tab_servidor
        barra = tk.Frame(f, bg=FONDO)
        barra.pack(fill="x", pady=(10, 6), padx=4)
        estilo_boton(barra, "⟳ Actualizar", self._refrescar_servidor, color=PANEL,
                     fg=TEXTO).pack(side="left")
        estilo_boton(barra, "🌐 Abrir la web", self._abrir_web, color=PANEL,
                     fg=TEXTO).pack(side="left", padx=6)
        estilo_boton(barra, "⚙ Ajustes", self._dialogo_ajustes, color=PANEL,
                     fg=TEXTO).pack(side="left")
        estilo_boton(barra, "🗑 Vaciar galería", self._vaciar_galeria,
                     color=ROJO).pack(side="right", padx=4)

        self.srv_txt = tk.Label(f, text="…", bg=PANEL, fg=TEXTO, anchor="nw",
                                justify="left", font=("Consolas", 10))
        self.srv_txt.pack(fill="both", expand=True, padx=8, pady=8)

    def _refrescar_servidor(self) -> None:
        def work():
            d = self.api.json("/system/health")
            st = self.api.json("/stats") or {}
            if d is None:
                self.root.after(0, lambda: self.srv_txt.config(
                    text="Sin conexión con el servidor.\n\n"
                         f"URL configurada: {self.ajustes.url}\n"
                         "Revisa «⚙ Ajustes» o si la Raspberry Pi está encendida."))
                return
            pi = d.get("pi") or {}
            nivel = {"ok": "✅", "aviso": "⚠️", "critico": "🔴"}.get(d.get("disk_nivel"), "")
            texto = (
                f"  DISCO   {nivel} {d.get('disk_used_pct')} % usado  ·  "
                f"{d.get('disk_free_gb')} GB libres de {d.get('disk_total_gb')} GB\n"
                f"  IMÁGENES   {d.get('images_mb')} MB en {d.get('images_n')} ficheros\n"
                f"  BASE DE DATOS   {d.get('db_mb')} MB\n"
                f"  SERVICIO   versión {d.get('version')}  ·  "
                f"{d.get('uptime_h')} h en marcha\n"
                f"  RASPBERRY PI   {pi.get('cpu_temp', '—')} °C  ·  CPU {pi.get('cpu', '—')} %"
                f"  ·  RAM {pi.get('ram_used', '—')}/{pi.get('ram_total', '—')} MB\n\n"
                f"  GALERÍA   {st.get('fotos', '—')} fotos  ·  "
                f"{st.get('analizadas', '—')} analizadas  ·  "
                f"{st.get('pendientes', '—')} pendientes  ·  "
                f"{st.get('insectos', '—')} insectos\n\n"
                "  Purga automática diaria a las 04:00 en la Pi\n"
                "  (0 insectos → 3 días · sin procesar → 7 días).\n\n"
                "  ⚠️ Con el disco lleno la ingesta se cae EN SILENCIO: el servicio sigue\n"
                "     'active' y la web funciona, pero no entra ni una foto (pasó el 31/07).")
            self.root.after(0, lambda: self.srv_txt.config(text=texto))
        self._hilo(work)

    def _abrir_web(self) -> None:
        import webbrowser
        webbrowser.open(self.ajustes.url)

    def _vaciar_galeria(self) -> None:
        n = (self.api.json("/images/count") or {}).get("total", "?")
        if not messagebox.askyesno(
                "Vaciar galería",
                f"Se van a BORRAR {n} imágenes del servidor (se conservan las de "
                f"demostración).\n\nEsto no se puede deshacer. ¿Seguro?"):
            return
        if not messagebox.askyesno("Confirmar", "Última confirmación: ¿vaciar la galería?"):
            return

        def work():
            r = self.api.delete("/images")
            if r is not None and r.ok:
                self._push(f"Galería vaciada: {r.json()}")
                self._refrescar_servidor()
            else:
                self._push("No se pudo vaciar la galería.")
        self._hilo(work)

    def _dialogo_ajustes(self) -> None:
        win = tk.Toplevel(self.root, bg=PANEL)
        win.title("Ajustes de conexión")
        win.geometry("460x230")
        win.transient(self.root)
        campos = {}
        for etiqueta, clave, valor, oculto in (
                ("URL del servidor", "url", self.ajustes.url, False),
                ("Usuario", "usuario", self.ajustes.usuario, False),
                ("Contraseña", "password", self.ajustes.password, True)):
            fr = tk.Frame(win, bg=PANEL)
            fr.pack(fill="x", padx=16, pady=8)
            tk.Label(fr, text=etiqueta, bg=PANEL, fg=TEXTO, width=16, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            e = tk.Entry(fr, bg=FONDO, fg=TEXTO, relief="flat", insertbackground=TEXTO,
                         show="•" if oculto else "")
            e.insert(0, valor)
            e.pack(side="right", fill="x", expand=True)
            campos[clave] = e

        def guardar():
            self.ajustes.url = campos["url"].get().strip()
            self.ajustes.usuario = campos["usuario"].get().strip()
            self.ajustes.password = campos["password"].get()
            self.ajustes.guardar()
            self._push(f"Ajustes guardados (servidor {self.ajustes.url}).")
            win.destroy()
            self._refrescar_todo()

        estilo_boton(win, "Guardar", guardar).pack(pady=10)

    # ---------------------------------------------------------------- refresco
    def _cambio_pestana(self, _evt=None) -> None:
        actual = self.nb.index(self.nb.select())
        if actual == 3:
            self._refrescar_servidor()
        if actual == 2 and not self.vista_activa and self.trampa_sel:
            # Entrar en la vista en vivo ES querer verla: arranca sola, sin pulsar nada.
            self._toggle_vista()
        if actual != 2 and self.vista_activa:      # salir de la vista corta el sondeo
            self.vista_activa = False
            self.btn_vista.config(text="▶ Empezar")

    def _refrescar_todo(self) -> None:
        if self.auto_var.get():
            self._refrescar_trampas()
        self.root.after(15000, self._refrescar_todo)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    CentroApp().run()


if __name__ == "__main__":
    main()
