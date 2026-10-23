"""Lógica de negocio de bodega: productos, lotes, transferencias, mermas y conteos.

Todas las operaciones que mueven stock se ejecutan dentro de una transacción ACID,
bloquean la fila de inventario con SELECT ... FOR UPDATE y validan que el stock
nunca quede negativo (RN03).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .. import db
from ..auditoria import auditar
from ..errors import ErrorNegocio
from . import catalogo

MOTIVOS_MERMA = {
    "VENCIMIENTO": "Vencimiento",
    "DETERIORO_FISICO": "Deterioro físico",
    "ROBO_HURTO": "Robo / hurto",
    "OTRO": "Otro",
}
UBICACIONES = {"BODEGA": "stock_bodega", "TIENDA": "stock_tienda"}
DOS_DECIMALES = Decimal("0.01")
CUATRO_DECIMALES = Decimal("0.0001")
DIAS_ALERTA_VENCIMIENTO = 15


def cajas(unidades: int, por_bulto: int) -> str:
    """Equivalencia en bultos completos: 603 u. de a 12 -> '50 cajas + 3 u.'"""
    if por_bulto <= 1:
        return f"{unidades} u."
    completas, sueltas = divmod(unidades, por_bulto)
    if completas == 0:
        return f"{sueltas} u. sueltas" if sueltas else "0 cajas"
    texto = f"{completas} caja{'s' if completas != 1 else ''}"
    return texto + (f" + {sueltas} u." if sueltas else "")


# ---------------------------------------------------------------------------
# Conversión y validación de entradas
# ---------------------------------------------------------------------------
def entero_positivo(valor: Any, campo: str) -> int:
    """Convierte a entero > 0 o lanza ErrorNegocio."""
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        raise ErrorNegocio(f"{campo} debe ser un número entero.") from None
    if numero <= 0:
        raise ErrorNegocio(f"{campo} debe ser mayor que cero.")
    if numero > 1_000_000:
        raise ErrorNegocio(f"{campo} es demasiado grande.")
    return numero


def monto(valor: Any, campo: str, permitir_cero: bool = False) -> Decimal:
    """Convierte un monto en pesos (acepta '1.990' o '1990,5') a Decimal."""
    texto = str(valor or "").strip().replace("$", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") >= 1 and len(texto.split(".")[-1]) == 3:
        texto = texto.replace(".", "")  # separador de miles chileno
    try:
        numero = Decimal(texto).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ErrorNegocio(f"{campo} no es un monto válido.") from None
    if numero < 0 or (numero == 0 and not permitir_cero):
        raise ErrorNegocio(f"{campo} debe ser mayor que cero.")
    if numero >= Decimal("10000000000"):
        raise ErrorNegocio(f"{campo} es demasiado grande.")
    return numero


def normalizar_sku(valor: Any) -> str:
    sku = (str(valor or "")).strip().upper()
    if not sku:
        raise ErrorNegocio("Ingresa o escanea el código SKU.")
    if len(sku) > 50:
        raise ErrorNegocio("El código SKU admite hasta 50 caracteres.")
    return sku


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def buscar_producto(empresa_id: int, sku: str) -> dict[str, Any] | None:
    """Producto con su inventario, categoría y marca por SKU, solo dentro de la empresa (RN01)."""
    return db.query_one(
        """SELECT p.id, p.codigo_sku, p.nombre, p.precio_venta, p.activo, p.es_perecible, p.meses_garantia,
                  p.unidades_por_bulto, p.categoria_id, c.nombre AS categoria, c.es_alimento, c.admite_garantia,
                  m.nombre AS marca, i.stock_bodega, i.stock_tienda, i.stock_critico
           FROM productos p JOIN inventario i ON i.producto_id = p.id
           JOIN categorias c ON c.id = p.categoria_id JOIN marcas m ON m.id = p.marca_id
           WHERE p.empresa_id = %s AND p.codigo_sku = %s""",
        (empresa_id, sku.strip().upper()),
    )


def listar_inventario(empresa_id: int, buscar: str = "", solo_criticos: bool = False,
                      categoria_id: int | None = None, marca_id: int | None = None) -> list[dict[str, Any]]:
    """Existencias por producto (unidades y cajas) con unidades vendidas y perdidas acumuladas."""
    filtros = ["p.empresa_id = %s"]
    params: list[Any] = [empresa_id]
    if buscar:
        filtros.append("(p.codigo_sku LIKE %s OR p.nombre LIKE %s OR m.nombre LIKE %s)")
        params += [f"%{buscar}%", f"%{buscar}%", f"%{buscar}%"]
    if solo_criticos:
        filtros.append("(i.stock_bodega + i.stock_tienda) <= i.stock_critico")
    if categoria_id:
        filtros.append("p.categoria_id = %s")
        params.append(categoria_id)
    if marca_id:
        filtros.append("p.marca_id = %s")
        params.append(marca_id)
    filas = db.query(
        f"""SELECT p.id, p.codigo_sku, p.nombre, p.precio_venta, p.es_perecible, p.meses_garantia, p.unidades_por_bulto,
                   c.nombre AS categoria, m.nombre AS marca,
                   i.stock_bodega, i.stock_tienda, i.stock_critico,
                   (i.stock_bodega + i.stock_tienda) AS stock_total,
                   (SELECT MIN(l.fecha_vencimiento) FROM lotes l
                     WHERE l.producto_id = p.id AND l.cantidad_disponible > 0) AS proximo_vencimiento,
                   COALESCE((SELECT SUM(d.cantidad) FROM detalle_ventas d JOIN ventas v ON v.id = d.venta_id
                             WHERE d.producto_id = p.id AND v.estado = 'CONFIRMADA'), 0) AS vendidas,
                   COALESCE((SELECT SUM(pe.cantidad) FROM perdidas pe WHERE pe.producto_id = p.id), 0) AS perdidas,
                   COALESCE((SELECT SUM(l.cantidad_disponible * l.costo_unitario) FROM lotes l
                             WHERE l.producto_id = p.id), 0) AS valor_inventario
            FROM productos p JOIN inventario i ON i.producto_id = p.id
            JOIN categorias c ON c.id = p.categoria_id JOIN marcas m ON m.id = p.marca_id
            WHERE {' AND '.join(filtros)}
            ORDER BY p.nombre, m.nombre""",
        params,
    )
    for fila in filas:
        fila["cajas_total"] = cajas(fila["stock_total"], fila["unidades_por_bulto"])
        fila["cajas_bodega"] = cajas(fila["stock_bodega"], fila["unidades_por_bulto"])
    return filas


def lotes_por_vencer(empresa_id: int, dias: int = DIAS_ALERTA_VENCIMIENTO) -> list[dict[str, Any]]:
    """Lotes con unidades disponibles que vencen dentro de `dias` días (o ya vencidos)."""
    return db.query(
        """SELECT l.id, l.numero_lote, l.fecha_vencimiento, l.cantidad_disponible, p.id AS producto_id,
                  p.codigo_sku, p.nombre, m.nombre AS marca, DATEDIFF(l.fecha_vencimiento, CURDATE()) AS dias
           FROM lotes l JOIN productos p ON p.id = l.producto_id JOIN marcas m ON m.id = p.marca_id
           WHERE l.empresa_id = %s AND l.cantidad_disponible > 0 AND l.fecha_vencimiento IS NOT NULL
             AND l.fecha_vencimiento <= CURDATE() + INTERVAL %s DAY
           ORDER BY l.fecha_vencimiento, p.nombre""",
        (empresa_id, dias),
    )


def _bloquear_inventario(cur: Any, empresa_id: int, sku: str) -> dict[str, Any]:
    """Obtiene producto + inventario bloqueando la fila (SELECT ... FOR UPDATE)."""
    cur.execute(
        """SELECT p.id AS producto_id, p.codigo_sku, p.nombre, p.precio_venta, p.activo,
                  i.id AS inventario_id, i.stock_bodega, i.stock_tienda, i.stock_critico
           FROM productos p JOIN inventario i ON i.producto_id = p.id
           WHERE p.empresa_id = %s AND p.codigo_sku = %s
           FOR UPDATE""",
        (empresa_id, sku),
    )
    fila = cur.fetchone()
    if fila is None:
        raise ErrorNegocio(f"No existe un producto con el código {sku}.")
    return fila


def consumir_fifo(cur: Any, empresa_id: int, producto_id: int, cantidad: int) -> list[tuple[int, int, Decimal]]:
    """Descuenta unidades de los lotes: primero el que vence antes (FEFO), luego el más antiguo (FIFO).

    Devuelve la lista de (lote_id, cantidad, costo_unitario) consumidos.
    """
    cur.execute(
        """SELECT id, cantidad_disponible, costo_unitario FROM lotes
           WHERE empresa_id = %s AND producto_id = %s AND cantidad_disponible > 0
           ORDER BY fecha_vencimiento IS NULL, fecha_vencimiento, fecha_ingreso, id FOR UPDATE""",
        (empresa_id, producto_id),
    )
    pendiente = cantidad
    consumos: list[tuple[int, int, Decimal]] = []
    for lote in cur.fetchall():
        if pendiente == 0:
            break
        toma = min(pendiente, lote["cantidad_disponible"])
        consumos.append((lote["id"], toma, lote["costo_unitario"]))
        pendiente -= toma
    if pendiente > 0:
        raise ErrorNegocio("Inconsistencia de lotes: no hay unidades suficientes registradas por lote.")
    for lote_id, toma, _costo in consumos:
        cur.execute(
            "UPDATE lotes SET cantidad_disponible = cantidad_disponible - %s WHERE id = %s AND empresa_id = %s",
            (toma, lote_id, empresa_id),
        )
    return consumos


def _movimiento(cur: Any, empresa_id: int, producto_id: int, usuario_id: int, tipo: str, cantidad: int,
                origen: str, destino: str, referencia_id: int | None) -> None:
    cur.execute(
        """INSERT INTO movimientos_inventario
               (empresa_id, producto_id, usuario_id, tipo_movimiento, cantidad, origen, destino, referencia_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (empresa_id, producto_id, usuario_id, tipo, cantidad, origen, destino, referencia_id),
    )


