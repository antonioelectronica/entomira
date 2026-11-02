# server/ — Backend + Web (FASE 2, se despliega en la RPi)

FastAPI + SQLite + frontend SPA (build estático).

- `app/api/` — routers (ingest, traps, greenhouses, images, inference, demo, health).
- `app/models/` — modelos SQLModel (ver `Docs_Obsidian/03_Base_de_Datos.md`).
- `app/services/` — lógica (auto-registro, mapa de calor, relay de streaming, demo).
- `app/core/` — config (.env), seguridad (X-API-Key), BD.
- `frontend/` — SPA (Galería, Trampas, Invernaderos, Demo).

Contrato en `Docs_Obsidian/04_API_Endpoints.md`. **Aún no implementado** (FASE 2).
