"""Controlador del Administrador: dashboard de KPIs, trabajadores, pérdidas, auditoría y configuración."""
from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import db
from ..acceso import solo_admin
from ..errors import ErrorNegocio
from ..services import cuentas, kpis
from ..services.inventario import DIAS_ALERTA_VENCIMIENTO, MOTIVOS_MERMA, lotes_por_vencer
from ..services.ventas import guardar_archivo

bp = Blueprint("admin", __name__)
ESTADOS = ("PENDIENTE", "ACTIVO", "BAJA", "RECHAZADO")


def _rango() -> tuple[str, str]:
    hoy = date.today()
    desde = request.args.get("desde") or hoy.replace(day=1).isoformat()
    hasta = request.args.get("hasta") or hoy.isoformat()
    try:
        if date.fromisoformat(desde) > date.fromisoformat(hasta):
            desde, hasta = hasta, desde
    except ValueError:
        desde, hasta = hoy.replace(day=1).isoformat(), hoy.isoformat()
    return desde, hasta


def _conteo_estados() -> dict[str, int]:
    return {
        f["estado"]: f["total"]
        for f in db.query(
            "SELECT estado, COUNT(*) AS total FROM usuarios WHERE empresa_id = %s AND rol_id <> 1 GROUP BY estado",
            (g.empresa["id"],),
        )
    }


@bp.route("/")
@solo_admin
def dashboard():
    desde, hasta = _rango()
    datos = kpis.calcular(g.empresa["id"], desde, hasta)
    criticos = db.query(
        """SELECT p.id, p.codigo_sku, p.nombre, i.stock_bodega, i.stock_tienda, i.stock_critico
           FROM inventario i JOIN productos p ON p.id = i.producto_id
           WHERE i.empresa_id = %s AND p.activo = 1 AND i.stock_bodega + i.stock_tienda <= i.stock_critico
           ORDER BY (i.stock_bodega + i.stock_tienda), p.nombre LIMIT 10""",
        (g.empresa["id"],),
    )
    dias_prueba = max((g.empresa["prueba_hasta"] - datetime.now()).days, 0)
    return render_template("admin/dashboard.html", k=datos, desde=desde, hasta=hasta, criticos=criticos,
                           conteo=_conteo_estados(), dias_prueba=dias_prueba, motivos=MOTIVOS_MERMA,
                           por_vencer=lotes_por_vencer(g.empresa["id"]), dias_alerta=DIAS_ALERTA_VENCIMIENTO)


# ---------------------------------------------------------------------------
# Trabajadores
# ---------------------------------------------------------------------------
@bp.route("/trabajadores")
@solo_admin
def trabajadores():
    estado = request.args.get("estado", "ACTIVO")
    if estado not in ESTADOS:
        estado = "ACTIVO"
    lista = db.query(
        """SELECT u.id, u.estado, u.creado_en, u.ultimo_ingreso, u.debe_cambiar_password, r.nombre_rol,
                  t.rut, t.nombre, t.apellido
           FROM usuarios u JOIN roles r ON r.id = u.rol_id JOIN trabajadores t ON t.usuario_id = u.id
           WHERE u.empresa_id = %s AND r.codigo <> 'ADMIN' AND u.estado = %s ORDER BY u.creado_en DESC""",
        (g.empresa["id"], estado),
    )
    return render_template("admin/trabajadores.html", lista=lista, estado=estado, conteo=_conteo_estados())


@bp.route("/trabajadores/nuevo", methods=["GET", "POST"])
@solo_admin
def nuevo_trabajador():
    if request.method == "POST":
        try:
            usuario_id, temporal = cuentas.crear_trabajador(g.empresa["id"], request.form)
        except ErrorNegocio as error:
            flash(str(error), "danger")
            return render_template("admin/nuevo_trabajador.html", form=request.form)
        t = cuentas.obtener_trabajador(g.empresa["id"], usuario_id)
        return render_template("admin/credencial.html", t=t, temporal=temporal)
    return render_template("admin/nuevo_trabajador.html", form={})


@bp.route("/trabajadores/<int:usuario_id>")
@solo_admin
def ficha(usuario_id: int):
    t = cuentas.obtener_trabajador(g.empresa["id"], usuario_id)
    if t is None:
        abort(404)
    ventas = db.query(
        """SELECT v.*, (SELECT SUM(d.cantidad) FROM detalle_ventas d WHERE d.venta_id = v.id) AS unidades
           FROM ventas v WHERE v.trabajador_id = %s AND v.empresa_id = %s ORDER BY v.fecha_hora DESC LIMIT 50""",
        (t["trabajador_id"], g.empresa["id"]),
    )
    resumen = db.query_one(
        """SELECT COUNT(*) AS cantidad, COALESCE(SUM(monto_total), 0) AS monto FROM ventas
           WHERE trabajador_id = %s AND empresa_id = %s AND estado = 'CONFIRMADA'""",
        (t["trabajador_id"], g.empresa["id"]),
    )
    movimientos = db.query(
        """SELECT m.*, p.codigo_sku, p.nombre FROM movimientos_inventario m JOIN productos p ON p.id = m.producto_id
           WHERE m.usuario_id = %s AND m.empresa_id = %s ORDER BY m.fecha_hora DESC LIMIT 50""",
        (usuario_id, g.empresa["id"]),
    )
    actividad = db.query(
        "SELECT * FROM auditoria WHERE usuario_id = %s AND empresa_id = %s ORDER BY fecha_hora DESC LIMIT 40",
        (usuario_id, g.empresa["id"]),
    )
    return render_template("admin/ficha.html", t=t, ventas=ventas, resumen=resumen, movimientos=movimientos,
                           actividad=actividad)