# ---------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------
def entero_opcional(valor: Any, campo: str, defecto: int) -> int:
    texto = str(valor or "").strip()
    return entero_positivo(texto, campo) if texto else defecto


def ingresar_lote(empresa_id: int, usuario_id: int, form: dict[str, Any]) -> dict[str, Any]:
    """Recepción de un lote en bultos (cajas). Crea el producto si el SKU es nuevo.

    Stock total = cantidad de bultos x unidades por bulto.
    El costo se puede ingresar por unidad, por bulto o como inversión total del lote;
    se guarda la inversión exacta y un costo unitario con 4 decimales.
    """
    sku = normalizar_sku(form.get("codigo_sku"))
    bultos = entero_positivo(form.get("cantidad_bultos"), "La cantidad de cajas/bultos")
    precio_venta = monto(form.get("precio_venta"), "El precio de venta por unidad")
    existente = buscar_producto(empresa_id, sku)
    por_bulto = entero_opcional(form.get("unidades_por_bulto"), "Las unidades por caja",
                                existente["unidades_por_bulto"] if existente else 1)
    cantidad = bultos * por_bulto
    if cantidad > 1_000_000:
        raise ErrorNegocio("El lote supera el máximo de 1.000.000 de unidades.")

    modo = form.get("modo_costo", "UNITARIO")
    valor_costo = monto(form.get("costo"), "El costo de adquisición", permitir_cero=True)
    if modo == "TOTAL":
        inversion = valor_costo
    elif modo == "BULTO":
        inversion = valor_costo * bultos
    elif modo == "UNITARIO":
        inversion = valor_costo * cantidad
    else:
        raise ErrorNegocio("Indica si el costo es por unidad, por caja o total.")
    inversion = inversion.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    costo_unitario = (inversion / cantidad).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)

    numero_lote = " ".join(str(form.get("numero_lote") or "").split())[:50]
    if not numero_lote:
        raise ErrorNegocio("Ingresa el número o código del lote.")

    vencimiento: date | None = None
    if form.get("fecha_vencimiento"):
        try:
            vencimiento = datetime.strptime(form["fecha_vencimiento"], "%Y-%m-%d").date()
        except ValueError:
            raise ErrorNegocio("La fecha de vencimiento no es válida.") from None
        if vencimiento < date.today():
            raise ErrorNegocio("La fecha de vencimiento ya pasó: no se puede ingresar un lote vencido.")
    stock_critico_txt = str(form.get("stock_critico") or "").strip()
    if stock_critico_txt and not stock_critico_txt.isdigit():
        raise ErrorNegocio("El stock crítico debe ser un número entero.")

    # Atributos de catálogo: obligatorios solo para un SKU nuevo.
    atributos = None
    if existente is None:
        nombre = " ".join(str(form.get("nombre") or "").split())[:150]
        if not nombre:
            raise ErrorNegocio("El SKU es nuevo: ingresa el nombre del producto.")
        atributos = catalogo.atributos_producto(empresa_id, form)
        es_perecible = atributos["es_perecible"]
    else:
        nombre = existente["nombre"]
        es_perecible = existente["es_perecible"]
    if es_perecible and vencimiento is None:
        raise ErrorNegocio("Producto perecible: la fecha de vencimiento del lote es obligatoria.")

    with db.transaccion() as cur:
        if existente is None:
            marca_id = catalogo.obtener_o_crear_marca(cur, empresa_id, form.get("marca"))
            cur.execute(
                """INSERT INTO productos (empresa_id, categoria_id, marca_id, codigo_sku, nombre, precio_venta,
                                          es_perecible, meses_garantia, unidades_por_bulto)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (empresa_id, atributos["categoria"]["id"], marca_id, sku, nombre, precio_venta,
                 es_perecible, atributos["meses_garantia"], por_bulto),
            )
            producto_id = cur.lastrowid
            cur.execute(
                "INSERT INTO inventario (empresa_id, producto_id, stock_critico) VALUES (%s, %s, %s)",
                (empresa_id, producto_id, int(stock_critico_txt) if stock_critico_txt else 5),
            )
            auditar(cur, "INSERT", "productos", producto_id, f"{sku} {nombre} ({atributos['categoria']['nombre']})")
        else:
            producto_id = existente["id"]
            cur.execute("SELECT id FROM productos WHERE id = %s AND empresa_id = %s FOR UPDATE", (producto_id, empresa_id))
            cur.execute(
                """UPDATE productos SET precio_venta = %s, unidades_por_bulto = %s, activo = 1
                   WHERE id = %s AND empresa_id = %s""",
                (precio_venta, por_bulto, producto_id, empresa_id),
            )
            if stock_critico_txt:
                cur.execute(
                    "UPDATE inventario SET stock_critico = %s WHERE producto_id = %s AND empresa_id = %s",
                    (int(stock_critico_txt), producto_id, empresa_id),
                )

        cur.execute("SELECT id FROM lotes WHERE producto_id = %s AND numero_lote = %s", (producto_id, numero_lote))
        if cur.fetchone():
            raise ErrorNegocio(f"El lote {numero_lote} ya fue registrado para este producto.")

        cur.execute(
            """INSERT INTO lotes (empresa_id, producto_id, usuario_id, numero_lote, cantidad_bultos, unidades_por_bulto,
                                  cantidad_ingresada, cantidad_disponible, inversion_total, costo_unitario, fecha_vencimiento)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, producto_id, usuario_id, numero_lote, bultos, por_bulto, cantidad, cantidad,
             inversion, costo_unitario, vencimiento),
        )
        lote_id = cur.lastrowid
        cur.execute(
            "UPDATE inventario SET stock_bodega = stock_bodega + %s WHERE producto_id = %s AND empresa_id = %s",
            (cantidad, producto_id, empresa_id),
        )
        _movimiento(cur, empresa_id, producto_id, usuario_id, "INGRESO_LOTE", cantidad, "PROVEEDOR", "BODEGA", lote_id)
        auditar(cur, "INSERT", "lotes", lote_id,
                f"{sku} lote {numero_lote}: {bultos} x {por_bulto} = {cantidad} u., inversión ${inversion}, "
                f"venta ${precio_venta}" + (f", vence {vencimiento:%d-%m-%Y}" if vencimiento else ""))
    return {"sku": sku, "nombre": nombre, "numero_lote": numero_lote, "cantidad": cantidad, "bultos": bultos,
            "por_bulto": por_bulto, "costo_unitario": costo_unitario, "inversion": inversion}


