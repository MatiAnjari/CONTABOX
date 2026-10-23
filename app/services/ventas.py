"""Lógica de negocio de ventas: confirmación atómica, comprobantes y notas de crédito.

Una venta confirmada es inmutable (RN06): solo puede anularse con una nota de
crédito autorizada por el administrador, que devuelve el stock y revierte el costo.
"""
from __future__ import annotations

import calendar
import os
import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .. import db
from ..auditoria import auditar
from ..errors import ErrorNegocio
from ..utils.rut import normalizar_rut
from .inventario import consumir_fifo, entero_positivo, monto, normalizar_sku

MEDIOS_PAGO = {"EFECTIVO": "Efectivo", "DEBITO": "Débito", "CREDITO": "Crédito", "TRANSFERENCIA": "Transferencia"}
EXTENSIONES_COMPROBANTE = {".png", ".jpg", ".jpeg", ".pdf", ".webp"}
FIRMAS = (b"\x89PNG", b"\xff\xd8\xff", b"%PDF", b"RIFF")
MAX_ITEMS = 100
TIPOS_DOCUMENTO = {"BOLETA": "Boleta", "FACTURA": "Factura"}
TASA_IVA = Decimal("0.19")


def sumar_meses(fecha: date, meses: int) -> date:
    """Suma meses calendario: 31-01 + 1 mes = 28/29-02 (ajusta al último día del mes)."""
    total = fecha.month - 1 + meses
    anio, mes = fecha.year + total // 12, total % 12 + 1
    return date(anio, mes, min(fecha.day, calendar.monthrange(anio, mes)[1]))


