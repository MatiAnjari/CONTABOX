"""Utilidades de seguridad: hash Bcrypt y generación de credenciales temporales."""
from __future__ import annotations

import secrets
import string

import bcrypt

_ALFABETO = string.ascii_letters + string.digits


def hash_password(password: str) -> str:
    """Genera un hash Bcrypt con salt aleatorio (costo 12)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verificar_password(password: str, password_hash: str) -> bool:
    """Compara una contraseña en texto plano contra su hash Bcrypt."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def password_temporal(largo: int = 10) -> str:
    """Crea una contraseña temporal aleatoria criptográficamente segura."""
    return "".join(secrets.choice(_ALFABETO) for _ in range(largo))


def validar_password(password: str, confirmacion: str) -> list[str]:
    """Devuelve la lista de errores de la contraseña (vacía si es válida)."""
    errores: list[str] = []
    if len(password) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")
    if not any(c.isdigit() for c in password) or not any(c.isalpha() for c in password):
        errores.append("La contraseña debe combinar letras y números.")
    if password != confirmacion:
        errores.append("Las contraseñas no coinciden.")
    return errores
