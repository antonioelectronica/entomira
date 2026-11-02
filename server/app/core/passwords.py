"""Hash y verificación de contraseñas (PBKDF2-HMAC-SHA256, stdlib, sin deps nativas).

Formato almacenado: pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>
"""
from __future__ import annotations

import hashlib
import hmac
import os

_ITER = 120_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iter_s))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False
