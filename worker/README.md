# worker/ — Nodo de IA (FASE 1, PC Windows · RTX 5060 Ti)

Entrenamiento y servicio de inferencia YOLO (Ultralytics + PyTorch CUDA, **GPU obligatoria**).

- `entomira_worker/` — paquete: entrenamiento, inferencia, cliente del servidor, GUI.
- `build/` — artefactos de PyInstaller para los `.exe` del Escritorio.

Genera en el Escritorio `Worker_IA.exe` y `Servidor_Trampas.exe`.
Diseño en `Docs_Obsidian/07_IA_y_Dataset.md`. Datasets en `C:\ento_ml\datasets`.
