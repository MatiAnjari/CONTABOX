"""Controlador de bodega (rol Bodeguero y Administrador)."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import db
from ..acceso import BODEGUERO, rol_requerido
from ..errors import ErrorNegocio
from ..services import catalogo
from ..services import inventario as inv

bp = Blueprint("bodega", __name__)


def _ejecutar(operacion, mensaje, destino: str):
    """Ejecuta una operación de negocio y muestra el resultado o el error."""
    try:
        resultado = operacion()
    except ErrorNegocio as error:
        flash(str(error), "danger")
        return redirect(url_for(destino, sku=request.form.get("codigo_sku", "")))
    flash(mensaje(resultado), "success")
    return redirect(url_for(destino))


@bp.route("/")
@rol_requerido(BODEGUERO)
def inventario():
    buscar = request.args.get("q", "").strip()[:60]
    criticos = request.args.get("criticos") == "1"
    categoria_id = request.args.get("categoria", type=int)
    marca_id = request.args.get("marca", type=int)
    productos = inv.listar_inventario(g.empresa["id"], buscar, criticos, categoria_id, marca_id)
    alertas = sum(1 for p in productos if p["stock_total"] <= p["stock_critico"]) if not criticos else len(productos)
    return render_template("bodega/inventario.html", productos=productos, buscar=buscar, criticos=criticos, alertas=alertas,
                           categorias=catalogo.listar_categorias(g.empresa["id"]), marcas=catalogo.listar_marcas(g.empresa["id"]),
                           categoria_id=categoria_id, marca_id=marca_id, por_vencer=inv.lotes_por_vencer(g.empresa["id"]),
                           dias_alerta=inv.DIAS_ALERTA_VENCIMIENTO)


@bp.route("/api/producto")
@rol_requerido(BODEGUERO)
def api_producto():
    """Consulta asíncrona (fetch) de un SKU para autocompletar formularios."""
    producto = inv.buscar_producto(g.empresa["id"], request.args.get("sku", ""))
    if producto is None:
        return jsonify(encontrado=False)
    return jsonify(encontrado=True, producto=producto)


@bp.route("/lotes", methods=["GET", "POST"])
@rol_requerido(BODEGUERO)
def lotes():
    if request.method == "POST":
        return _ejecutar(
            lambda: inv.ingresar_lote(g.empresa["id"], g.usuario["id"], request.form),
            lambda r: f"Lote {r['numero_lote']} registrado: {r['bultos']} x {r['por_bulto']} = {r['cantidad']} u. "
                      f"de {r['nombre']} (inversión ${r['inversion']:,.0f}).".replace(",", "."),
            "bodega.lotes",
        )
    recientes = db.query(
        """SELECT l.*, p.codigo_sku, p.nombre, CONCAT(t.nombre, ' ', t.apellido) AS responsable
           FROM lotes l JOIN productos p ON p.id = l.producto_id JOIN trabajadores t ON t.usuario_id = l.usuario_id
           WHERE l.empresa_id = %s ORDER BY l.fecha_ingreso DESC, l.id DESC LIMIT 30""",
        (g.empresa["id"],),
    )
    return render_template("bodega/lotes.html", recientes=recientes, sku=request.args.get("sku", ""),
                           categorias=catalogo.listar_categorias(g.empresa["id"]), marcas=catalogo.listar_marcas(g.empresa["id"]))


@bp.route("/transferencias", methods=["GET", "POST"])
@rol_requerido(BODEGUERO)
def transferencias():
    if request.method == "POST":
        return _ejecutar(
            lambda: inv.transferir(g.empresa["id"], g.usuario["id"], request.form.get("codigo_sku", ""),
                                   request.form.get("cantidad"), request.form.get("origen", "BODEGA")),
            lambda r: f"{r['cantidad']} u. de {r['nombre']} transferidas de {r['origen'].lower()} a {r['destino'].lower()}.",
            "bodega.transferencias",
        )
    recientes = db.query(
        """SELECT m.*, p.codigo_sku, p.nombre, CONCAT(t.nombre, ' ', t.apellido) AS responsable
           FROM movimientos_inventario m JOIN productos p ON p.id = m.producto_id
           JOIN trabajadores t ON t.usuario_id = m.usuario_id
           WHERE m.empresa_id = %s AND m.tipo_movimiento = 'TRANSFERENCIA'
           ORDER BY m.fecha_hora DESC, m.id DESC LIMIT 30""",
        (g.empresa["id"],),
    )
    return render_template("bodega/transferencias.html", recientes=recientes, sku=request.args.get("sku", ""))


@bp.route("/mermas", methods=["GET", "POST"])
@rol_requerido(BODEGUERO)
def mermas():
    if request.method == "POST":
        return _ejecutar(
            lambda: inv.registrar_merma(g.empresa["id"], g.usuario["id"], request.form),
            lambda r: f"Merma registrada: {r['cantidad']} u. de {r['nombre']} por {r['motivo'].lower()} "
                      f"(pérdida ${r['costo_total']:,.0f}).".replace(",", "."),
            "bodega.mermas",
        )
    recientes = db.query(
        """SELECT pe.*, p.codigo_sku, p.nombre, CONCAT(t.nombre, ' ', t.apellido) AS responsable
           FROM perdidas pe JOIN productos p ON p.id = pe.producto_id JOIN trabajadores t ON t.usuario_id = pe.usuario_id
           WHERE pe.empresa_id = %s ORDER BY pe.fecha_hora DESC, pe.id DESC LIMIT 30""",
        (g.empresa["id"],),
    )
    return render_template("bodega/mermas.html", recientes=recientes, motivos=inv.MOTIVOS_MERMA,
                           sku=request.args.get("sku", ""))


@bp.route("/conteos", methods=["GET", "POST"])
@rol_requerido(BODEGUERO)
def conteos():
    if request.method == "POST":
        return _ejecutar(
            lambda: inv.registrar_conteo(g.empresa["id"], g.usuario["id"], request.form.get("codigo_sku", ""),
                                         request.form.get("stock_fisico"), request.form.get("observacion", "")),
            lambda r: f"Conteo de {r['nombre']}: sistema {r['sistema']}, físico {r['fisico']} "
                      f"(diferencia {r['diferencia']:+d}).",
            "bodega.conteos",
        )
    recientes = db.query(
        """SELECT c.*, p.codigo_sku, p.nombre, CONCAT(t.nombre, ' ', t.apellido) AS responsable
           FROM conteos_inventario c JOIN productos p ON p.id = c.producto_id JOIN trabajadores t ON t.usuario_id = c.usuario_id
           WHERE c.empresa_id = %s ORDER BY c.fecha_hora DESC, c.id DESC LIMIT 30""",
        (g.empresa["id"],),
    )
    return render_template("bodega/conteos.html", recientes=recientes, sku=request.args.get("sku", ""))


@bp.route("/movimientos")
@rol_requerido(BODEGUERO)
def movimientos():
    tipo = request.args.get("tipo", "")
    filtros = ["m.empresa_id = %s"]
    params: list = [g.empresa["id"]]
    if tipo in ("INGRESO_LOTE", "TRANSFERENCIA", "VENTA", "MERMA", "ANULACION_VENTA"):
        filtros.append("m.tipo_movimiento = %s")
        params.append(tipo)
    filas = db.query(
        f"""SELECT m.*, p.codigo_sku, p.nombre, CONCAT(t.nombre, ' ', t.apellido) AS responsable
            FROM movimientos_inventario m JOIN productos p ON p.id = m.producto_id
            JOIN trabajadores t ON t.usuario_id = m.usuario_id
            WHERE {' AND '.join(filtros)} ORDER BY m.fecha_hora DESC, m.id DESC LIMIT 300""",
        params,
    )
    return render_template("bodega/movimientos.html", movimientos=filas, tipo=tipo)


@bp.route("/productos/<int:producto_id>", methods=["GET", "POST"])
@rol_requerido(BODEGUERO)
def producto(producto_id: int):
    if request.method == "POST":
        try:
            inv.actualizar_producto(g.empresa["id"], producto_id, request.form)
            flash("Producto actualizado.", "success")
        except ErrorNegocio as error:
            flash(str(error), "danger")
        return redirect(url_for("bodega.producto", producto_id=producto_id))
    fila = db.query_one(
        """SELECT p.*, i.stock_bodega, i.stock_tienda, i.stock_critico, c.nombre AS categoria, m.nombre AS marca
           FROM productos p JOIN inventario i ON i.producto_id = p.id
           JOIN categorias c ON c.id = p.categoria_id JOIN marcas m ON m.id = p.marca_id
           WHERE p.id = %s AND p.empresa_id = %s""",
        (producto_id, g.empresa["id"]),
    )
    if fila is None:
        abort(404)
    lotes_producto = db.query(
        "SELECT * FROM lotes WHERE producto_id = %s AND empresa_id = %s ORDER BY fecha_ingreso DESC, id DESC",
        (producto_id, g.empresa["id"]),
    )
    movs = db.query(
        """SELECT m.*, CONCAT(t.nombre, ' ', t.apellido) AS responsable FROM movimientos_inventario m
           JOIN trabajadores t ON t.usuario_id = m.usuario_id
           WHERE m.producto_id = %s AND m.empresa_id = %s ORDER BY m.fecha_hora DESC, m.id DESC LIMIT 50""",
        (producto_id, g.empresa["id"]),
    )
    return render_template("bodega/producto.html", p=fila, lotes=lotes_producto, movimientos=movs,
                           cajas=inv.cajas(fila["stock_bodega"] + fila["stock_tienda"], fila["unidades_por_bulto"]),
                           categorias=catalogo.listar_categorias(g.empresa["id"]), marcas=catalogo.listar_marcas(g.empresa["id"]))


@bp.route("/catalogo", methods=["GET", "POST"])
@rol_requerido(BODEGUERO)
def catalogo_vista():
    """Catálogo maestro: categorías (alimentos, garantías) y marcas."""
    if request.method == "POST":
        try:
            if request.form.get("tipo") == "marca":
                catalogo.crear_marca(g.empresa["id"], request.form.get("nombre"))
                flash("Marca creada.", "success")
            else:
                categoria_id = request.form.get("categoria_id", type=int)
                catalogo.guardar_categoria(g.empresa["id"], request.form, categoria_id)
                flash("Categoría guardada.", "success")
        except ErrorNegocio as error:
            flash(str(error), "danger")
        return redirect(url_for("bodega.catalogo_vista"))
    return render_template("bodega/catalogo.html", categorias=catalogo.listar_categorias(g.empresa["id"], solo_activas=False),
                           marcas=catalogo.listar_marcas(g.empresa["id"]))
