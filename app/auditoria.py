"""Trazabilidad (RN05): registro de cada operación de escritura."""
from __future__ import annotations

from typing import Any

from flask import g, has_request_context, request


def auditar(
    cur: Any,
    accion: str,
    entidad: str,
    entidad_id: int | None = None,
    detalle: str | None = None,
    empresa_id: int | None = None,
    usuario_id: int | None = None,
) -> None:
    """Inserta un registro de auditoría dentro de la transacción en curso.

    Por defecto toma la empresa y el usuario de la sesión activa.
    """
    usuario = g.get("usuario") if has_request_context() else None
    if empresa_id is None and usuario:
        empresa_id = usuario["empresa_id"]
    if usuario_id is None and usuario:
        usuario_id = usuario["id"]
    ip = request.remote_addr if has_request_context() else None
    cur.execute(
        """INSERT INTO auditoria (empresa_id, usuario_id, accion, entidad, entidad_id, detalle, ip)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (empresa_id, usuario_id, accion, entidad, entidad_id, (detalle or "")[:500] or None, ip),
    )