def calcular_totales(subtotal: Decimal, descuento: Decimal) -> dict[str, Decimal]:
    """Precios con IVA incluido: total = subtotal - descuento; neto = total / 1,19; IVA = total - neto."""
    total = subtotal - descuento
    neto = (total / (1 + TASA_IVA)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return {"subtotal": subtotal, "descuento": descuento, "total": total, "neto": neto, "iva": total - neto}


def _datos_cliente(tipo_documento: str, cliente: dict[str, Any]) -> tuple[str | None, ...]:
    """Valida los datos del receptor de una factura. La boleta no los requiere."""
    if tipo_documento == "BOLETA":
        return (None, None, None, None)
    rut = normalizar_rut(cliente.get("cliente_rut", ""))
    razon = " ".join(str(cliente.get("cliente_razon_social") or "").split())
    giro = " ".join(str(cliente.get("cliente_giro") or "").split())
    direccion = " ".join(str(cliente.get("cliente_direccion") or "").split())
    errores = []
    if not rut:
        errores.append("RUT del cliente inválido.")
    if not razon:
        errores.append("Ingresa la razón social del cliente.")
    if not giro:
        errores.append("Ingresa el giro del cliente.")
    if not direccion:
        errores.append("Ingresa la dirección del cliente.")
    if errores:
        raise ErrorNegocio("Para emitir factura: " + " ".join(errores))
    return (rut, razon[:150], giro[:100], direccion[:200])


def guardar_archivo(archivo: FileStorage, carpeta: str, extensiones: set[str], privado: bool = True) -> tuple[str, str]:
    """Valida extensión y firma del archivo y lo guarda con nombre aleatorio.

    Los archivos privados (comprobantes) se guardan fuera de /static y solo se
    entregan mediante una ruta que valida empresa y rol. Los públicos (logos) van a /static.
    Devuelve (ruta relativa, nombre original).
    """
    nombre_original = secure_filename(archivo.filename or "") or "archivo"
    extension = os.path.splitext(nombre_original)[1].lower()
    if extension not in extensiones:
        raise ErrorNegocio(f"Formato no permitido. Usa: {', '.join(sorted(extensiones))}.")
    cabecera = archivo.stream.read(8)
    archivo.stream.seek(0)
    if not cabecera.startswith(FIRMAS):
        raise ErrorNegocio("El archivo no parece una imagen o PDF válido.")
    nombre = f"{uuid.uuid4().hex}{extension}"
    base = current_app.config["PRIVATE_UPLOAD_FOLDER" if privado else "UPLOAD_FOLDER"]
    os.makedirs(os.path.join(base, carpeta), exist_ok=True)
    archivo.save(os.path.join(base, carpeta, nombre))
    return (f"{carpeta}/{nombre}" if privado else f"uploads/{carpeta}/{nombre}"), nombre_original


def _leer_items(items: Any) -> dict[str, int]:
    """Agrupa los ítems del carrito por SKU sumando cantidades."""
    if not isinstance(items, list) or not items:
        raise ErrorNegocio("Agrega al menos un producto a la venta.")
    if len(items) > MAX_ITEMS:
        raise ErrorNegocio(f"Una venta admite hasta {MAX_ITEMS} productos distintos.")
    agrupados: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ErrorNegocio("Formato de venta inválido.")
        sku = normalizar_sku(item.get("sku"))
        agrupados[sku] = agrupados.get(sku, 0) + entero_positivo(item.get("cantidad"), f"La cantidad de {sku}")
    return agrupados


def confirmar_venta(
    empresa_id: int,
    usuario_id: int,
    trabajador_id: int,
    items: Any,
    medio_pago: str,
    comprobante: FileStorage | None,
    tipo_documento: str = "BOLETA",
    descuento: Any = "0",
    cliente: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra una venta de forma atómica.

    Dentro de una sola transacción: bloquea el inventario de cada producto,
    verifica stock en tienda (RN03), descuenta lotes FEFO/FIFO, genera el folio
    correlativo del documento (boleta o factura), calcula IVA, descuento y fecha
    de término de garantía, y guarda detalle, movimientos, comprobante y auditoría.
    Cualquier fallo provoca ROLLBACK completo.
    """
    agrupados = _leer_items(items)
    if medio_pago not in MEDIOS_PAGO:
        raise ErrorNegocio("Selecciona el medio de pago.")
    if tipo_documento not in TIPOS_DOCUMENTO:
        raise ErrorNegocio("Selecciona boleta o factura.")
    datos_cliente = _datos_cliente(tipo_documento, cliente or {})
    monto_descuento = monto(descuento or "0", "El descuento", permitir_cero=True)
    tiene_archivo = comprobante is not None and bool(comprobante.filename)
    if current_app.config["COMPROBANTE_OBLIGATORIO"] and not tiene_archivo:
        raise ErrorNegocio("Adjunta el comprobante de pago para validar la venta.")

    columna_folio = "ultimo_numero_boleta" if tipo_documento == "BOLETA" else "ultimo_numero_factura"
    ahora = datetime.now().replace(microsecond=0)
    ruta_archivo: str | None = None
    try:
        with db.transaccion() as cur:
            # Correlativo: bloquear la fila de la empresa serializa la numeración.
            cur.execute(f"SELECT {columna_folio} AS ultimo FROM empresas WHERE id = %s FOR UPDATE", (empresa_id,))
            folio = cur.fetchone()["ultimo"] + 1

            # Bloqueo en orden de SKU para evitar interbloqueos entre ventas simultáneas.
            lineas: list[dict[str, Any]] = []
            for sku in sorted(agrupados):
                cantidad = agrupados[sku]
                cur.execute(
                    """SELECT p.id AS producto_id, p.nombre, p.precio_venta, p.activo, p.meses_garantia,
                              m.nombre AS marca, i.id AS inventario_id, i.stock_tienda
                       FROM productos p JOIN inventario i ON i.producto_id = p.id JOIN marcas m ON m.id = p.marca_id
                       WHERE p.empresa_id = %s AND p.codigo_sku = %s FOR UPDATE""",
                    (empresa_id, sku),
                )
                fila = cur.fetchone()
                if fila is None or not fila["activo"]:
                    raise ErrorNegocio(f"El código {sku} no corresponde a un producto activo.")
                if fila["stock_tienda"] < cantidad:
                    raise ErrorNegocio(
                        f"Stock insuficiente de {fila['nombre']} ({sku}): pediste {cantidad} y hay {fila['stock_tienda']} en tienda."
                    )
                lineas.append({**fila, "sku": sku, "cantidad": cantidad})

            subtotal = Decimal("0")
            costo_total = Decimal("0")
            for linea in lineas:
                consumos = consumir_fifo(cur, empresa_id, linea["producto_id"], linea["cantidad"])
                costo_linea = sum((Decimal(c) * q for _l, q, c in consumos), Decimal("0"))
                linea["consumos"] = consumos
                linea["costo_unitario"] = (costo_linea / linea["cantidad"]).quantize(Decimal("0.0001"))
                linea["subtotal"] = linea["precio_venta"] * linea["cantidad"]
                meses = linea["meses_garantia"] or 0
                linea["fin_garantia"] = sumar_meses(ahora.date(), meses) if meses else None
                subtotal += linea["subtotal"]
                costo_total += costo_linea

            if monto_descuento > subtotal:
                raise ErrorNegocio("El descuento no puede superar el subtotal de la venta.")
            totales = calcular_totales(subtotal, monto_descuento)

            cur.execute(
                """INSERT INTO ventas (empresa_id, trabajador_id, tipo_documento, folio, subtotal, descuento, monto_total,
                                       monto_neto, monto_iva, costo_total, medio_pago, cliente_rut, cliente_razon_social,
                                       cliente_giro, cliente_direccion, fecha_hora)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (empresa_id, trabajador_id, tipo_documento, folio, subtotal, monto_descuento, totales["total"],
                 totales["neto"], totales["iva"], costo_total.quantize(Decimal("0.01")), medio_pago,
                 *datos_cliente, ahora),
            )
            venta_id = cur.lastrowid
            cur.execute(f"UPDATE empresas SET {columna_folio} = %s WHERE id = %s", (folio, empresa_id))

            for linea in lineas:
                cur.execute(
                    """INSERT INTO detalle_ventas (venta_id, producto_id, producto_nombre, marca_nombre, cantidad,
                                                   precio_unitario, costo_unitario, subtotal, meses_garantia_aplicados,
                                                   fecha_fin_garantia)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (venta_id, linea["producto_id"], linea["nombre"], linea["marca"], linea["cantidad"],
                     linea["precio_venta"], linea["costo_unitario"], linea["subtotal"], linea["meses_garantia"] or 0,
                     linea["fin_garantia"]),
                )
                detalle_id = cur.lastrowid
                cur.executemany(
                    "INSERT INTO consumo_lotes (lote_id, detalle_venta_id, cantidad, costo_unitario) VALUES (%s, %s, %s, %s)",
                    [(lote_id, detalle_id, q, c) for lote_id, q, c in linea["consumos"]],
                )
                cur.execute(
                    "UPDATE inventario SET stock_tienda = stock_tienda - %s WHERE id = %s AND empresa_id = %s",
                    (linea["cantidad"], linea["inventario_id"], empresa_id),
                )
                cur.execute(
                    """INSERT INTO movimientos_inventario
                           (empresa_id, producto_id, usuario_id, tipo_movimiento, cantidad, origen, destino, referencia_id)
                       VALUES (%s, %s, %s, 'VENTA', %s, 'TIENDA', 'CLIENTE', %s)""",
                    (empresa_id, linea["producto_id"], usuario_id, linea["cantidad"], venta_id),
                )

            if tiene_archivo:
                ruta_archivo, original = guardar_archivo(comprobante, "comprobantes", EXTENSIONES_COMPROBANTE)
                cur.execute(
                    "INSERT INTO comprobantes (venta_id, usuario_id, archivo_url, nombre_original) VALUES (%s, %s, %s, %s)",
                    (venta_id, usuario_id, ruta_archivo, original[:255]),
                )
            auditar(cur, "INSERT", "ventas", venta_id,
                    f"{TIPOS_DOCUMENTO[tipo_documento].lower()} {folio}: {len(lineas)} productos, total ${totales['total']}"
                    f" (desc. ${monto_descuento}), {MEDIOS_PAGO[medio_pago]}")
    except Exception:
        # Si la transacción falló después de guardar el archivo, se elimina para no dejar basura.
        if ruta_archivo:
            try:
                os.remove(os.path.join(current_app.config["PRIVATE_UPLOAD_FOLDER"], ruta_archivo))
            except OSError:
                pass
        raise

    return {"venta_id": venta_id, "folio": folio, "tipo_documento": tipo_documento, "total": totales["total"]}


def adjuntar_comprobante(empresa_id: int, usuario: dict[str, Any], venta_id: int, archivo: FileStorage) -> None:
    """Agrega un comprobante a una venta existente (no modifica la venta)."""
    venta = obtener_venta(empresa_id, venta_id)
    if venta is None:
        raise ErrorNegocio("Venta no encontrada.")
    if usuario["rol"] != "ADMIN" and venta["trabajador_id"] != usuario["trabajador_id"]:
        raise ErrorNegocio("Solo puedes adjuntar comprobantes a tus propias ventas.")
    if not archivo or not archivo.filename:
        raise ErrorNegocio("Selecciona un archivo.")
    ruta, original = guardar_archivo(archivo, "comprobantes", EXTENSIONES_COMPROBANTE)
    with db.transaccion() as cur:
        cur.execute(
            "INSERT INTO comprobantes (venta_id, usuario_id, archivo_url, nombre_original) VALUES (%s, %s, %s, %s)",
            (venta_id, usuario["id"], ruta, original[:255]),
        )
        auditar(cur, "INSERT", "comprobantes", cur.lastrowid, f"{venta['tipo_documento'].lower()} {venta['folio']}")


def obtener_venta(empresa_id: int, venta_id: int) -> dict[str, Any] | None:
    """Venta con vendedor, detalle, comprobantes y nota de crédito (solo de la empresa)."""
    venta = db.query_one(
        """SELECT v.*, CONCAT(t.nombre, ' ', t.apellido) AS vendedor, t.rut AS rut_vendedor
           FROM ventas v JOIN trabajadores t ON t.id = v.trabajador_id
           WHERE v.id = %s AND v.empresa_id = %s""",
        (venta_id, empresa_id),
    )
    if venta is None:
        return None
    venta["detalle"] = db.query(
        """SELECT d.*, p.codigo_sku, d.producto_nombre AS nombre, d.marca_nombre AS marca
           FROM detalle_ventas d JOIN productos p ON p.id = d.producto_id
           WHERE d.venta_id = %s ORDER BY d.id""",
        (venta_id,),
    )
    venta["comprobantes"] = db.query("SELECT * FROM comprobantes WHERE venta_id = %s ORDER BY fecha_carga", (venta_id,))
    venta["nota_credito"] = db.query_one(
        """SELECT nc.*, CONCAT(t.nombre, ' ', t.apellido) AS autorizado_por FROM notas_credito nc
           JOIN trabajadores t ON t.usuario_id = nc.usuario_id
           WHERE nc.venta_id = %s AND nc.empresa_id = %s""",
        (venta_id, empresa_id),
    )
    return venta


def anular_con_nota_credito(empresa_id: int, admin_usuario_id: int, venta_id: int, motivo: str) -> int:
    """Emite una nota de crédito que anula la venta, devuelve el stock a tienda y restituye los lotes.

    Devuelve el número de la nota de crédito.
    """
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorNegocio("Describe el motivo de la nota de crédito (mínimo 5 caracteres).")
    with db.transaccion() as cur:
        cur.execute(
            "SELECT * FROM ventas WHERE id = %s AND empresa_id = %s FOR UPDATE", (venta_id, empresa_id)
        )
        venta = cur.fetchone()
        if venta is None:
            raise ErrorNegocio("Venta no encontrada.")
        if venta["estado"] != "CONFIRMADA":
            raise ErrorNegocio("La venta ya fue anulada.")

        cur.execute("SELECT ultimo_numero_nc FROM empresas WHERE id = %s FOR UPDATE", (empresa_id,))
        numero = cur.fetchone()["ultimo_numero_nc"] + 1

        cur.execute("SELECT id, producto_id, cantidad FROM detalle_ventas WHERE venta_id = %s ORDER BY producto_id", (venta_id,))
        for detalle in cur.fetchall():
            cur.execute(
                "SELECT id FROM inventario WHERE producto_id = %s AND empresa_id = %s FOR UPDATE",
                (detalle["producto_id"], empresa_id),
            )
            cur.execute(
                "UPDATE inventario SET stock_tienda = stock_tienda + %s WHERE producto_id = %s AND empresa_id = %s",
                (detalle["cantidad"], detalle["producto_id"], empresa_id),
            )
            cur.execute("SELECT lote_id, cantidad FROM consumo_lotes WHERE detalle_venta_id = %s", (detalle["id"],))
            for consumo in cur.fetchall():
                cur.execute(
                    "UPDATE lotes SET cantidad_disponible = cantidad_disponible + %s WHERE id = %s AND empresa_id = %s",
                    (consumo["cantidad"], consumo["lote_id"], empresa_id),
                )
            cur.execute(
                """INSERT INTO movimientos_inventario
                       (empresa_id, producto_id, usuario_id, tipo_movimiento, cantidad, origen, destino, referencia_id)
                   VALUES (%s, %s, %s, 'ANULACION_VENTA', %s, 'CLIENTE', 'TIENDA', %s)""",
                (empresa_id, detalle["producto_id"], admin_usuario_id, detalle["cantidad"], venta_id),
            )

        cur.execute(
            """INSERT INTO notas_credito (empresa_id, venta_id, usuario_id, numero, motivo, monto, costo_revertido)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, venta_id, admin_usuario_id, numero, motivo[:255], venta["monto_total"], venta["costo_total"]),
        )
        nc_id = cur.lastrowid
        cur.execute("UPDATE empresas SET ultimo_numero_nc = %s WHERE id = %s", (numero, empresa_id))
        cur.execute("UPDATE ventas SET estado = 'ANULADA' WHERE id = %s AND empresa_id = %s", (venta_id, empresa_id))
        auditar(cur, "INSERT", "notas_credito", nc_id,
                f"NC {numero} anula {venta['tipo_documento'].lower()} {venta['folio']} por ${venta['monto_total']}: {motivo[:150]}")
    return numero


