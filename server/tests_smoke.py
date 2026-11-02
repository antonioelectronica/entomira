"""Smoke test funcional del backend (TestClient, BD temporal)."""
import io
import os
import tempfile

os.environ["ENTOMIRA_DATA_DIR"] = tempfile.mkdtemp(prefix="entomira_test_")
os.environ["DEVICE_API_KEY"] = "dev"
os.environ["WORKER_API_KEY"] = "wrk"
os.environ["ADMIN_PASS"] = ""   # sin Basic Auth en el test

from PIL import Image
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
DEV = {"X-API-Key": "dev"}
WRK = {"X-API-Key": "wrk"}


def jpeg_bytes(color=(240, 220, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


def main():
    with TestClient(app) as c:
        # health
        assert c.get("/api/v1/health").json()["status"] == "ok"

        # crear invernadero
        gh = c.post("/api/v1/greenhouses", json={
            "nombre": "Inv. Poniente", "ubicacion": "El Ejido",
            "num_lineos": 12, "responsable": "Antonio", "contacto": "600123123"}).json()
        assert gh["id"] and gh["sectores"] == ["A", "B", "pasillo", "C", "D"]

        # ingesta auto-registra la trampa
        r = c.post("/api/v1/ingest",
                   files={"image": ("f.jpg", jpeg_bytes(), "image/jpeg")},
                   data={"device_id": "esp32-AABBCCDDEEFF", "ip": "192.168.1.50"},
                   headers=DEV)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["registered"] is True
        trap_id = body["trap_id"]
        image_id = body["image_id"]
        assert body["config"]["version"] == 1

        # asignar la trampa al invernadero y sector B
        c.patch(f"/api/v1/traps/{trap_id}",
                json={"invernadero_id": gh["id"], "sector": "B"})

        # editar config (sube version)
        cfg = c.put(f"/api/v1/traps/{trap_id}/config",
                    json={"brillo": 1, "resolucion": "UXGA", "intervalo_seg": 86400}).json()
        assert cfg["version"] == 2 and cfg["resolucion"] == "UXGA"

        # worker: pendientes
        pend = c.get("/api/v1/inference/pending", headers=WRK).json()
        assert any(p["image_id"] == image_id for p in pend), pend

        # worker: subir resultado
        res = c.put(f"/api/v1/inference/{image_id}", headers=WRK, json={
            "model_version": "v1", "num_insectos": 3,
            "detections": [{"clase": "insecto", "confianza": 0.9,
                            "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}] * 3})
        assert res.status_code == 200, res.text

        # galería
        imgs = c.get("/api/v1/images").json()
        assert imgs and imgs[0]["num_insectos"] == 3
        assert imgs[0]["invernadero_nombre"] == "Inv. Poniente"
        assert len(imgs[0]["detecciones"]) == 3

        # mapa de calor: sector B debe sumar 3
        hm = c.get(f"/api/v1/greenhouses/{gh['id']}/heatmap").json()
        assert hm["sectors"]["B"] == 3, hm
        assert hm["total"] == 3

        # worker health
        assert c.get("/api/v1/health/worker").json()["online"] is True

        # snapshot: sin cámara real (IP falsa) → 502/504
        s = c.get(f"/api/v1/traps/{trap_id}/snapshot")
        assert s.status_code in (502, 504), s.status_code
        # stream: arranca (200) y falla dentro del generador si la cámara no responde
        st = c.get(f"/api/v1/traps/{trap_id}/stream")
        assert st.status_code == 200, st.status_code

        # demo con trampa online pero sin cámara alcanzable → 502
        d = c.post(f"/api/v1/demo/run?trap_id={trap_id}")
        assert d.status_code == 502, d.status_code

        print("SMOKE OK — todos los asserts pasaron")


if __name__ == "__main__":
    main()