def transferir(empresa_id: int, usuario_id: int, sku: str, cantidad: Any, origen: str = "BODEGA") -> dict[str, Any]:
    """Transferencia interna Bodega Central -> Salón de Venta (o devolución inversa)."""
    sku = normalizar_sku(sku)
    cantidad = entero_positivo(cantidad, "La cantidad")
    if origen not in UBICACIONES:
        raise ErrorNegocio("Origen no válido.")
    destino = "TIENDA" if origen == "BODEGA" else "BODEGA"
    col_origen, col_destino = UBICACIONES[origen], UBICACIONES[destino]

    with db.transaccion() as cur:
        inv = _bloquear_inventario(cur, empresa_id, sku)
        if inv[col_origen] < cantidad:
            raise ErrorNegocio(
                f"Stock insuficiente en {origen.lower()}: hay {inv[col_origen]} u. de {inv['nombre']}."
            )
        cur.execute(
            f"UPDATE inventario SET {col_origen} = {col_origen} - %s, {col_destino} = {col_destino} + %s "
            "WHERE id = %s AND empresa_id = %s",
            (cantidad, cantidad, inv["inventario_id"], empresa_id),
        )
        _movimiento(cur, empresa_id, inv["producto_id"], usuario_id, "TRANSFERENCIA", cantidad, origen, destino, None)
        auditar(cur, "UPDATE", "inventario", inv["inventario_id"], f"{sku}: {cantidad} u. {origen} -> {destino}")
    return {"nombre": inv["nombre"], "cantidad": cantidad, "origen": origen, "destino": destino}


