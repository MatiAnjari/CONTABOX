"""Control de acceso RBAC (RN02) y vigencia del plan de la empresa.

El rol y la empresa se leen siempre desde la base de datos en cada petición
(ver `cargar_usuario` en app/__init__.py), nunca desde datos enviados por el cliente.
"""
from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Any, Callable

from flask import abort, flash, g, jsonify, redirect, request, url_for

ADMIN, BODEGUERO, VENDEDOR = "ADMIN", "BODEGUERO", "VENDEDOR"

INICIO_POR_ROL = {
    ADMIN: "admin.dashboard",
    BODEGUERO: "bodega.inventario",
    VENDEDOR: "ventas.pos",
}


def plan_vigente(empresa: dict[str, Any]) -> bool:
    """True si la empresa está dentro del mes gratis o tiene suscripción vigente."""
    ahora = datetime.now()
    if empresa["prueba_hasta"] >= ahora:
        return True
    return bool(empresa["suscripcion_hasta"] and empresa["suscripcion_hasta"] >= ahora)


def _es_api() -> bool:
    return request.path.startswith(("/api/", "/ventas/api/", "/bodega/api/")) or request.is_json


def rol_requerido(*roles: str) -> Callable:
    """Restringe una vista a los roles indicados. Responde 403 ante cualquier otro rol.

    El administrador tiene acceso a todas las vistas de su propia empresa.
    """

    def decorador(vista: Callable) -> Callable:
        @wraps(vista)
        def envoltura(*args: Any, **kwargs: Any):
            if g.get("usuario") is None:
                if _es_api():
                    return jsonify(error="Sesión no iniciada."), 401
                flash("Debes iniciar sesión para continuar.", "warning")
                return redirect(url_for("auth.login", next=request.path))
            if not plan_vigente(g.empresa):
                if _es_api():
                    return jsonify(error="El plan de la empresa no está vigente."), 402
                return redirect(url_for("auth.plan_vencido"))
            rol = g.usuario["rol"]
            if rol != ADMIN and rol not in roles:
                if _es_api():
                    return jsonify(error="No tienes permiso para esta acción."), 403
                abort(403)
            return vista(*args, **kwargs)

        return envoltura

    return decorador


def solo_admin(vista: Callable) -> Callable:
    """Atajo para vistas exclusivas del administrador."""
    return rol_requerido(ADMIN)(vista)
