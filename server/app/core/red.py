"""Qué IPs de trampa puede contactar el servidor.

La IP la declara la propia trampa (telemetría e ingesta) y el relay directo del ESP32 hace
peticiones a `http://<ip>:81`. Sin filtro, quien pueda registrar una trampa hace que la master
llame a donde quiera: localhost, el túnel WireGuard o cualquier otra máquina (SSRF). Solo se
contacta una dirección IPv4 privada de LAN; el resto se guarda para mostrarse, pero no se usa.
"""
from __future__ import annotations

import ipaddress

from . import config

# Privadas pero NO de cámaras: el túnel con el VPS (WireGuard).
_VETADAS = (ipaddress.ip_network("10.77.0.0/24"),)


def ip_valida(ip: str | None) -> str | None:
    """La IP normalizada si es una dirección IPv4 o IPv6 válida; si no, None."""
    if not ip or not isinstance(ip, str):
        return None
    try:
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError:
        return None


def _redes_camaras() -> list:
    redes = []
    for r in config.REDES_CAMARAS:
        try:
            redes.append(ipaddress.ip_network(r, strict=False))
        except ValueError:
            continue
    return redes


def ip_de_camara(ip: str | None) -> str | None:
    """La IP si el servidor puede contactarla: IPv4 privada DENTRO de las subredes de cámaras
    configuradas (`ENTOMIRA_REDES_CAMARAS`) y que no sea su puerta de enlace; si no, None."""
    ip = ip_valida(ip)
    if ip is None:
        return None
    dir_ = ipaddress.ip_address(ip)
    if (dir_.version != 4 or not dir_.is_private or dir_.is_loopback or dir_.is_link_local
            or dir_.is_multicast or dir_.is_unspecified or dir_.is_reserved
            or any(dir_ in red for red in _VETADAS)):
        return None
    for red in _redes_camaras():
        if dir_ in red and dir_ not in (red.network_address, red.network_address + 1,
                                        red.broadcast_address):
            return ip
    return None
