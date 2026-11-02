"""Pruebas del Worker de IA. **Sin GPU y sin servidor**: se prueba la lógica, no el modelo.

El worker no tenía ninguna prueba, y es la pieza que más veces se ha quedado colgada en
silencio: si no encuentra el servidor, si el servidor contesta mal o si el modelo devuelve algo
raro, lo que NO puede hacer es romperse — tiene que seguir dando vueltas hasta que la cosa
vuelva. Eso es justo lo que se comprueba aquí.

    cd C:\\entomira\\worker && python -m pytest -q
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from entomira_worker import infer                      # noqa: E402
from entomira_worker.client import ServerClient        # noqa: E402


# --------------------------------------------------------------------------- utilidades
class RespFalsa:
    def __init__(self, ok=True, data=None, content=b""):
        self.ok = ok
        self.status_code = 200 if ok else 500
        self._data = data if data is not None else {}
        self.content = content

    def json(self):
        return self._data


# =========================================================== el cliente y su reconexión
def test_prueba_las_dos_urls_y_recuerda_la_que_funciona(monkeypatch):
    """Tailscale primero, IP local después. Y se queda con la que contestó.

    Es lo que hace que el worker sobreviva a que el PC cambie de red sin que nadie lo toque.
    """
    intentos = []

    def request_falso(method, url, **kw):
        intentos.append(url)
        if "no-existe" in url:
            import requests
            raise requests.RequestException("sin ruta")
        return RespFalsa(True, {"status": "ok"})

    monkeypatch.setattr("entomira_worker.client.config.SERVER_BASE_URL", "http://no-existe:8000")
    monkeypatch.setattr("entomira_worker.client.config.SERVER_FALLBACK_URL", "http://bueno:8000")
    monkeypatch.setattr("requests.request", request_falso)

    c = ServerClient()
    assert c.ping() is True
    assert len(intentos) == 2, "tiene que probar la primera y caer a la segunda"
    assert c.active_base == "http://bueno:8000"

    # A partir de aquí va directa a la buena: no vuelve a perder tiempo con la caída.
    intentos.clear()
    assert c.ping() is True
    assert intentos[0].startswith("http://bueno:8000")


def test_si_no_hay_servidor_no_revienta(monkeypatch):
    """Con el servidor caído el worker devuelve vacío y sigue vivo, no lanza excepción."""
    def request_falso(method, url, **kw):
        import requests
        raise requests.RequestException("caído")

    monkeypatch.setattr("requests.request", request_falso)
    c = ServerClient()
    assert c.ping() is False
    assert c.pending_images() == []          # lista vacía, no una excepción
    assert c.submit_result(1, {}) is False
    assert c.get_params() == {}
    assert c.active_base is None


def test_un_error_del_servidor_no_se_toma_por_bueno(monkeypatch):
    """Un 500 es un fallo aunque la conexión haya ido bien: no debe darse por hecho."""
    monkeypatch.setattr("requests.request", lambda *a, **k: RespFalsa(ok=False))
    c = ServerClient()
    assert c.ping() is False
    assert c.pending_images() == []
    assert c.submit_result(7, {"num_insectos": 3}) is False


def test_descargar_imagen_escribe_el_fichero(monkeypatch, tmp_path):
    monkeypatch.setattr("requests.request", lambda *a, **k: RespFalsa(True, content=b"JPEGJPEG"))
    destino = tmp_path / "x.jpg"
    c = ServerClient()
    assert c.download_image(5, str(destino)) is True
    assert destino.read_bytes() == b"JPEGJPEG"


# =========================================================== la inferencia y su formato
class TensorFalso(list):
    """YOLO devuelve tensores; lo unico que usa `infer` de ellos es `.tolist()`."""

    def tolist(self):
        return list(self)


class CajaFalsa:
    def __init__(self, cls, conf, xywhn):
        self.cls = [cls]
        self.conf = [conf]
        self.xywhn = [TensorFalso(xywhn)]


class ResultadoFalso:
    names = {0: "WF", 1: "MR"}

    def __init__(self, cajas):
        self.boxes = cajas


class ModeloFalso:
    """Sustituye a YOLO: aquí no se prueba el modelo, se prueba lo que hacemos con él."""

    def __init__(self, cajas):
        self._cajas = cajas
        self.ultimo_kw = None

    def predict(self, **kw):
        self.ultimo_kw = kw
        return [ResultadoFalso(self._cajas)]


def test_las_bboxes_salen_normalizadas_y_el_conteo_cuadra(monkeypatch):
    """Las cajas van en 0..1, independientes de la resolución.

    Importa de verdad: la misma trampa dispara a 5 MP y el vídeo a VGA, y la web pinta las
    cajas en porcentaje sobre la imagen. Si esto saliera en píxeles, las cajas se irían de
    sitio en cuanto cambiara la resolución.
    """
    modelo = ModeloFalso([CajaFalsa(0, 0.912345, [0.5, 0.25, 0.1, 0.2]),
                          CajaFalsa(1, 0.4, [0.1, 0.1, 0.05, 0.05])])
    monkeypatch.setattr(infer, "load_model", lambda *a, **k: modelo)

    r = infer.infer_image("da_igual.jpg", model_path=r"C:\m\bichosi\best.pt")

    assert r["num_insectos"] == 2 == len(r["detections"])
    d = r["detections"][0]
    assert d["clase"] == "WF"
    assert 0 <= d["x"] <= 1 and 0 <= d["y"] <= 1 and 0 <= d["w"] <= 1 and 0 <= d["h"] <= 1
    assert d["confianza"] == pytest.approx(0.9123, abs=1e-4)
    # La versión del modelo sale de la CARPETA: es lo que permite saber después con qué modelo
    # se contó cada foto.
    assert r["model_version"] == "bichosi"


def test_una_imagen_sin_insectos_da_cero_y_no_falla(monkeypatch):
    monkeypatch.setattr(infer, "load_model", lambda *a, **k: ModeloFalso([]))
    r = infer.infer_image("vacia.jpg")
    assert r["num_insectos"] == 0 and r["detections"] == []


def test_max_det_alto_para_no_truncar_el_conteo(monkeypatch):
    """El `max_det` por defecto de YOLO es 300 y una tarjeta con mosca blanca lo pasa.

    Truncar el conteo no da error: da un número más bajo, creíble y falso. Por eso se fija
    explícitamente, y por eso esta prueba lo vigila.
    """
    modelo = ModeloFalso([])
    monkeypatch.setattr(infer, "load_model", lambda *a, **k: modelo)
    infer.infer_image("x.jpg")
    assert modelo.ultimo_kw["max_det"] >= 1000


def test_la_telemetria_del_nodo_no_rompe_sin_gpu():
    """Sin `nvidia-smi` delante, la telemetría devuelve {} en vez de tirar el worker."""
    from entomira_worker import telemetry
    assert isinstance(telemetry.gpu_stats(), dict)
    assert isinstance(telemetry.host_stats(), dict)
