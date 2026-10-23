"""Excepciones de negocio. Su mensaje es seguro para mostrar al usuario."""


class ErrorNegocio(Exception):
    """Regla de negocio incumplida (stock insuficiente, datos inválidos, etc.)."""