def registrar_merma(empresa_id: int, usuario_id: int, form: dict[str, Any]) -> dict[str, Any]:
    """Baja de unidades por merma con motivo obligatorio (RN04). Valoriza la pérdida con costo FIFO."""
    sku = normalizar_sku(form.get("codigo_sku"))
    cantidad = entero_positivo(form.get("cantidad"), "La cantidad")
    motivo = form.get("motivo", "")
    if motivo not in MOTIVOS_MERMA:
        raise ErrorNegocio("Selecciona el motivo de la merma (obligatorio).")
    observacion = (form.get("observacion") or "").strip()[:255]
    if motivo == "OTRO" and not observacion:
        raise ErrorNegocio("Cuando el motivo es 'Otro' debes describir la causa.")
    origen = form.get("origen", "BODEGA")
    if origen not in UBICACIONES:
        raise ErrorNegocio("Indica si la merma está en bodega o en tienda.")
    columna = UBICACIONES[origen]

    with db.transaccion() as cur:
        inv = _bloquear_inventario(cur, empresa_id, sku)
        if inv[columna] < cantidad:
            raise ErrorNegocio(f"Stock insuficiente en {origen.lower()}: hay {inv[columna]} u. de {inv['nombre']}.")
        consumos = consumir_fifo(cur, empresa_id, inv["producto_id"], cantidad)
        costo_total = sum((Decimal(c) * q for _l, q, c in consumos), Decimal("0")).quantize(DOS_DECIMALES)
        cur.execute(
            f"UPDATE inventario SET {columna} = {columna} - %s WHERE id = %s AND empresa_id = %s",
            (cantidad, inv["inventario_id"], empresa_id),
        )
        cur.execute(
            """INSERT INTO perdidas (empresa_id, producto_id, usuario_id, cantidad, motivo, observacion, origen, costo_total)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, inv["producto_id"], usuario_id, cantidad, motivo, observacion or None, origen, costo_total),
        )
        perdida_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO consumo_lotes (lote_id, perdida_id, cantidad, costo_unitario) VALUES (%s, %s, %s, %s)",
            [(lote_id, perdida_id, q, c) for lote_id, q, c in consumos],
        )
        _movimiento(cur, empresa_id, inv["producto_id"], usuario_id, "MERMA", cantidad, origen, "BAJA", perdida_id)
        auditar(cur, "INSERT", "perdidas", perdida_id,
                f"{sku}: {cantidad} u. por {MOTIVOS_MERMA[motivo]} (${costo_total})")
    return {"nombre": inv["nombre"], "cantidad": cantidad, "costo_total": costo_total, "motivo": MOTIVOS_MERMA[motivo]}


def registrar_conteo(empresa_id: int, usuario_id: int, sku: str, stock_fisico: Any, observacion: str = "") -> dict[str, Any]:
    """Registra un conteo físico (auditoría de inventario) para el cálculo del %PPI."""
    sku = normalizar_sku(sku)
    try:
        fisico = int(str(stock_fisico).strip())
    except ValueError:
        raise ErrorNegocio("Las unidades contadas deben ser un número entero.") from None
    if fisico < 0:
        raise ErrorNegocio("Las unidades contadas no pueden ser negativas.")
    with db.transaccion() as cur:
        inv = _bloquear_inventario(cur, empresa_id, sku)
        sistema = inv["stock_bodega"] + inv["stock_tienda"]
        cur.execute(
            """INSERT INTO conteos_inventario (empresa_id, producto_id, usuario_id, stock_sistema, stock_fisico, observacion)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (empresa_id, inv["producto_id"], usuario_id, sistema, fisico, (observacion or "").strip()[:255] or None),
        )
        auditar(cur, "INSERT", "conteos_inventario", cur.lastrowid, f"{sku}: sistema {sistema}, físico {fisico}")
    return {"nombre": inv["nombre"], "sistema": sistema, "fisico": fisico, "diferencia": fisico - sistema}


