"""
Worker_IA — app de escritorio (GUI, sin consola) del nodo de IA de ENTOMIRA.

- Arranca el bucle de inferencia contra el servidor (reconexión automática).
- Permite reentrenar el modelo con las imágenes disponibles.
- Activa el sistema para el modo Demo (el bucle atiende las peticiones del servidor).
- Registra todo a un fichero de log.
- Vive en la bandeja de iconos ocultos (petición de Antonio, 17/10/2026): arranca escondida;
  clic en el icono o «Abrir» la enseña; «Cerrar» la termina; la X de la ventana solo la esconde.

Se empaqueta a Worker_IA.exe con PyInstaller (--noconsole). Ver build_exes.py.
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext

from . import config
from .loop import WorkerLoop

# Paleta ENTOMIRA
VERDE_OSC = "#1B4332"
VERDE_MED = "#2D6A4F"
VERDE_CLR = "#52B788"
AMBAR = "#F7A93B"
FONDO = "#0F1E17"
TEXTO = "#E8F3EC"

LOG_FILE = Path.home() / "entomira_worker.log"
ICONO = Path(__file__).resolve().parents[1] / "entomira.ico"


class WorkerApp:
    def __init__(self) -> None:
        self.msgq: "queue.Queue[str]" = queue.Queue()
        # Órdenes para Tk desde otros hilos (bandeja, GPU, reentreno). Tk no se toca nunca fuera
        # de su hilo, ni siquiera con root.after (acordado con Codex): se encola y lo ejecuta
        # _drain, que corre en el hilo de Tk.
        self.cmdq: "queue.Queue" = queue.Queue()
        self.icono = None
        self._viva = True
        self._setup_logging()
        self.loop = WorkerLoop(on_status=self._push, on_llamada=lambda: self._tk(self._mostrar))
        self.loop_thread: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("ENTOMIRA · Worker IA")
        self.root.configure(bg=FONDO)
        self.root.geometry("640x480")
        self.root.minsize(560, 420)
        self._build_ui()
        self._check_gpu()
        self.root.after(200, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._esconder)
        # Arranque automático del bucle al abrir.
        self._start()
        self._a_la_bandeja()

    # -- infraestructura --------------------------------------------------
    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
        )
        logging.getLogger("entomira").info("=== Worker_IA arrancado ===")

    def _push(self, msg: str) -> None:
        self.msgq.put(msg)

    def _tk(self, fn) -> None:
        """Ejecuta `fn` en el hilo de Tk (se puede llamar desde cualquier hilo)."""
        self.cmdq.put(fn)

    def _drain(self) -> None:
        while True:
            try:
                fn = self.cmdq.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:                         # noqa: BLE001 - una orden rota no para las demás
                logging.getLogger("entomira").exception("orden de la interfaz fallida")
            if not self._viva:
                return                                # _salir destruyó la ventana
        try:
            while True:
                msg = self.msgq.get_nowait()
                ts = datetime.now().strftime("%H:%M:%S")
                self.txt.configure(state="normal")
                self.txt.insert("end", f"[{ts}] {msg}\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
                self.status_var.set(msg)
                if self.icono is not None:
                    self.icono.title = ("ENTOMIRA · Worker IA — " + msg)[:127]
        except queue.Empty:
            pass
        self.root.after(200, self._drain)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        head = tk.Frame(self.root, bg=VERDE_OSC)
        head.pack(fill="x")
        tk.Label(head, text="ENTOMIRA", bg=VERDE_OSC, fg="white",
                 font=("Montserrat", 18, "bold")).pack(side="left", padx=16, pady=12)
        tk.Label(head, text="Worker de IA · YOLO GPU", bg=VERDE_OSC, fg=VERDE_CLR,
                 font=("Segoe UI", 10)).pack(side="left")
        self.gpu_lbl = tk.Label(head, text="GPU: …", bg=VERDE_OSC, fg=AMBAR,
                                font=("Consolas", 9))
        self.gpu_lbl.pack(side="right", padx=16)

        bar = tk.Frame(self.root, bg=FONDO)
        bar.pack(fill="x", padx=12, pady=8)
        self.btn_start = tk.Button(bar, text="▶ Iniciar", command=self._start,
                                   bg=VERDE_MED, fg="white", relief="flat",
                                   font=("Segoe UI", 10, "bold"), padx=14, pady=6)
        self.btn_start.pack(side="left")
        self.btn_stop = tk.Button(bar, text="■ Detener", command=self._stop,
                                  bg="#7A2E2E", fg="white", relief="flat",
                                  font=("Segoe UI", 10, "bold"), padx=14, pady=6)
        self.btn_stop.pack(side="left", padx=6)
        self.btn_train = tk.Button(bar, text="⟳ Reentrenar modelo",
                                   command=self._retrain, bg=AMBAR, fg=VERDE_OSC,
                                   relief="flat", font=("Segoe UI", 10, "bold"),
                                   padx=14, pady=6)
        self.btn_train.pack(side="right")

        self.status_var = tk.StringVar(value="Inicializando…")
        tk.Label(self.root, textvariable=self.status_var, bg=FONDO, fg=VERDE_CLR,
                 anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=14)

        self.txt = scrolledtext.ScrolledText(self.root, bg="#0A1611", fg=TEXTO,
                                             insertbackground=TEXTO, relief="flat",
                                             font=("Consolas", 9), state="disabled")
        self.txt.pack(fill="both", expand=True, padx=12, pady=(4, 10))

        tk.Label(self.root, text=f"Servidor: {config.SERVER_BASE_URL}   ·   "
                 f"Log: {LOG_FILE}", bg=FONDO, fg="#6B7280",
                 font=("Segoe UI", 8)).pack(fill="x", padx=14, pady=(0, 8))

    def _check_gpu(self) -> None:
        def work():
            try:
                import torch
                if torch.cuda.is_available():
                    name = torch.cuda.get_device_name(0)
                    self._tk(lambda: self.gpu_lbl.config(text=f"GPU: {name} ✓", fg=VERDE_CLR))
                    self._push(f"GPU disponible: {name}")
                else:
                    self._tk(lambda: self.gpu_lbl.config(text="GPU: NO DISPONIBLE ✗", fg="#FF6B6B"))
                    self._push("AVISO: CUDA no disponible; la inferencia iría por CPU.")
            except Exception as e:
                self._tk(lambda: self.gpu_lbl.config(text="GPU: error", fg="#FF6B6B"))
                self._push(f"No se pudo comprobar la GPU: {e}")
        threading.Thread(target=work, daemon=True).start()

    # -- acciones ---------------------------------------------------------
    def _start(self) -> None:
        if self.loop_thread and self.loop_thread.is_alive():
            return
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()

    def _stop(self) -> None:
        self.loop.stop()
        self._push("Worker detenido por el usuario.")

    def _retrain(self) -> None:
        self.btn_train.config(state="disabled")
        self._push("Reentrenamiento lanzado (GPU). Esto puede tardar…")

        def work():
            try:
                from .train import train
                summary = train(data="generic", model=r"C:\ento_ml\yolo11s.pt",
                                epochs=100, imgsz=960, batch=-1, patience=20,
                                name=None)
                self._push(f"Reentrenamiento OK · mAP50={summary['mAP50']} "
                           f"→ {summary['version_dir']}")
            except SystemExit as e:
                self._push(f"Reentrenamiento abortado: {e}")
            except Exception as e:
                self._push(f"Error en reentrenamiento: {e}")
            finally:
                self._tk(lambda: self.btn_train.config(state="normal"))
        threading.Thread(target=work, daemon=True).start()

    # -- bandeja de iconos ocultos ---------------------------------------------
    def _a_la_bandeja(self) -> None:
        """Icono en la bandeja y ventana escondida. Sin pystray, la ventana se queda a la vista."""
        try:
            import pystray
            from PIL import Image
            imagen = Image.open(ICONO) if ICONO.is_file() else Image.new("RGB", (64, 64), VERDE_MED)
            menu = pystray.Menu(
                pystray.MenuItem("Abrir", lambda: self._tk(self._mostrar), default=True),
                pystray.MenuItem("Cerrar", lambda: self._tk(self._salir)))
            self.icono = pystray.Icon("entomira_worker", imagen, "ENTOMIRA · Worker IA", menu)
            self.icono.run_detached()
        except Exception as e:                        # noqa: BLE001 - sin bandeja, ventana normal
            self.icono = None
            logging.getLogger("entomira").warning("sin icono en la bandeja: %s", e)
            return
        self.root.withdraw()
        # No se da por bueno hasta que pystray dice que el icono está a la vista: si no llega,
        # la ventana vuelve, para que nunca quede un worker invisible e inalcanzable (Codex).
        self.root.after(3000, self._comprobar_icono)

    def _comprobar_icono(self) -> None:
        if self.icono is not None and not getattr(self.icono, "visible", False):
            logging.getLogger("entomira").warning("el icono de la bandeja no apareció: ventana visible")
            try:
                self.icono.stop()
            except Exception:                         # noqa: BLE001
                pass
            self.icono = None
            self._mostrar()

    def _mostrar(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _esconder(self) -> None:
        if self.icono is None:          # sin bandeja, la X cierra como siempre
            self._salir()
        else:
            self.root.withdraw()

    def _salir(self) -> None:
        """«Cerrar» de la bandeja: para el bucle y termina cuando suelta (máx. 10 s), sin
        congelar la interfaz mientras tanto (Codex)."""
        self.loop.stop()
        self.root.withdraw()
        self._esperar_y_cerrar(50)

    def _esperar_y_cerrar(self, quedan: int) -> None:
        if quedan > 0 and self.loop_thread and self.loop_thread.is_alive():
            self.root.after(200, self._esperar_y_cerrar, quedan - 1)
            return
        if self.icono is not None:
            self.icono.stop()
        logging.getLogger("entomira").info("=== Worker_IA cerrado desde la bandeja ===")
        self._viva = False
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def ya_abierto() -> bool:
    """¿Hay ya un worker abierto? Si lo hay, se le pide que enseñe su ventana."""
    import time
    from .loop import hay_otro_worker, pedir_que_se_muestre
    if not hay_otro_worker():
        return False
    # El otro puede estar aún arrancando (GPU) y no haber creado el evento: se reintenta un poco,
    # pero este lanzamiento se va igual, sin abrir una segunda ventana (hallazgo de Codex).
    for _ in range(20):
        if pedir_que_se_muestre():
            break
        time.sleep(0.5)
    return True


def main() -> None:
    # Arranca escondido en la bandeja, así que es fácil volver a lanzarlo sin saber que ya está:
    # entonces se muestra el que funciona y este se va, sin el «puerto ocupado» (18/10/2026).
    if ya_abierto():
        logging.getLogger("entomira").info("ya había un worker: se le pide que se muestre")
        return
    WorkerApp().run()


if __name__ == "__main__":
    main()
