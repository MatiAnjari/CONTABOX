"""Fábrica de la aplicación ContaBox (capa de presentación + controladores)."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal

from flask import Flask, g, jsonify, render_template, request, session
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db
from .acceso import ADMIN, BODEGUERO, VENDEDOR
from .config import Config
from .utils.rut import formatear_rut

csrf = CSRFProtect()

ENDPOINTS_LIBRES = {"auth.cambiar_password", "auth.logout", "static"}


def _pesos(valor) -> str:
    return "$" + f"{float(valor or 0):,.0f}".replace(",", ".")


def _numero(valor, decimales: int = 0) -> str:
    texto = f"{float(valor or 0):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def create_app() -> Flask:
    """Crea y configura la aplicación Flask."""
    app = Flask(__name__)
    app.config.from_object(Config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # detrás de Nginx
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "logos"), exist_ok=True)
    os.makedirs(os.path.join(app.config["PRIVATE_UPLOAD_FOLDER"], "comprobantes"), exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)

    from .admin.routes import bp as admin_bp
    from .auth.routes import bp as auth_bp
    from .bodega.routes import bp as bodega_bp
    from .ventas.routes import bp as ventas_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(bodega_bp, url_prefix="/bodega")
    app.register_blueprint(ventas_bp, url_prefix="/ventas")

    @app.before_request
    def cargar_usuario():
        """Carga el usuario, su rol y su empresa desde la base en cada petición (RN01/RN02)."""
        g.usuario = None
        g.empresa = None
        usuario_id = session.get("usuario_id")
        if usuario_id is None:
            return None
        usuario = db.query_one(
            """SELECT u.*, r.codigo AS rol, r.nombre_rol, t.id AS trabajador_id, t.rut,
                      t.nombre, t.apellido, t.cargo
               FROM usuarios u
               JOIN roles r ON r.id = u.rol_id
               JOIN trabajadores t ON t.usuario_id = u.id
               WHERE u.id = %s""",
            (usuario_id,),
        )
        # Si el trabajador fue dado de baja mientras estaba conectado, se cierra su sesión.
        if usuario is None or usuario["estado"] != "ACTIVO":
            session.clear()
            return None
        g.usuario = usuario
        g.empresa = db.query_one("SELECT * FROM empresas WHERE id = %s", (usuario["empresa_id"],))

        if usuario["debe_cambiar_password"] and request.endpoint not in ENDPOINTS_LIBRES:
            from flask import redirect, url_for

            return redirect(url_for("auth.cambiar_password"))
        return None

    def _fondo_perfil() -> str:
        """Imagen de fondo según el perfil: el vendedor en Ventas y el bodeguero en Bodega."""
        usuario = g.get("usuario")
        if not usuario or usuario["debe_cambiar_password"]:
            return ""
        fondos = {(VENDEDOR, "ventas"): "fondo-panel fondo-ventas", (BODEGUERO, "bodega"): "fondo-panel fondo-bodega"}
        return fondos.get((usuario["rol"], request.blueprint), "")

    @app.context_processor
    def variables_globales():
        empresa = g.get("empresa")
        return {
            "usuario_actual": g.get("usuario"),
            "empresa_actual": empresa,
            "es_admin": bool(g.get("usuario") and g.usuario["rol"] == ADMIN),
            "nombre_marca": empresa["nombre_fantasia"] if empresa else "ContaBox",
            "ahora": datetime.now(),
            "fondo_perfil": _fondo_perfil(),
        }

    app.jinja_env.filters["rut"] = formatear_rut
    app.jinja_env.filters["fecha"] = lambda d: d.strftime("%d-%m-%Y %H:%M") if d else "—"
    app.jinja_env.filters["dia"] = lambda d: d.strftime("%d-%m-%Y") if d else "—"
    app.jinja_env.filters["pesos"] = _pesos
    app.jinja_env.filters["numero"] = _numero
    app.jinja_env.filters["etiqueta"] = lambda s: (s or "").replace("_", " ").capitalize()

    def _respuesta_error(codigo: int, plantilla: str, mensaje: str):
        if request.path.startswith(("/ventas/api/", "/bodega/api/")):
            return jsonify(error=mensaje), codigo
        return render_template(plantilla, mensaje=mensaje), codigo

    @app.errorhandler(403)
    def prohibido(_e):
        return _respuesta_error(403, "errores/403.html", "No tienes permiso para esta sección.")

    @app.errorhandler(404)
    def no_encontrado(_e):
        return _respuesta_error(404, "errores/404.html", "Recurso no encontrado.")

    @app.errorhandler(413)
    def muy_grande(_e):
        return _respuesta_error(413, "errores/error.html", "El archivo supera el máximo de 5 MB.")

    @app.errorhandler(CSRFError)
    def csrf_invalido(_e):
        return _respuesta_error(400, "errores/error.html", "La sesión del formulario expiró. Recarga la página e inténtalo de nuevo.")

    @app.errorhandler(Exception)
    def error_interno(error):
        """Registra la traza en el servidor y muestra un mensaje genérico al cliente."""
        from werkzeug.exceptions import HTTPException

        if isinstance(error, HTTPException):
            return error
        app.logger.exception("Error no controlado en %s", request.path)
        return _respuesta_error(500, "errores/error.html", "Ocurrió un error inesperado. Inténtalo nuevamente.")

    app.json.default = lambda o: float(o) if isinstance(o, Decimal) else str(o)  # type: ignore[attr-defined]
    return app
