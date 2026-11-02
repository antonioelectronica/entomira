"""El agricultor VE las trampas de su invernadero, pero no las controla.

Bug del 2026-10-16: «pepe» (agricultor del invernadero 1) no veía nada aunque la trampa de campo
estaba en su invernadero, porque la visibilidad solo contaba las trampas de las que era dueño
(sus subidas del móvil). Ahora ve las de su invernadero, con la ficha recortada; el directo, la
configuración, el hardware y borrar siguen siendo del admin o del dueño. Acordado con Codex.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _invernadero(cliente, nombre):
    r = cliente.post("/api/v1/greenhouses", auth=ADMIN, json={"nombre": nombre})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _agricultor(cliente, usuario, gh):
    r = cliente.post("/api/v1/users", auth=ADMIN, json={
        "username": usuario, "password": "agri12345", "rol": "agricultor",
        "nombre": usuario, "invernadero_id": gh})
    assert r.status_code == 201, r.text
    return r.json()["id"], (usuario, "agri12345")


def test_el_agricultor_ve_la_trampa_de_su_invernadero_y_no_la_controla(cliente, jpeg):
    gh_a = _invernadero(cliente, "Invernadero A (prueba)")
    gh_b = _invernadero(cliente, "Invernadero B (prueba)")
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "zero-GH-A"})
    assert r.status_code == 201
    tid, img = r.json()["trap_id"], r.json()["image_id"]
    assert cliente.patch(f"/api/v1/traps/{tid}", auth=ADMIN,
                         json={"invernadero_id": gh_a}).status_code == 200
    uid_a, PEPE = _agricultor(cliente, "pepe_prueba", gh_a)
    uid_b, OTRO = _agricultor(cliente, "otro_prueba", gh_b)

    # Pepe la ve: listado, ficha, fotos y estadísticas.
    lista = cliente.get("/api/v1/traps", auth=PEPE).json()
    assert [t["id"] for t in lista] == [tid]
    ficha = cliente.get(f"/api/v1/traps/{tid}", auth=PEPE)
    assert ficha.status_code == 200
    # ...pero sin los datos de mantenimiento.
    f = ficha.json()
    assert f["config"] is None and f["ip"] is None and f["fw_version"] is None
    assert cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()["config"] is not None
    fotos = cliente.get("/api/v1/images", params={"trampa": tid}, auth=PEPE).json()
    assert img in [x["id"] for x in fotos]
    for sufijo in ("", "/file", "/thumb"):
        assert cliente.get(f"/api/v1/images/{img}{sufijo}", auth=PEPE).status_code == 200, sufijo
    # Solo la recortada (decisión de Antonio): el original es del admin.
    assert cliente.get(f"/api/v1/images/{img}/original", auth=PEPE).status_code == 403
    assert cliente.get(f"/api/v1/images/{img}/original", auth=ADMIN).status_code == 200
    assert cliente.get("/api/v1/stats", auth=PEPE).status_code == 200

    # No la controla ni ve sus datos internos.
    for metodo, ruta, esperado in (
            ("get", f"/api/v1/traps/{tid}/hardware", 404),
            ("post", f"/api/v1/traps/{tid}/live-ping", 404),
            ("post", f"/api/v1/traps/{tid}/foto", 404),
            ("put", f"/api/v1/traps/{tid}/config", 403),
            ("delete", f"/api/v1/traps/{tid}", 403),
            ("get", f"/api/v1/traps/{tid}/recorte", 403)):
        kw = {"json": {"enfoque": "auto"}} if metodo == "put" else {}
        r = getattr(cliente, metodo)(ruta, auth=PEPE, **kw)
        assert r.status_code == esperado, f"{metodo.upper()} {ruta}: {r.status_code}"

    # El de otro invernadero no la ve ni sabe que existe.
    assert cliente.get("/api/v1/traps", auth=OTRO).json() == []
    assert cliente.get(f"/api/v1/traps/{tid}", auth=OTRO).status_code == 404
    assert cliente.get(f"/api/v1/images/{img}", auth=OTRO).status_code == 404
    assert cliente.get(f"/api/v1/images/{img}/file", auth=OTRO).status_code == 404

    # El enlace de cliente del invernadero A enseña lo mismo que ve Pepe, sin iniciar sesión.
    token = cliente.post(f"/api/v1/greenhouses/{gh_a}/client-link", auth=ADMIN).json()["token"]
    token_b = cliente.post(f"/api/v1/greenhouses/{gh_b}/client-link", auth=ADMIN).json()["token"]
    base = f"/api/v1/client/{token}"
    assert cliente.get(base).status_code == 200                      # el plano, como antes
    s = cliente.get(f"{base}/stats").json()
    assert "por_especie" in s and "pendientes" in s and "trampas" not in s
    lista = cliente.get(f"{base}/images").json()
    assert [x["id"] for x in lista] == [x["id"] for x in fotos]
    assert all(x["device_id"] == "" and x["condiciones"] is None for x in lista)
    for sufijo in ("/file", "/thumb"):
        assert cliente.get(f"{base}/images/{img}{sufijo}").status_code == 200, sufijo
    # Ni el original, ni las fotos de otro invernadero, ni con un token inventado.
    # (no existe esa ruta: responde el comodín de la web con su HTML, nunca una imagen)
    r = cliente.get(f"{base}/images/{img}/original")
    assert "image" not in r.headers.get("content-type", "")
    assert cliente.get(f"/api/v1/client/{token_b}/images/{img}/file").status_code == 404
    assert cliente.get(f"/api/v1/client/{token_b}/images").json() == []
    assert cliente.get("/api/v1/client/inventado/images").status_code == 404
    assert cliente.get("/api/v1/client/inventado/stats").status_code == 404

    # Trampa calibrada con una foto sin recorte: el enlace no da el original (hallazgo de Codex).
    from sqlmodel import Session
    from app.core.db import engine
    from app.models.models import Imagen, Trampa
    with Session(engine) as ses:
        t = ses.get(Trampa, tid)
        t.recorte_tarjeta = {"quad": [[0, 0], [10, 0], [10, 10], [0, 10]], "res": [64, 48]}
        ses.add(t); ses.commit()
        im = ses.get(Imagen, img); ruta_thumb = im.ruta_thumbnail
        im.ruta_recorte = None; im.ruta_thumbnail = ""; ses.add(im); ses.commit()
    assert cliente.get(f"{base}/images/{img}/file").status_code == 404
    assert cliente.get(f"{base}/images/{img}/thumb").status_code == 404
    with Session(engine) as ses:
        im = ses.get(Imagen, img); im.ruta_thumbnail = ruta_thumb; ses.add(im)
        t = ses.get(Trampa, tid); t.recorte_tarjeta = None; ses.add(t); ses.commit()

    for uid in (uid_a, uid_b):
        cliente.delete(f"/api/v1/users/{uid}", auth=ADMIN)
