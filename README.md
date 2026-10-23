# ContaBox

Plataforma web multiempresa de gestión de bodega y ventas para PYMEs comerciales. Ataca el **stock fantasma** con trazabilidad completa: cada lote, transferencia, venta, merma y conteo queda registrado y auditado.

**Arquitectura de tres capas**

| Capa | Tecnología | Dónde está |
|---|---|---|
| Presentación | HTML5, Bootstrap 5, JavaScript ES6 (fetch) | `app/templates`, `app/static` |
| Lógica de negocio | Python 3.11+ con Flask | `app/services` (reglas) y `app/*/routes.py` (controladores) |
| Persistencia | MySQL 8.0, InnoDB, 3FN | `database/schema.sql`, `database/triggers.sql`, `app/db.py` |

Producción: Nginx (TLS 1.3) → Gunicorn → Flask en Ubuntu Server 22.04. Los pasos están en [`deploy/DESPLIEGUE.md`](deploy/DESPLIEGUE.md).

---

## Puesta en marcha en tu computador (Windows + VS Code)

Requisitos: Python 3.11 o superior, MySQL 8 encendido y VS Code con la extensión Python.

> Consejo: deja la carpeta fuera de OneDrive (por ejemplo, `C:\proyectos\contabox`) para evitar bloqueos de archivos.

En la terminal de VS Code:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python init_db.py          # crea la base 'contabox' con tablas y disparadores
python seed_demo.py        # opcional: carga una empresa de prueba con ventas
python run.py
```

Abre <http://127.0.0.1:5000>.

- **El archivo `.env`** ya trae la conexión a MySQL (usuario `root`). Si tu usuario o contraseña son otros, edítalo ahí.
- **Si PowerShell dice que "la ejecución de scripts está deshabilitada"**, ejecuta una vez `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (responde S) y vuelve a correr `venv\Scripts\activate`.
- `python init_db.py --reset` **borra** la base completa y la crea vacía.
- Para ver los errores con detalle mientras programas, pon `APP_DEBUG=1` en `.env`.

**Usuarios de la demo** (contraseña `demo1234`): administrador `admin@demo.cl`, bodeguero `11.111.111-1`, vendedor `12.345.678-5`.

---

## Roles y permisos (RBAC)

| Rol | Puede |
|---|---|
| **Administrador** | Configurar la PYME (razón social, nombre, logo, RUT), aprobar o rechazar solicitudes, crear trabajadores con credencial temporal, dar de baja y reactivar, ver el dashboard de KPIs, la auditoría de pérdidas, el registro de ventas, emitir notas de crédito y ver la bitácora completa. También puede entrar a Bodega y Ventas. |
| **Bodeguero** | Recibir lotes con costo, transferir de bodega a tienda, consultar existencias y alertas de stock crítico, registrar mermas justificadas y conteos físicos. |
| **Vendedor** | Usar el punto de venta con lector de código de barras, verificar stock en tiempo real, confirmar ventas con comprobante y ver sus propias ventas. |

Cualquier acceso fuera del rol responde **HTTP 403**.

## Reglas de negocio implementadas

| Regla | Implementación |
|---|---|
| **RN01** Aislamiento multiempresa | La empresa se toma siempre de la sesión validada contra la base (`app/__init__.py`). Todas las consultas filtran por `empresa_id`. Si alguien pide un recurso de otra empresa, recibe 404. |
| **RN02** RBAC en backend | Decoradores `rol_requerido` y `solo_admin` en cada ruta (`app/acceso.py`). La API responde con JSON 403. |
| **RN03** Sin stock negativo | Se bloquea la fila con `SELECT … FOR UPDATE`, se valida antes de descontar y además hay restricciones `CHECK (stock >= 0)` en MySQL. Está probado con 5 ventas simultáneas del último producto: solo una pasa. |
| **RN04** Mermas justificadas | `perdidas.motivo` es un ENUM obligatorio (Vencimiento, Deterioro físico, Robo/Hurto, Otro). Con "Otro", la justificación escrita es obligatoria. |
| **RN05** Trazabilidad | Toda escritura inserta en `auditoria` el usuario, la entidad, el id, el detalle, la IP y la fecha y hora en milisegundos, dentro de la misma transacción. La tabla está protegida por disparadores. |
| **RN06** Ventas inmutables | Disparadores en MySQL impiden editar o borrar ventas y su detalle. La anulación se hace solo con una **nota de crédito** del administrador, que devuelve el stock a tienda y restituye los lotes exactos. |
| **ACID** | `db.transaccion()` hace `START TRANSACTION … COMMIT`, con `ROLLBACK` automático ante cualquier error. |

Otras decisiones:

- **Costo real (FIFO):** cada venta y cada merma consume los lotes más antiguos primero (`consumo_lotes`). Así el costo de lo vendido y el %MBO son exactos.
- **Mes gratis:** al registrarse, la empresa recibe `DIAS_PRUEBA` días. Al vencer, el acceso se bloquea sin perder datos. El cobro de la suscripción no está implementado.
- **Comprobantes privados:** se guardan en `archivos_privados/`, fuera de `/static`, y solo se entregan a usuarios autorizados de la misma empresa.
- **Seguridad:** contraseñas con Bcrypt (costo 12), CSRF en todos los formularios y en fetch, cookies HttpOnly y SameSite, solo consultas parametrizadas, validación de tipo y tamaño de archivos, y errores sin trazas para el cliente (quedan en el log del servidor).