def listar_ventas(empresa_id: int, desde: str, hasta: str, trabajador_id: int | None = None,
                  estado: str | None = None, limite: int = 300) -> list[dict[str, Any]]:
    """Ventas del período, opcionalmente filtradas por vendedor y estado."""
    filtros = ["v.empresa_id = %s", "v.fecha_hora >= %s", "v.fecha_hora < %s + INTERVAL 1 DAY"]
    params: list[Any] = [empresa_id, desde, hasta]
    if trabajador_id:
        filtros.append("v.trabajador_id = %s")
        params.append(trabajador_id)
    if estado in ("CONFIRMADA", "ANULADA"):
        filtros.append("v.estado = %s")
        params.append(estado)
    params.append(limite)
    return db.query(
        f"""SELECT v.*, CONCAT(t.nombre, ' ', t.apellido) AS vendedor,
                   (SELECT SUM(d.cantidad) FROM detalle_ventas d WHERE d.venta_id = v.id) AS unidades,
                   (SELECT COUNT(*) FROM comprobantes c WHERE c.venta_id = v.id) AS comprobantes
            FROM ventas v JOIN trabajadores t ON t.id = v.trabajador_id
            WHERE {' AND '.join(filtros)}
            ORDER BY v.fecha_hora DESC, v.id DESC LIMIT %s""",
        params,
    )
