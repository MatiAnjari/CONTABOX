"""Configuración de la aplicación leída desde variables de entorno (.env)."""
from __future__ import annotations

import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _bool(nombre: str, defecto: str = "0") -> bool:
    return os.getenv(nombre, defecto).strip().lower() in ("1", "true", "si", "sí", "yes")


class Config:
    """Parámetros de Flask, MySQL y reglas de negocio configurables."""

    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-cambiar")
    DEBUG: bool = _bool("APP_DEBUG")

    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "contabox")

    DIAS_PRUEBA: int = int(os.getenv("DIAS_PRUEBA", "30"))
    COMPROBANTE_OBLIGATORIO: bool = _bool("COMPROBANTE_OBLIGATORIO", "1")

    # Sesión segura
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    SESSION_COOKIE_SECURE: bool = _bool("SESSION_COOKIE_SECURE")  # 1 en producción con HTTPS
    PERMANENT_SESSION_LIFETIME = timedelta(hours=10)

    MAX_CONTENT_LENGTH: int = 5 * 1024 * 1024  # 5 MB por archivo
    UPLOAD_FOLDER: str = os.path.join(os.path.dirname(__file__), "static", "uploads")  # públicos (logos)
    PRIVATE_UPLOAD_FOLDER: str = os.getenv(
        "PRIVATE_UPLOAD_FOLDER", os.path.join(os.path.dirname(os.path.dirname(__file__)), "archivos_privados")
    )  # comprobantes de pago: nunca accesibles directamente
