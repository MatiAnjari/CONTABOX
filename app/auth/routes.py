"""Controlador de autenticación: login, registros, cambio de contraseña y plan vencido."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .. import db
from ..acceso import INICIO_POR_ROL, plan_vigente
from ..auditoria import auditar
from ..errors import ErrorNegocio
from ..services import cuentas
from ..utils.rut import normalizar_rut

bp = Blueprint("auth", __name__)


def _empresa_marca():
    """Empresa cuyo logo y nombre se muestran en el login (?empresa=RUT)."""
    rut = normalizar_rut(request.args.get("empresa", ""))
    return db.query_one("SELECT rut, nombre_fantasia, logo_url FROM empresas WHERE rut = %s", (rut,)) if rut else None


def _iniciar_sesion(usuario_id: int) -> None:
    session.clear()
    session.permanent = True
    session["usuario_id"] = usuario_id


@bp.route("/")
def index():
    if g.usuario:
        return redirect(url_for(INICIO_POR_ROL[g.usuario["rol"]]))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.usuario:
        return redirect(url_for(INICIO_POR_ROL[g.usuario["rol"]]))
    marca = _empresa_marca()
    if request.method == "POST":
        identificador = request.form.get("identificador", "")
        try:
            usuario = cuentas.autenticar(identificador, request.form.get("password", ""))
        except ErrorNegocio as error:
            flash(str(error), "danger")
            return render_template("auth/login.html", identificador=identificador, marca=marca)
        _iniciar_sesion(usuario["id"])
        siguiente = request.args.get("next", "")
        if siguiente.startswith("/") and not siguiente.startswith("//"):
            return redirect(siguiente)
        rol = {1: "ADMIN", 2: "BODEGUERO", 3: "VENDEDOR"}[usuario["rol_id"]]
        return redirect(url_for(INICIO_POR_ROL[rol]))
    return render_template("auth/login.html", marca=marca)


@bp.route("/logout")
def logout():
    if g.usuario:
        with db.transaccion() as cur:
            auditar(cur, "LOGOUT", "usuarios", g.usuario["id"])
        rut_empresa = g.empresa["rut"]
    else:
        rut_empresa = None
    session.clear()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login", empresa=rut_empresa) if rut_empresa else url_for("auth.login"))


@bp.route("/registro/empleador", methods=["GET", "POST"])
def registro_empleador():
    if g.usuario:
        return redirect(url_for("auth.index"))
    if request.method == "POST":
        try:
            usuario_id = cuentas.registrar_empresa(request.form)
        except ErrorNegocio as error:
            flash(str(error), "danger")
            return render_template("auth/registro_empleador.html", form=request.form)
        _iniciar_sesion(usuario_id)
        flash("¡Bienvenido a ContaBox! Tu mes gratis ya está activo.", "success")
        return redirect(url_for("admin.dashboard"))
    return render_template("auth/registro_empleador.html", form={})


@bp.route("/registro/trabajador", methods=["GET", "POST"])
def registro_trabajador():
    if g.usuario:
        return redirect(url_for("auth.index"))
    marca = _empresa_marca()
    if request.method == "POST":
        try:
            cuentas.solicitar_ingreso(request.form)
        except ErrorNegocio as error:
            flash(str(error), "danger")
            return render_template("auth/registro_trabajador.html", form=request.form, marca=marca)
        flash("Solicitud enviada. Podrás ingresar cuando el administrador la apruebe.", "success")
        return redirect(url_for("auth.login", empresa=marca["rut"]) if marca else url_for("auth.login"))
    form = {"nombre_empresa": marca["nombre_fantasia"], "rut_empresa": marca["rut"]} if marca else {}
    return render_template("auth/registro_trabajador.html", form=form, marca=marca)


@bp.route("/cambiar-password", methods=["GET", "POST"])
def cambiar_password():
    if g.usuario is None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        try:
            cuentas.cambiar_password(
                g.usuario, request.form.get("actual", ""), request.form.get("nueva", ""), request.form.get("nueva2", "")
            )
        except ErrorNegocio as error:
            flash(str(error), "danger")
            return render_template("auth/cambiar_password.html")
        flash("Contraseña actualizada.", "success")
        return redirect(url_for("auth.index"))
    return render_template("auth/cambiar_password.html")


@bp.route("/plan-vencido")
def plan_vencido():
    if g.usuario is None:
        return redirect(url_for("auth.login"))
    if plan_vigente(g.empresa):
        return redirect(url_for("auth.index"))
    return render_template("auth/plan_vencido.html")
