"""Controlador de ventas: Punto de Venta (POS), boletas, comprobantes y notas de crédito."""
from __future__ import annotations

import json
import os
from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import db
from ..acceso import ADMIN, VENDEDOR, rol_requerido, solo_admin
from ..errors import ErrorNegocio
from ..services import documentos
from ..services import inventario as inv
from ..services import ventas as svc

bp = Blueprint("ventas", __name__)


def _rango() -> tuple[str, str]:
    """Rango de fechas de los filtros (por defecto, el mes en curso)."""
    hoy = date.today()
    desde = request.args.get("desde") or hoy.replace(day=1).isoformat()
    hasta = request.args.get("hasta") or hoy.isoformat()
    try:
        date.fromisoformat(desde)
        date.fromisoformat(hasta)
    except ValueError:
        desde, hasta = hoy.replace(day=1).isoformat(), hoy.isoformat()
    return desde, hasta


def _venta_visible(venta_id: int) -> dict:
    """Obtiene la venta validando empresa (RN01) y, para vendedores, que sea propia."""
    venta = svc.obtener_venta(g.empresa["id"], venta_id)
    if venta is None:
        abort(404)
    if g.usuario["rol"] != ADMIN and venta["trabajador_id"] != g.usuario["trabajador_id"]:
        abort(403)
    return venta


@bp.route("/")
@rol_requerido(VENDEDOR)
def pos():
    return render_template(
        "ventas/pos.html", medios=svc.MEDIOS_PAGO, comprobante_obligatorio=current_app.config["COMPROBANTE_OBLIGATORIO"]
    )


@bp.route("/<int:venta_id>/pdf")
@rol_requerido(VENDEDOR)
def pdf(venta_id: int):
    """Descarga el comprobante (boleta o factura) en PDF."""
    venta = _venta_visible(venta_id)
    contenido = documentos.generar_pdf(venta, g.empresa)
    disposicion = "inline" if request.args.get("ver") else "attachment"
    return Response(contenido, mimetype="application/pdf", headers={
        "Content-Disposition": f'{disposicion}; filename="{documentos.nombre_archivo(venta)}"',
        "Cache-Control": "private, no-store",
    })


@bp.route("/api/producto")
@rol_requerido(VENDEDOR)
def api_producto():
    """Verificación de stock en tiempo real para el POS (lectura de código de barras)."""
    producto = inv.buscar_producto(g.empresa["id"], request.args.get("sku", ""))
    if producto is None or not producto["activo"]:
        return jsonify(error="Código no encontrado."), 404
    return jsonify(
        sku=producto["codigo_sku"], nombre=producto["nombre"], marca=producto["marca"],
        categoria=producto["categoria"], precio=float(producto["precio_venta"]),
        meses_garantia=producto["meses_garantia"],
        stock_tienda=producto["stock_tienda"], stock_bodega=producto["stock_bodega"],
    )


@bp.route("/api/confirmar", methods=["POST"])
@rol_requerido(VENDEDOR)
def api_confirmar():
    """Confirma la venta (multipart: items en JSON, medio_pago y comprobante)."""
    try:
        items = json.loads(request.form.get("items", "[]"))
    except ValueError:
        return jsonify(error="Formato de venta inválido."), 400
    try:
        resultado = svc.confirmar_venta(
            g.empresa["id"], g.usuario["id"], g.usuario["trabajador_id"], items,
            request.form.get("medio_pago", ""), request.files.get("comprobante"),
            tipo_documento=request.form.get("tipo_documento", "BOLETA"),
            descuento=request.form.get("descuento", "0"),
            cliente=request.form,
        )
    except ErrorNegocio as error:
        return jsonify(error=str(error)), 409
    return jsonify(
        ok=True, folio=resultado["folio"], tipo_documento=resultado["tipo_documento"], total=float(resultado["total"]),
        url_boleta=url_for("ventas.boleta", venta_id=resultado["venta_id"]),
        url_pdf=url_for("ventas.pdf", venta_id=resultado["venta_id"]),
    )


@bp.route("/historial")
@rol_requerido(VENDEDOR)
def historial():
    desde, hasta = _rango()
    es_admin = g.usuario["rol"] == ADMIN
    trabajador_id = request.args.get("vendedor", type=int) if es_admin else g.usuario["trabajador_id"]
    estado = request.args.get("estado") or None
    filas = svc.listar_ventas(g.empresa["id"], desde, hasta, trabajador_id, estado)
    vendedores = db.query(
        """SELECT t.id, CONCAT(t.nombre, ' ', t.apellido) AS nombre FROM trabajadores t
           JOIN usuarios u ON u.id = t.usuario_id WHERE t.empresa_id = %s ORDER BY t.nombre""",
        (g.empresa["id"],),
    ) if es_admin else []
    confirmadas = [v for v in filas if v["estado"] == "CONFIRMADA"]
    resumen = {"cantidad": len(confirmadas), "monto": sum(v["monto_total"] for v in confirmadas),
               "unidades": sum(v["unidades"] or 0 for v in confirmadas)}
    return render_template("ventas/historial.html", ventas=filas, desde=desde, hasta=hasta, vendedores=vendedores,
                           vendedor=trabajador_id, estado=estado, resumen=resumen, medios=svc.MEDIOS_PAGO,
                           tipos=svc.TIPOS_DOCUMENTO)


@bp.route("/<int:venta_id>")
@rol_requerido(VENDEDOR)
def boleta(venta_id: int):
    return render_template("ventas/boleta.html", v=_venta_visible(venta_id), medios=svc.MEDIOS_PAGO,
                           tipos=svc.TIPOS_DOCUMENTO)


@bp.route("/<int:venta_id>/comprobante", methods=["POST"])
@rol_requerido(VENDEDOR)
def adjuntar(venta_id: int):
    try:
        svc.adjuntar_comprobante(g.empresa["id"], g.usuario, venta_id, request.files.get("comprobante"))
        flash("Comprobante adjuntado.", "success")
    except ErrorNegocio as error:
        flash(str(error), "danger")
    return redirect(url_for("ventas.boleta", venta_id=venta_id))


@bp.route("/comprobantes/<int:comprobante_id>")
@rol_requerido(VENDEDOR)
def ver_comprobante(comprobante_id: int):
    """Entrega el archivo solo si la venta pertenece a la empresa y al usuario autorizado."""
    fila = db.query_one(
        """SELECT c.archivo_url, v.id AS venta_id FROM comprobantes c JOIN ventas v ON v.id = c.venta_id
           WHERE c.id = %s AND v.empresa_id = %s""",
        (comprobante_id, g.empresa["id"]),
    )
    if fila is None:
        abort(404)
    _venta_visible(fila["venta_id"])
    carpeta, nombre = os.path.split(fila["archivo_url"])
    return send_from_directory(os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], carpeta), nombre)


@bp.route("/<int:venta_id>/nota-credito", methods=["POST"])
@solo_admin
def nota_credito(venta_id: int):
    """Anulación de una venta mediante nota de crédito autorizada por el administrador (RN06)."""
    try:
        numero = svc.anular_con_nota_credito(g.empresa["id"], g.usuario["id"], venta_id, request.form.get("motivo", ""))
        flash(f"Nota de crédito N° {numero} emitida. La venta quedó anulada y el stock volvió a tienda.", "success")
    except ErrorNegocio as error:
        flash(str(error), "danger")
    return redirect(url_for("ventas.boleta", venta_id=venta_id))
