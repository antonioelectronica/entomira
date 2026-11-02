# ENTOMIRA

Open software stack for smart sticky-trap pest monitoring in greenhouses.

Field traps photograph a sticky card on a schedule and upload the images. The server stores them,
tracks trap health (battery, connectivity, sensors), and queues each photo for a YOLO detector
that counts the insects. Growers see counts per sector and how they evolve on a web dashboard.

This repository holds the software that does not depend on a specific trap design:

| Folder | What it is |
|---|---|
| `server/` | FastAPI + SQLite backend, REST API for traps, workers and users, and the web dashboard (`server/frontend/`). |
| `worker/` | GPU inference worker: pulls pending images from the server, runs YOLO and posts the detections back. Includes a small desktop control panel. |
| `scripts/etiquetar.py` | Labelling tool for building the field dataset from trap images. |
| `scripts/entrenar_modelo.py` | Training pipeline (tiled training on full-resolution trap photos). |

Code comments and the UI are in Spanish; the project is developed in Almería (Spain).

## Quick start (local)

```bash
cd server
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env        # then edit it
ENTOMIRA_MODO_LOCAL=1 uvicorn app.main:app --reload
python -m pytest tests -q
```

Worker (needs a CUDA GPU and a YOLO weights file you train yourself):

```bash
cd worker
pip install -r requirements.txt
SERVER_BASE_URL=http://127.0.0.1:8000 WORKER_API_KEY=... WORKER_MODEL_PATH=models/best.pt python run_worker_consola.py
```

Trained weights and the field dataset are not included.

## Security

Outside local mode the server will not start with missing or example API keys. Never commit
`.env` files. Report vulnerabilities privately to the maintainer instead of opening a public issue.

## License

GNU Affero General Public License v3.0. See `LICENSE`. If you run a modified version as a network
service, you must make your source code available to its users.
