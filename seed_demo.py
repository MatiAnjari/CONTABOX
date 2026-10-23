"""Carga una empresa de demostración con trabajadores, productos, ventas y mermas.

Uso:  python seed_demo.py      (después de python init_db.py)

Credenciales creadas (contraseña de todos: demo1234):
    Administrador : admin@demo.cl
    Bodeguero     : 11.111.111-1
    Vendedor      : 12.345.678-5
"""
from __future__ import annotations

import io
import json
import random
from datetime import date, timedelta

from app import create_app, db

PASSWORD = "demo1234"
# (sku, nombre, categoría, marca, perecible, garantía, cajas, unidades por caja, costo unitario, precio)
PRODUCTOS = [
    ("7801001", "Bebida cola 1,5 L", "Bebidas", "Coca-Cola", "0", "", 5, 6, 900, 1590),
    ("7801011", "Bebida cola 1,5 L", "Bebidas", "Kem", "0", "", 4, 6, 650, 1190),
    ("7801002", "Agua mineral 500 ml", "Bebidas", "Cachantun", "0", "", 4, 24, 300, 700),
    ("7801003", "Galletas de chocolate", "Alimentos", "McKay", "0", "", 5, 12, 450, 990),
    ("7801004", "Leche entera 1 L", "Alimentos", "Colún", "1", "", 4, 12, 750, 1190),
    ("7801005", "Pan de molde", "Alimentos", "Ideal", "1", "", 3, 10, 1300, 2290),
    ("7801006", "Detergente líquido 3 L", "Limpieza", "Omo", "", "", 2, 6, 5200, 8490),
    ("7801007", "Audífonos bluetooth", "Tecnología", "JBL", "", "12", 2, 5, 18000, 29990),
    ("7801008", "Televisor 50\" 4K", "Televisores", "Samsung", "", "24", 1, 2, 260000, 389990),
    ("7801009", "Silla de escritorio", "Mueblería", "Ergo", "", "6", 1, 4, 35000, 59990),
]
COMPROBANTE = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def main() -> None:
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        if db.query_one("SELECT id FROM empresas WHERE rut = '76123456-0'"):
            print("La empresa demo ya existe. Usa 'python init_db.py --reset' para empezar de cero.")
            return

    admin = app.test_client()
    admin.post("/registro/empleador", data=dict(
        nombre_fantasia="Minimarket Demo", razon_social="Comercial Demo SpA", rut="76.123.456-0",
        nombre="Carla", apellido="Muñoz", email="admin@demo.cl", password=PASSWORD, password2=PASSWORD))
    admin.post("/admin/configuracion", data=dict(razon_social="Comercial Demo SpA", nombre_fantasia="Minimarket Demo",
                                                 direccion="Av. Providencia 1234, Santiago", telefono="+56 2 2345 6789"))
    for rut, nombre, apellido, rol in [("11.111.111-1", "Luis", "Rojas", "BODEGUERO"),
                                       ("12.345.678-5", "Ana", "Soto", "VENDEDOR"),
                                       ("22.222.222-2", "Pedro", "Díaz", "VENDEDOR")]:
        app.test_client().post("/registro/trabajador", data=dict(
            nombre_empresa="Minimarket Demo", rut_empresa="76.123.456-0", nombre=nombre, apellido=apellido,
            rut=rut, rol=rol, password=PASSWORD, password2=PASSWORD))
    with app.app_context():
        pendientes = db.query("SELECT id FROM usuarios WHERE estado = 'PENDIENTE'")
    for fila in pendientes:
        admin.post(f"/admin/trabajadores/{fila['id']}/estado/aprobar")
    # Una solicitud queda pendiente para ver el flujo de aprobación.
    app.test_client().post("/registro/trabajador", data=dict(
        nombre_empresa="Minimarket Demo", rut_empresa="76.123.456-0", nombre="Sofía", apellido="Vera",
        rut="33.333.333-3", rol="VENDEDOR", password=PASSWORD, password2=PASSWORD))

    bodega = app.test_client()
    bodega.post("/login", data=dict(identificador="11111111-1", password=PASSWORD))
    with app.app_context():
        categorias = {c["nombre"]: c["id"] for c in db.query("SELECT id, nombre FROM categorias")}
    vence = (date.today() + timedelta(days=40)).isoformat()
    for i, (sku, nombre, categoria, marca, perecible, garantia, cajas, por_caja, costo, precio) in enumerate(PRODUCTOS):
        bodega.post("/bodega/lotes", data=dict(
            codigo_sku=sku, nombre=nombre, categoria_id=categorias[categoria], marca=marca, es_perecible=perecible,
            meses_garantia=garantia, numero_lote=f"L-{date.today():%Y%m}-{i + 1:03d}", cantidad_bultos=cajas,
            unidades_por_bulto=por_caja, modo_costo="UNITARIO", costo=costo, precio_venta=precio,
            stock_critico=max(por_caja // 2, 2), fecha_vencimiento=vence if perecible == "1" else ""))
        bodega.post("/bodega/transferencias", data=dict(codigo_sku=sku, cantidad=cajas * por_caja * 2 // 3))
    # Un lote de leche que vence pronto, para ver la alerta de vencimiento.
    bodega.post("/bodega/lotes", data=dict(codigo_sku="7801004", numero_lote="L-PRONTO", cantidad_bultos=1,
                                           unidades_por_bulto=12, modo_costo="BULTO", costo=8400, precio_venta=1190,
                                           fecha_vencimiento=(date.today() + timedelta(days=5)).isoformat()))

    random.seed(7)
    vendedores = []
    for rut in ("12345678-5", "22222222-2"):
        cliente = app.test_client()
        cliente.post("/login", data=dict(identificador=rut, password=PASSWORD))
        vendedores.append(cliente)
    ventas = 0
    for _ in range(45):
        items = [{"sku": sku, "cantidad": random.randint(1, 3)}
                 for sku, *_ in random.sample(PRODUCTOS, k=random.randint(1, 3))]
        respuesta = random.choice(vendedores).post("/ventas/api/confirmar", data={
            "items": json.dumps(items), "medio_pago": random.choice(["EFECTIVO", "DEBITO", "CREDITO"]),
            "descuento": random.choice(["0", "0", "0", "500"]),
            "comprobante": (io.BytesIO(COMPROBANTE), "boleta.png")}, content_type="multipart/form-data")
        ventas += respuesta.status_code == 200

    bodega.post("/bodega/mermas", data=dict(codigo_sku="7801004", cantidad=3, origen="BODEGA", motivo="VENCIMIENTO"))
    bodega.post("/bodega/mermas", data=dict(codigo_sku="7801001", cantidad=2, origen="TIENDA", motivo="DETERIORO_FISICO",
                                            observacion="Botellas rotas en góndola"))
    for sku, *_ in PRODUCTOS[:5]:
        with app.app_context():
            fila = db.query_one(
                """SELECT i.stock_bodega + i.stock_tienda AS total FROM inventario i
                   JOIN productos p ON p.id = i.producto_id WHERE p.codigo_sku = %s""", (sku,))
        bodega.post("/bodega/conteos", data=dict(codigo_sku=sku, stock_fisico=max(fila["total"] - random.randint(0, 1), 0)))

    print(f"Empresa demo creada con {len(PRODUCTOS)} productos y {ventas} ventas.")
    print(f"Ingresa con admin@demo.cl / {PASSWORD}  (bodeguero 11.111.111-1, vendedor 12.345.678-5)")


if __name__ == "__main__":
    main()
