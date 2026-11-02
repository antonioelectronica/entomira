"""Disco externo: pack de campaña por invernadero, espejo y endpoints (2026-10-23).

Lo que importa: el pack lleva TODAS las trampas del invernadero juntas, sin la llave del enlace de
cliente, y se verifica byte a byte; y nada se escribe si el disco no está montado.
"""
import json
import zipfile
from pathlib import Path

import pytest
from conftest import ADMIN, DEVICE_KEY


def _invernadero_con_dos_trampas(cliente, jpeg):
    from app.core.db import engine
    from app.models.models import Invernadero, Trampa
    from sqlmodel import Session
    ids = []
    for dev in ("esp32-DISCO1", "esp32-DISCO2"):
        r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                         files={"image": ("f.jpg", jpeg, "image/jpeg")}, data={"device_id": dev})
        assert r.status_code == 201, r.text
        ids.append(r.json()["trap_id"])
    with Session(engine) as s:
        gh = Invernadero(nombre="Nave de prueba / 1", client_token="secreto-del-enlace")
        s.add(gh); s.commit(); s.refresh(gh)
        for tid in ids:
            t = s.get(Trampa, tid); t.invernadero_id = gh.id; s.add(t)
        s.commit()
        return gh.id, ids


def test_pack_junta_las_trampas_y_se_verifica(cliente, jpeg, tmp_path):
    from app.core import config
    from app.services import disco
    gh, trampas = _invernadero_con_dos_trampas(cliente, jpeg)
    r = disco.crear_pack(Path(config.DB_PATH), gh, destino=tmp_path)
    z = Path(r["fichero"])
    assert z.exists() and not list(tmp_path.rglob("*.part"))
    with zipfile.ZipFile(z) as f:
        nombres = f.namelist()
        assert {f"trampa_{t}/originales" for t in trampas} <= {n.rsplit("/", 1)[0] for n in nombres}
        gh_csv = f.read("datos/invernadero.csv").decode()
        assert "secreto-del-enlace" not in gh_csv and "client_token" not in gh_csv
        m = json.loads(f.read("manifest.json"))
    assert sorted(m["trampas"]) == sorted(trampas) and m["fotos"] >= 2
    assert disco.verificar_pack(z) == len(m["ficheros"])


def test_pack_corrupto_se_detecta(cliente, jpeg, tmp_path):
    from app.core import config
    from app.services import disco
    gh, _ = _invernadero_con_dos_trampas(cliente, jpeg)
    z = Path(disco.crear_pack(Path(config.DB_PATH), gh, destino=tmp_path)["fichero"])
    with zipfile.ZipFile(z) as f:
        m = json.loads(f.read("manifest.json"))
    m["ficheros"]["datos/imagenes.csv"] = "0" * 64
    malo = tmp_path / "malo.zip"
    with zipfile.ZipFile(z) as a, zipfile.ZipFile(malo, "w") as b:
        for n in a.namelist():
            b.writestr(n, json.dumps(m) if n == "manifest.json" else a.read(n))
    with pytest.raises(disco.DiscoError):
        disco.verificar_pack(malo)


def test_sin_disco_montado_no_se_escribe_nada(cliente, monkeypatch, tmp_path):
    from app.services import disco
    monkeypatch.setattr(disco, "PUNTO", tmp_path / "no_montado")
    with pytest.raises(disco.DiscoError):
        disco.espejo(Path("x.db"), tmp_path)
    assert not (tmp_path / "no_montado").exists()
    r = cliente.post("/api/v1/disco/backup/invernadero/1", auth=ADMIN, json={})
    assert r.status_code == 409 and "montado" in r.json()["detail"]


def test_espejo_copia_fotos_y_bd(monkeypatch, tmp_path):
    import sqlite3
    from app.services import disco
    punto = tmp_path / "disco"; punto.mkdir()
    monkeypatch.setattr(disco, "PUNTO", punto)
    monkeypatch.setattr(disco, "montado", lambda: True)
    monkeypatch.setattr(disco, "DATA", tmp_path)
    monkeypatch.setattr(disco, "CERROJO", tmp_path / "disco.lock")
    imgs = tmp_path / "images" / "2026-10-23"; imgs.mkdir(parents=True)
    (imgs / "a.jpg").write_bytes(b"x" * 10)
    db = tmp_path / "e.db"
    c = sqlite3.connect(db)
    c.execute("create table t (x)"); c.commit(); c.close()
    r1 = disco.espejo(db, tmp_path / "images")
    r2 = disco.espejo(db, tmp_path / "images")
    assert r1["fotos_nuevas"] == 1 and r2["fotos_nuevas"] == 0 and r2["fotos_ya"] == 1
    assert (punto / "ENTOMIRA" / "espejo" / "images" / "2026-10-23" / "a.jpg").read_bytes() == b"x" * 10
    assert list((punto / "ENTOMIRA" / "espejo" / "bd").glob("entomira_*.db"))


def test_expulsar_y_apagar_piden_confirmacion_y_admin(cliente):
    assert cliente.post("/api/v1/disco/expulsar", json={"confirmar": "EXPULSAR"}).status_code in (401, 403)
    assert cliente.post("/api/v1/disco/expulsar", auth=ADMIN, json={}).status_code == 400
    assert cliente.post("/api/v1/sistema/apagar", auth=ADMIN, json={"confirmar": "si"}).status_code == 400
    assert cliente.get("/api/v1/disco", auth=ADMIN).json()["montado"] is False
