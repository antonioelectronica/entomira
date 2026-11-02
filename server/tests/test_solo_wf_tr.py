"""Solo mosca blanca y trips (2026-10-23), reproceso con otro modelo y tendencia diaria.

- MR/NC se guardan pero no cuentan ni se enseñan.
- Un reproceso no vuelve la foto a «pendiente» ni la anuncia otra vez por Telegram (`procesada`
  es el cursor del aviso).
- La tendencia diaria sale de las detecciones: cambia sola al reprocesar.
"""
from conftest import ADMIN, DEVICE_KEY

WORKER = {"X-API-Key": "clave-worker-de-prueba"}


def _foto(cliente, jpeg, dev):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("f.jpg", jpeg, "image/jpeg")}, data={"device_id": dev})
    assert r.status_code == 201, r.text
    return r.json()["trap_id"], r.json()["image_id"]


def _det(clase, x=0.5):
    return {"clase": clase, "confianza": 0.9, "x": x, "y": 0.5, "w": 0.01, "h": 0.01}


def _analizar(cliente, iid, dets, modelo="bichosi"):
    r = cliente.put(f"/api/v1/inference/{iid}", headers=WORKER,
                    json={"num_insectos": len(dets), "model_version": modelo, "detections": dets})
    assert r.status_code == 200, r.text
    return r.json()


def test_mr_nc_se_guardan_pero_no_cuentan_ni_se_ven(cliente, jpeg):
    tid, iid = _foto(cliente, jpeg, "esp32-SOLOWFTR1")
    r = _analizar(cliente, iid, [_det("WF"), _det("WF", 0.4), _det("TR"), _det("MR"), _det("NC")])
    assert r["num_insectos"] == 3, "MR/NC siguen sumando en num_insectos"
    img = cliente.get(f"/api/v1/images/{iid}", auth=ADMIN).json()
    assert sorted(d["clase"] for d in img["detecciones"]) == ["TR", "WF", "WF"]
    # Pero no se han perdido: el etiquetador (Modelo 1) los recibe todos.
    crudo = cliente.get(f"/api/v1/inference/{iid}/detections", headers=WORKER).json()
    assert sorted(d["clase"] for d in crudo["detections"]) == ["MR", "NC", "TR", "WF", "WF"]
    assert crudo["estado"] == "procesada"
    s = cliente.get("/api/v1/stats", auth=ADMIN).json()
    assert "auxiliares" not in s and "MR" not in s["por_especie"]


def test_detections_exige_la_clave_del_worker(cliente, jpeg):
    _, iid = _foto(cliente, jpeg, "esp32-SOLOWFTR2")
    assert cliente.get(f"/api/v1/inference/{iid}/detections").status_code in (401, 403)


def test_reprocesar_no_vuelve_a_pendiente_ni_mueve_el_cursor_de_avisos(cliente, jpeg):
    tid, iid = _foto(cliente, jpeg, "esp32-SOLOWFTR3")
    _analizar(cliente, iid, [_det("WF")])
    from app.core.db import engine
    from app.models.models import Imagen
    from sqlmodel import Session
    with Session(engine) as s:
        procesada_antes = s.get(Imagen, iid).procesada

    r = cliente.post(f"/api/v1/images/reprocesar?trampa={tid}", auth=ADMIN)
    assert r.status_code == 200 and r.json()["marcadas"] == 1
    img = cliente.get(f"/api/v1/images/{iid}", auth=ADMIN).json()
    assert img["estado"] == "procesada" and img["num_insectos"] == 1, \
        "mientras se reprocesa tiene que seguir valiendo el conteo anterior"
    pend = [x["image_id"] for x in
            cliente.get("/api/v1/inference/pending?limit=200", headers=WORKER).json()]
    assert iid in pend, "el worker no recibe la foto a reprocesar"

    _analizar(cliente, iid, [_det("WF"), _det("WF", 0.3), _det("TR")], modelo="nuevo")
    with Session(engine) as s:
        im = s.get(Imagen, iid)
        assert im.num_insectos == 3 and not im.reprocesar
        assert im.procesada == procesada_antes, "el reproceso la anunciaría otra vez por Telegram"
    pend = [x["image_id"] for x in
            cliente.get("/api/v1/inference/pending?limit=200", headers=WORKER).json()]
    assert iid not in pend


def test_reprocesar_es_solo_de_admin(cliente):
    assert cliente.post("/api/v1/images/reprocesar").status_code in (401, 403)


def test_tendencia_media_diaria_y_dinamica(cliente, jpeg):
    tid, a = _foto(cliente, jpeg, "esp32-SOLOWFTR4")
    _, b = _foto(cliente, jpeg, "esp32-SOLOWFTR4")
    _, c = _foto(cliente, jpeg, "esp32-SOLOWFTR4")          # se queda pendiente: sin dato
    _analizar(cliente, a, [_det("WF"), _det("TR")])
    _analizar(cliente, b, [_det("WF"), _det("WF"), _det("WF"), _det("MR")])
    s = cliente.get(f"/api/v1/stats/tendencia?trampa_id={tid}", auth=ADMIN).json()["series"]
    assert len(s) == 1 and len(s[0]["dias"]) == 1
    d = s[0]["dias"][0]
    assert d["n_fotos"] == 2 and d["pendientes"] == 1
    assert d["wf_media"] == 2.0 and d["tr_media"] == 0.5
    assert s[0]["tarjetas"], "faltan las fechas de cambio de tarjeta"
    # Un modelo mejor reprocesa y la tendencia cambia sola.
    _analizar(cliente, a, [_det("WF")] * 5)
    d = cliente.get(f"/api/v1/stats/tendencia?trampa_id={tid}",
                    auth=ADMIN).json()["series"][0]["dias"][0]
    assert d["wf_media"] == 4.0 and d["tr_media"] == 0.0
    csv = cliente.get(f"/api/v1/stats/tendencia?trampa_id={tid}&formato=csv", auth=ADMIN)
    assert csv.status_code == 200 and csv.text.startswith("trampa_id,alias,dia")


def test_recontar_sin_auxiliares_es_idempotente(cliente, jpeg):
    from app.core.db import _recontar_sin_auxiliares, engine
    from app.models.models import Imagen
    from sqlmodel import Session
    _, iid = _foto(cliente, jpeg, "esp32-SOLOWFTR5")
    _analizar(cliente, iid, [_det("WF"), _det("MR")])
    with Session(engine) as s:                 # como estaban las fotos antes del cambio
        im = s.get(Imagen, iid); im.num_insectos = 2; s.add(im); s.commit()
    assert _recontar_sin_auxiliares() >= 1
    with Session(engine) as s:
        assert s.get(Imagen, iid).num_insectos == 1
    assert _recontar_sin_auxiliares() == 0
