"""Autenticación por cabecera X-API-Key para dispositivos y worker."""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from . import config


def require_device(x_api_key: str = Header(default="")) -> None:
    if x_api_key != config.DEVICE_API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="API key de dispositivo inválida")


def require_worker(x_api_key: str = Header(default="")) -> None:
    if x_api_key != config.WORKER_API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="API key de worker inválida")