def actualizar_producto(empresa_id: int, producto_id: int, form: dict[str, Any]) -> None:
    """Edita nombre, categoría, marca, perecible, garantía, empaque, precio y stock crítico."""
    nombre = " ".join(str(form.get("nombre") or "").split())
    if not nombre:
        raise ErrorNegocio("El nombre del producto es obligatorio.")
    precio = monto(form.get("precio_venta"), "El precio de venta")
    critico_txt = str(form.get("stock_critico") or "0").strip()
    if not critico_txt.isdigit():
        raise ErrorNegocio("El stock crítico debe ser un número entero.")
    por_bulto = entero_positivo(form.get("unidades_por_bulto") or 1, "Las unidades por caja")
    atributos = catalogo.atributos_producto(empresa_id, form)
    if not db.query_one("SELECT id FROM productos WHERE id = %s AND empresa_id = %s", (producto_id, empresa_id)):
        raise ErrorNegocio("Producto no encontrado.")
    with db.transaccion() as cur:
        marca_id = catalogo.obtener_o_crear_marca(cur, empresa_id, form.get("marca"))
        cur.execute(
            """UPDATE productos SET nombre = %s, precio_venta = %s, categoria_id = %s, marca_id = %s,
                      es_perecible = %s, meses_garantia = %s, unidades_por_bulto = %s
               WHERE id = %s AND empresa_id = %s""",
            (nombre[:150], precio, atributos["categoria"]["id"], marca_id, atributos["es_perecible"],
             atributos["meses_garantia"], por_bulto, producto_id, empresa_id),
        )
        cur.execute(
            "UPDATE inventario SET stock_critico = %s WHERE producto_id = %s AND empresa_id = %s",
            (int(critico_txt), producto_id, empresa_id),
        )
        auditar(cur, "UPDATE", "productos", producto_id,
                f"{nombre}: precio ${precio}, {atributos['categoria']['nombre']}, garantía {atributos['meses_garantia']} m, "
                f"crítico {critico_txt}")
