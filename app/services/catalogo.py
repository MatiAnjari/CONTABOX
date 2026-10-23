"""Catálogo maestro: categorías estilo supermercado y marcas de cada empresa."""
from __future__ import annotations

from typing import Any

from .. import db
from ..auditoria import auditar
from ..errors import ErrorNegocio

# (nombre, es_alimento, admite_garantia, meses de garantía por defecto)
CATEGORIAS_BASE: list[tuple[str, int, int, int]] = [
    ("Alimentos", 1, 0, 0),
    ("Bebidas", 1, 0, 0),
    ("Limpieza", 0, 0, 0),
    ("Cuidado personal", 0, 0, 0),
    ("Ropa", 0, 0, 0),
    ("Tecnología", 0, 1, 12),
    ("Electrónica", 0, 1, 12),
    ("Televisores", 0, 1, 24),
    ("Electrodomésticos", 0, 1, 12),
    ("Mueblería", 0, 1, 6),
    ("Otros", 0, 0, 0),
]


def crear_categorias_base(cur: Any, empresa_id: int) -> None:
    """Carga el set inicial de categorías al registrar una empresa."""
    cur.executemany(
        """INSERT INTO categorias (empresa_id, nombre, es_alimento, admite_garantia, garantia_meses_defecto)
           VALUES (%s, %s, %s, %s, %s)""",
        [(empresa_id, *fila) for fila in CATEGORIAS_BASE],
    )


def listar_categorias(empresa_id: int, solo_activas: bool = True) -> list[dict[str, Any]]:
    filtro = "AND c.activo = 1" if solo_activas else ""
    return db.query(
        f"""SELECT c.*, (SELECT COUNT(*) FROM productos p WHERE p.categoria_id = c.id) AS productos
            FROM categorias c WHERE c.empresa_id = %s {filtro} ORDER BY c.nombre""",
        (empresa_id,),
    )


def listar_marcas(empresa_id: int) -> list[dict[str, Any]]:
    return db.query(
        """SELECT m.*, (SELECT COUNT(*) FROM productos p WHERE p.marca_id = m.id) AS productos
           FROM marcas m WHERE m.empresa_id = %s ORDER BY m.nombre""",
        (empresa_id,),
    )


def obtener_categoria(empresa_id: int, categoria_id: Any) -> dict[str, Any]:
    """Categoría activa de la empresa o ErrorNegocio."""
    try:
        categoria_id = int(categoria_id)
    except (TypeError, ValueError):
        raise ErrorNegocio("Selecciona la categoría del producto.") from None
    categoria = db.query_one(
        "SELECT * FROM categorias WHERE id = %s AND empresa_id = %s AND activo = 1", (categoria_id, empresa_id)
    )
    if categoria is None:
        raise ErrorNegocio("Selecciona una categoría válida.")
    return categoria


def obtener_o_crear_marca(cur: Any, empresa_id: int, nombre: Any) -> int:
    """Devuelve el id de la marca por nombre; la crea si no existe (dentro de la transacción)."""
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        raise ErrorNegocio("Indica la marca del producto.")
    if len(nombre) > 100:
        raise ErrorNegocio("La marca admite hasta 100 caracteres.")
    cur.execute("SELECT id FROM marcas WHERE empresa_id = %s AND nombre = %s", (empresa_id, nombre))
    fila = cur.fetchone()
    if fila:
        return fila["id"]
    cur.execute("INSERT INTO marcas (empresa_id, nombre) VALUES (%s, %s)", (empresa_id, nombre))
    marca_id = cur.lastrowid
    auditar(cur, "INSERT", "marcas", marca_id, nombre)
    return marca_id


def _meses(valor: Any) -> int:
    texto = str(valor or "0").strip()
    if not texto.isdigit() or int(texto) > 120:
        raise ErrorNegocio("Los meses de garantía deben ser un número entre 0 y 120.")
    return int(texto)


def guardar_categoria(empresa_id: int, form: dict[str, Any], categoria_id: int | None = None) -> None:
    """Crea o actualiza una categoría."""
    nombre = " ".join(str(form.get("nombre") or "").split())
    if not nombre or len(nombre) > 100:
        raise ErrorNegocio("El nombre de la categoría es obligatorio (máximo 100 caracteres).")
    es_alimento = 1 if form.get("es_alimento") else 0
    admite_garantia = 1 if form.get("admite_garantia") else 0
    meses = _meses(form.get("garantia_meses_defecto")) if admite_garantia else 0
    if es_alimento and admite_garantia:
        raise ErrorNegocio("Una categoría de alimentos no lleva garantía.")
    activo = 0 if form.get("inactiva") else 1
    duplicada = db.query_one(
        "SELECT id FROM categorias WHERE empresa_id = %s AND nombre = %s", (empresa_id, nombre)
    )
    if duplicada and duplicada["id"] != categoria_id:
        raise ErrorNegocio("Ya existe una categoría con ese nombre.")
    with db.transaccion() as cur:
        if categoria_id is None:
            cur.execute(
                """INSERT INTO categorias (empresa_id, nombre, es_alimento, admite_garantia, garantia_meses_defecto)
                   VALUES (%s, %s, %s, %s, %s)""",
                (empresa_id, nombre, es_alimento, admite_garantia, meses),
            )
            auditar(cur, "INSERT", "categorias", cur.lastrowid, nombre)
        else:
            cur.execute(
                """UPDATE categorias SET nombre = %s, es_alimento = %s, admite_garantia = %s,
                          garantia_meses_defecto = %s, activo = %s
                   WHERE id = %s AND empresa_id = %s""",
                (nombre, es_alimento, admite_garantia, meses, activo, categoria_id, empresa_id),
            )
            if cur.rowcount == 0 and not db.query_one(
                "SELECT id FROM categorias WHERE id = %s AND empresa_id = %s", (categoria_id, empresa_id)
            ):
                raise ErrorNegocio("Categoría no encontrada.")
            auditar(cur, "UPDATE", "categorias", categoria_id,
                    f"{nombre}: alimento={es_alimento}, garantía={meses if admite_garantia else 'no'}, activa={activo}")


def crear_marca(empresa_id: int, nombre: Any) -> None:
    with db.transaccion() as cur:
        cur.execute("SELECT id FROM marcas WHERE empresa_id = %s AND nombre = %s",
                    (empresa_id, " ".join(str(nombre or "").split())))
        if cur.fetchone():
            raise ErrorNegocio("Esa marca ya existe.")
        obtener_o_crear_marca(cur, empresa_id, nombre)


def atributos_producto(empresa_id: int, form: dict[str, Any]) -> dict[str, Any]:
    """Valida categoría, perecible y garantía según las reglas del catálogo.

    - Alimentos: es_perecible obligatorio (0 o 1).
    - Categorías con garantía: meses de garantía configurables (por defecto, los de la categoría).
    """
    categoria = obtener_categoria(empresa_id, form.get("categoria_id"))
    es_perecible = None
    if categoria["es_alimento"]:
        valor = str(form.get("es_perecible", ""))
        if valor not in ("0", "1"):
            raise ErrorNegocio(f"Indica si el producto de {categoria['nombre']} es perecible o no perecible.")
        es_perecible = int(valor)
    meses = 0
    if categoria["admite_garantia"]:
        texto = str(form.get("meses_garantia", "")).strip()
        meses = _meses(texto) if texto != "" else categoria["garantia_meses_defecto"]
    return {"categoria": categoria, "es_perecible": es_perecible, "meses_garantia": meses}