## Catálogo, lotes por cajas, garantías y comprobantes

**Catálogo maestro (Bodega → Categorías y marcas)**
- Cada empresa parte con estas categorías: Alimentos, Bebidas, Limpieza, Cuidado personal, Ropa, Tecnología, Electrónica, Televisores, Electrodomésticos, Mueblería y Otros. Puedes agregar, renombrar o desactivar categorías.
- Cada producto pertenece a una **marca**. El mismo artículo de otra marca es otro producto, con su propio SKU y precio. Las marcas se crean solas al escribirlas en la recepción.
- En categorías de **alimentos** es obligatorio indicar si el producto es **perecible** o **no perecible**. Si es perecible, cada lote exige fecha de vencimiento.

**Recepción de lotes**
- Se piden el código de lote, la cantidad de **cajas o bultos** y las **unidades por caja**. Stock total = cajas × unidades. Por ejemplo, 2 entregas de 25 cajas son 50 cajas; 50 × 12 = 600 unidades.
- El costo se ingresa **por unidad, por caja o como total del lote**. Se guarda la inversión exacta y un costo unitario con 4 decimales. Así, $10.000 en 3 unidades ya no se muestra como $9.999.
- Ventas y mermas consumen primero el lote que **vence antes** (FEFO) y luego el más antiguo (FIFO).
- Las existencias se ven en **unidades** y en **cajas completas** (por ejemplo, "50 cajas + 3 u."). Hay alertas para los lotes que vencen en los próximos 15 días.

**Garantías**
- Las categorías con garantía (Tecnología 12 meses, Electrónica 12, Televisores 24, Electrodomésticos 12, Mueblería 6) asignan esos meses por defecto, y cada producto puede ajustarlos.
- En cada venta se guarda `fecha_fin_garantia = fecha de venta + meses`, ajustando a fin de mes: 31-01 + 1 mes = 28-02.

**Boleta o factura en PDF**
- En el punto de venta eliges **Boleta** o **Factura**. La factura pide RUT, razón social, giro y dirección del cliente. Cada tipo tiene su propio folio correlativo.
- Puedes aplicar un **descuento** en pesos. Los precios incluyen IVA: neto = total ÷ 1,19 e IVA = total − neto.
- Al confirmar la venta, el PDF se **descarga automáticamente**. Incluye logo, nombre, razón social, RUT, dirección y teléfono de la PYME, el folio, la fecha y hora, el vendedor con su RUT, el medio de pago, el detalle con SKU, producto, marca, cantidad, precio y subtotal, el subtotal, el descuento, el neto, el IVA 19 %, el total y una sección de **garantías** ("Garantía válida por X meses - Vigente hasta: DD/MM/AAAA").
- El PDF se puede volver a descargar desde la página de la venta. La dirección y el teléfono se configuran en **Configuración**.
- Importante: el PDF es un comprobante interno. Para emitir boletas y facturas con validez tributaria se debe integrar un proveedor de documentos electrónicos autorizado por el SII.

## KPIs del dashboard

| Indicador | Fórmula | Meta |
|---|---|---|
| % Precisión de inventario (PPI) | Σ unidades físicas contadas ÷ Σ stock en sistema al contar × 100 (último conteo de cada producto en el período) | ≥ 95 % |
| % Merma y pérdidas (PMR) | Unidades en `perdidas` ÷ unidades ingresadas por lotes × 100 | ≤ 2 % |
| % Margen bruto operacional (MBO) | (Ventas − costo FIFO de lo vendido) ÷ ventas × 100 | ≥ 25 % |

El dashboard también muestra ventas, ganancia bruta, pérdidas, ganancia neta, ventas por día y por vendedor, productos más y menos vendidos, pérdidas por motivo, stock en bodega y en tienda, inventario valorizado y alertas de stock crítico. Todo se filtra por rango de fechas.

## Logo y nombre en el login

En **Configuración**, el administrador sube su logo PNG y define el nombre de fantasía. Para que sus trabajadores vean esa marca, comparte el enlace `…/login?empresa=RUT`, que también lleva al registro de empleados con los datos de la empresa ya completados.

## Hardware

- **Lectores de código de barras USB o Bluetooth:** funcionan como teclado. El campo SKU del POS y de bodega procesa el código al recibir el Enter del lector.
- **Tablets Android:** la interfaz es responsiva, con botones táctiles de 44 px o más y teclado numérico en los campos de cantidad.

## Estructura

```
contabox/
├── run.py                 # arranque (dev) / objeto WSGI para Gunicorn
├── init_db.py             # crea base, tablas y disparadores
├── seed_demo.py           # datos de demostración
├── database/
│   ├── schema.sql         # 18 tablas InnoDB
│   └── triggers.sql       # inmutabilidad de ventas y auditoría
├── deploy/                # Nginx, Gunicorn, systemd, respaldos, guía
└── app/
    ├── __init__.py        # fábrica, carga de sesión, manejo de errores
    ├── acceso.py          # RBAC y vigencia del plan
    ├── auditoria.py       # RN05
    ├── db.py              # conexión y transacciones ACID
    ├── security.py        # Bcrypt y credenciales temporales
    ├── services/          # lógica de negocio: cuentas, inventario, ventas, kpis
    ├── auth/ admin/ bodega/ ventas/   # controladores por módulo
    ├── templates/         # vistas
    └── static/            # CSS, JS, Bootstrap y Chart.js locales, logos
```
