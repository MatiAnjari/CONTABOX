"""Cálculo en tiempo real de los indicadores del dashboard del administrador.

%PPI = (Unidades físicas auditadas / Stock registrado en sistema) x 100      Meta >= 95,0 %
%PMR = (Unidades registradas en pérdidas / Unidades ingresadas por lotes) x 100   Meta <= 2,0 %
%MBO = ((Ventas totales - Costo de lotes vendidos) / Ventas totales) x 100   Meta >= 25,0 %
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .. import db

METAS = {"ppi": 95.0, "pmr": 2.0, "mbo": 25.0}


def _pct(numerador: Any, denominador: Any) -> float | None:
    den = float(denominador or 0)
    return None if den == 0 else round(float(numerador or 0) / den * 100, 1)


def calcular(empresa_id: int, desde: str, hasta: str) -> dict[str, Any]:
    """Devuelve todos los indicadores del período [desde, hasta] (fechas 'AAAA-MM-DD')."""
    rango = (empresa_id, desde, hasta)
    periodo = "fecha_hora >= %s AND fecha_hora < %s + INTERVAL 1 DAY"

    ventas = db.query_one(
        f"""SELECT COUNT(*) AS cantidad, COALESCE(SUM(monto_total), 0) AS monto,
                   COALESCE(SUM(costo_total), 0) AS costo
            FROM ventas WHERE empresa_id = %s AND estado = 'CONFIRMADA' AND {periodo}""",
        rango,
    )
    unidades_vendidas = db.query_one(
        f"""SELECT COALESCE(SUM(d.cantidad), 0) AS u FROM detalle_ventas d JOIN ventas v ON v.id = d.venta_id
            WHERE v.empresa_id = %s AND v.estado = 'CONFIRMADA' AND v.{periodo}""",
        rango,
    )["u"]
    anuladas = db.query_one(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(monto), 0) AS monto FROM notas_credito WHERE empresa_id = %s AND {periodo}",
        rango,
    )
    perdidas = db.query_one(
        f"""SELECT COALESCE(SUM(cantidad), 0) AS unidades, COALESCE(SUM(costo_total), 0) AS costo
            FROM perdidas WHERE empresa_id = %s AND {periodo}""",
        rango,
    )
    ingresos = db.query_one(
        """SELECT COALESCE(SUM(cantidad_ingresada), 0) AS unidades,
                  COALESCE(SUM(inversion_total), 0) AS inversion
           FROM lotes WHERE empresa_id = %s AND fecha_ingreso >= %s AND fecha_ingreso < %s + INTERVAL 1 DAY""",
        rango,
    )
    # %PPI: último conteo físico de cada producto dentro del período.
    ppi = db.query_one(
        """SELECT COALESCE(SUM(c.stock_fisico), 0) AS fisico, COALESCE(SUM(c.stock_sistema), 0) AS sistema,
                  COUNT(*) AS productos
           FROM conteos_inventario c
           JOIN (SELECT producto_id, MAX(id) AS id FROM conteos_inventario
                 WHERE empresa_id = %s AND fecha_hora >= %s AND fecha_hora < %s + INTERVAL 1 DAY
                 GROUP BY producto_id) ult ON ult.id = c.id""",
        rango,
    )
    stock = db.query_one(
        """SELECT COALESCE(SUM(stock_bodega), 0) AS bodega, COALESCE(SUM(stock_tienda), 0) AS tienda,
                  COALESCE(SUM(CASE WHEN stock_bodega + stock_tienda <= stock_critico THEN 1 ELSE 0 END), 0) AS criticos
           FROM inventario WHERE empresa_id = %s""",
        (empresa_id,),
    )
    valor_inventario = db.query_one(
        "SELECT COALESCE(SUM(cantidad_disponible * costo_unitario), 0) AS v FROM lotes WHERE empresa_id = %s",
        (empresa_id,),
    )["v"]

    ventas_por_producto = db.query(
        f"""SELECT p.codigo_sku, p.nombre,
                   COALESCE(SUM(CASE WHEN v.id IS NOT NULL THEN d.cantidad END), 0) AS unidades,
                   COALESCE(SUM(CASE WHEN v.id IS NOT NULL THEN d.subtotal END), 0) AS monto
            FROM productos p
            LEFT JOIN detalle_ventas d ON d.producto_id = p.id
            LEFT JOIN ventas v ON v.id = d.venta_id AND v.estado = 'CONFIRMADA' AND v.{periodo}
            WHERE p.empresa_id = %s AND p.activo = 1
            GROUP BY p.id, p.codigo_sku, p.nombre""",
        (desde, hasta, empresa_id),
    )
    mas_vendidos = sorted(ventas_por_producto, key=lambda f: (-f["unidades"], f["nombre"]))[:5]
    menos_vendidos = sorted(ventas_por_producto, key=lambda f: (f["unidades"], f["nombre"]))[:5]

    por_vendedor = db.query(
        f"""SELECT CONCAT(t.nombre, ' ', t.apellido) AS vendedor, COUNT(v.id) AS ventas,
                   COALESCE(SUM(v.monto_total), 0) AS monto
            FROM ventas v JOIN trabajadores t ON t.id = v.trabajador_id
            WHERE v.empresa_id = %s AND v.estado = 'CONFIRMADA' AND v.{periodo}
            GROUP BY t.id, t.nombre, t.apellido ORDER BY monto DESC""",
        rango,
    )
    por_dia = db.query(
        f"""SELECT DATE(fecha_hora) AS dia, COUNT(*) AS ventas, SUM(monto_total) AS monto, SUM(costo_total) AS costo
            FROM ventas WHERE empresa_id = %s AND estado = 'CONFIRMADA' AND {periodo}
            GROUP BY DATE(fecha_hora) ORDER BY dia""",
        rango,
    )
    por_categoria = db.query(
        f"""SELECT c.nombre AS categoria, SUM(d.cantidad) AS unidades, SUM(d.subtotal) AS monto
            FROM detalle_ventas d JOIN ventas v ON v.id = d.venta_id
            JOIN productos p ON p.id = d.producto_id JOIN categorias c ON c.id = p.categoria_id
            WHERE v.empresa_id = %s AND v.estado = 'CONFIRMADA' AND v.{periodo}
            GROUP BY c.id, c.nombre ORDER BY monto DESC""",
        rango,
    )
    perdidas_por_motivo = db.query(
        f"""SELECT motivo, SUM(cantidad) AS unidades, SUM(costo_total) AS costo
            FROM perdidas WHERE empresa_id = %s AND {periodo} GROUP BY motivo ORDER BY costo DESC""",
        rango,
    )

    monto = Decimal(ventas["monto"])
    costo = Decimal(ventas["costo"])
    ganancia_bruta = monto - costo
    return {
        "ventas": ventas,
        "unidades_vendidas": unidades_vendidas,
        "anuladas": anuladas,
        "perdidas": perdidas,
        "ingresos": ingresos,
        "stock": stock,
        "valor_inventario": valor_inventario,
        "ganancia_bruta": ganancia_bruta,
        "ganancia_neta": ganancia_bruta - Decimal(perdidas["costo"]),
        "ticket_promedio": (monto / ventas["cantidad"]) if ventas["cantidad"] else 0,
        "ppi": _pct(ppi["fisico"], ppi["sistema"]),
        "ppi_productos": ppi["productos"],
        "pmr": _pct(perdidas["unidades"], ingresos["unidades"]),
        "mbo": _pct(ganancia_bruta, monto),
        "metas": METAS,
        "mas_vendidos": mas_vendidos,
        "menos_vendidos": menos_vendidos,
        "por_vendedor": por_vendedor,
        "por_dia": por_dia,
        "perdidas_por_motivo": perdidas_por_motivo,
        "por_categoria": por_categoria,
    }