@bp.route("/trabajadores/<int:usuario_id>/estado/<accion>", methods=["POST"])
@solo_admin
def cambiar_estado(usuario_id: int, accion: str):
    try:
        flash(cuentas.cambiar_estado(g.empresa["id"], usuario_id, accion), "success")
    except ErrorNegocio as error:
        flash(str(error), "warning")
    if request.form.get("volver") == "ficha":
        return redirect(url_for("admin.ficha", usuario_id=usuario_id))
    return redirect(url_for("admin.trabajadores", estado=request.form.get("estado_lista", "PENDIENTE")))


@bp.route("/trabajadores/<int:usuario_id>/credencial", methods=["POST"])
@solo_admin
def restablecer(usuario_id: int):
    try:
        temporal = cuentas.restablecer_password(g.empresa["id"], usuario_id)
    except ErrorNegocio as error:
        flash(str(error), "danger")
        return redirect(url_for("admin.trabajadores"))
    return render_template("admin/credencial.html", t=cuentas.obtener_trabajador(g.empresa["id"], usuario_id),
                           temporal=temporal)


# ---------------------------------------------------------------------------
# Auditoría de pérdidas y bitácora
# ---------------------------------------------------------------------------
@bp.route("/perdidas")
@solo_admin
def perdidas():
    desde, hasta = _rango()
    motivo = request.args.get("motivo", "")
    filtros = ["pe.empresa_id = %s", "pe.fecha_hora >= %s", "pe.fecha_hora < %s + INTERVAL 1 DAY"]
    params: list = [g.empresa["id"], desde, hasta]
    if motivo in MOTIVOS_MERMA:
        filtros.append("pe.motivo = %s")
        params.append(motivo)
    filas = db.query(
        f"""SELECT pe.*, p.codigo_sku, p.nombre, CONCAT(t.nombre, ' ', t.apellido) AS responsable
            FROM perdidas pe JOIN productos p ON p.id = pe.producto_id JOIN trabajadores t ON t.usuario_id = pe.usuario_id
            WHERE {' AND '.join(filtros)} ORDER BY pe.fecha_hora DESC LIMIT 500""",
        params,
    )
    total = {"unidades": sum(f["cantidad"] for f in filas), "costo": sum(f["costo_total"] for f in filas)}
    return render_template("admin/perdidas.html", filas=filas, total=total, desde=desde, hasta=hasta,
                           motivo=motivo, motivos=MOTIVOS_MERMA)


@bp.route("/auditoria")
@solo_admin
def auditoria():
    desde, hasta = _rango()
    entidad = request.args.get("entidad", "")
    filtros = ["a.empresa_id = %s", "a.fecha_hora >= %s", "a.fecha_hora < %s + INTERVAL 1 DAY"]
    params: list = [g.empresa["id"], desde, hasta]
    if entidad:
        filtros.append("a.entidad = %s")
        params.append(entidad[:50])
    filas = db.query(
        f"""SELECT a.*, CONCAT(t.nombre, ' ', t.apellido) AS usuario, r.nombre_rol
            FROM auditoria a LEFT JOIN trabajadores t ON t.usuario_id = a.usuario_id
            LEFT JOIN usuarios u ON u.id = a.usuario_id LEFT JOIN roles r ON r.id = u.rol_id
            WHERE {' AND '.join(filtros)} ORDER BY a.fecha_hora DESC, a.id DESC LIMIT 500""",
        params,
    )
    entidades = [f["entidad"] for f in db.query(
        "SELECT DISTINCT entidad FROM auditoria WHERE empresa_id = %s ORDER BY entidad", (g.empresa["id"],))]
    return render_template("admin/auditoria.html", filas=filas, desde=desde, hasta=hasta, entidad=entidad,
                           entidades=entidades)


# ---------------------------------------------------------------------------
# Configuración de la PYME
# ---------------------------------------------------------------------------
@bp.route("/configuracion", methods=["GET", "POST"])
@solo_admin
def configuracion():
    if request.method == "POST":
        try:
            logo_url = None
            archivo = request.files.get("logo")
            if archivo and archivo.filename:
                logo_url, _ = guardar_archivo(archivo, "logos", {".png"}, privado=False)
            cuentas.actualizar_empresa(g.empresa["id"], request.form, logo_url)
            flash("Configuración guardada.", "success")
        except ErrorNegocio as error:
            flash(str(error), "danger")
        return redirect(url_for("admin.configuracion"))
    enlace = url_for("auth.login", empresa=g.empresa["rut"], _external=True)
    return render_template("admin/configuracion.html", enlace=enlace)
