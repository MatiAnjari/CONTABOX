"""Lógica de negocio de cuentas: empresas, usuarios y trabajadores."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from flask import current_app

from .. import db
from ..auditoria import auditar
from ..errors import ErrorNegocio
from ..security import hash_password, password_temporal, validar_password, verificar_password
from ..utils.rut import motivo_rut_invalido, normalizar_rut
from .catalogo import crear_categorias_base

CORREO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROL_ID = {"ADMIN": 1, "BODEGUERO": 2, "VENDEDOR": 3}
CARGO = {"ADMIN": "Administrador", "BODEGUERO": "Bodeguero", "VENDEDOR": "Vendedor"}


def _texto(valor: Any, campo: str, maximo: int, errores: list[str]) -> str:
    texto = (valor or "").strip()
    if not texto:
        errores.append(f"Ingresa {campo}.")
    elif len(texto) > maximo:
        errores.append(f"{campo.capitalize()} admite hasta {maximo} caracteres.")
    return texto


def _correo(valor: Any, obligatorio: bool, errores: list[str]) -> str | None:
    correo = (valor or "").strip().lower() or None
    if correo is None:
        if obligatorio:
            errores.append("Ingresa un correo válido.")
        return None
    if not CORREO_RE.match(correo) or len(correo) > 150:
        errores.append("El correo no es válido.")
    return correo


def _comparable(texto: Any) -> str:
    """Normaliza un nombre para compararlo sin importar mayúsculas, tildes ni espacios repetidos."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().replace(".", "").replace(",", " ").split())


def _rut_ocupado(cuenta: dict[str, Any], empresa_id: int | None) -> str:
    """Mensaje cuando el RUT ya pertenece a otra cuenta (el RUT identifica a una sola persona en ContaBox)."""
    if empresa_id is not None and cuenta["empresa_id"] == empresa_id:
        if cuenta["estado"] == "PENDIENTE":
            return "Ese RUT ya tiene una solicitud pendiente en esta empresa. Apruébala en Trabajadores → Pendientes."
        if cuenta["estado"] == "BAJA":
            return "Ese RUT pertenece a un trabajador dado de baja en esta empresa. Reactívalo en Trabajadores → De baja."
        return "Ese RUT ya pertenece a una cuenta de esta empresa."
    return "Ese RUT ya está registrado en ContaBox con otra cuenta (puede ser de otra empresa). Cada persona usa un solo RUT para ingresar."


def _verificar_unicos(rut: str | None, correo: str | None, errores: list[str], excluir_usuario: int | None = None,
                      empresa_id: int | None = None) -> None:
    if rut:
        fila = db.query_one(
            "SELECT u.id, u.empresa_id, u.estado FROM trabajadores t JOIN usuarios u ON u.id = t.usuario_id WHERE t.rut = %s",
            (rut,),
        )
        if fila and fila["id"] != excluir_usuario:
            errores.append(_rut_ocupado(fila, empresa_id))
    if correo:
        fila = db.query_one("SELECT id FROM usuarios WHERE email = %s", (correo,))
        if fila and fila["id"] != excluir_usuario:
            errores.append("Ya existe una cuenta registrada con ese correo.")


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
def autenticar(identificador: str, password: str) -> dict[str, Any]:
    """Valida credenciales por correo o RUT. Devuelve el usuario o lanza ErrorNegocio."""
    identificador = (identificador or "").strip()
    if "@" in identificador:
        usuario = db.query_one("SELECT * FROM usuarios WHERE email = %s", (identificador.lower(),))
    else:
        rut = normalizar_rut(identificador)
        usuario = rut and db.query_one(
            "SELECT u.* FROM usuarios u JOIN trabajadores t ON t.usuario_id = u.id WHERE t.rut = %s", (rut,)
        )

    if not usuario or not verificar_password(password, usuario["password_hash"]):
        with db.transaccion() as cur:
            auditar(cur, "LOGIN_FALLIDO", "usuarios", usuario["id"] if usuario else None,
                    f"identificador={identificador[:60]}",
                    empresa_id=usuario["empresa_id"] if usuario else None,
                    usuario_id=usuario["id"] if usuario else None)
        raise ErrorNegocio("Correo/RUT o contraseña incorrectos.")

    mensajes = {
        "PENDIENTE": "Tu solicitud aún no ha sido aprobada por el administrador.",
        "RECHAZADO": "Tu solicitud fue rechazada por el administrador.",
        "BAJA": "Tu cuenta fue dada de baja. Contacta al administrador de tu empresa.",
    }
    if usuario["estado"] != "ACTIVO":
        raise ErrorNegocio(mensajes[usuario["estado"]])

    with db.transaccion() as cur:
        cur.execute("UPDATE usuarios SET ultimo_ingreso = NOW() WHERE id = %s", (usuario["id"],))
        auditar(cur, "LOGIN", "usuarios", usuario["id"], empresa_id=usuario["empresa_id"], usuario_id=usuario["id"])
    return usuario


def cambiar_password(usuario: dict[str, Any], actual: str, nueva: str, confirmacion: str) -> None:
    """Cambia la contraseña del usuario conectado y quita la marca de credencial temporal."""
    errores = validar_password(nueva, confirmacion)
    if not verificar_password(actual, usuario["password_hash"]):
        errores.insert(0, "La contraseña actual no es correcta.")
    elif actual == nueva:
        errores.append("La nueva contraseña debe ser distinta a la actual.")
    if errores:
        raise ErrorNegocio(" ".join(errores))
    with db.transaccion() as cur:
        cur.execute(
            "UPDATE usuarios SET password_hash = %s, debe_cambiar_password = 0 WHERE id = %s AND empresa_id = %s",
            (hash_password(nueva), usuario["id"], usuario["empresa_id"]),
        )
        auditar(cur, "UPDATE", "usuarios", usuario["id"], "cambio de contraseña")


# ---------------------------------------------------------------------------
# Registro de empresa (administrador) con mes gratis
# ---------------------------------------------------------------------------
def registrar_empresa(form: dict[str, Any]) -> int:
    """Crea la empresa, su usuario administrador y su ficha. Devuelve el id del usuario."""
    errores: list[str] = []
    nombre_fantasia = _texto(form.get("nombre_fantasia"), "el nombre de la tienda", 120, errores)
    razon_social = (form.get("razon_social") or "").strip() or nombre_fantasia
    rut = normalizar_rut(form.get("rut", ""))
    if not rut:
        errores.append(motivo_rut_invalido(form.get("rut", ""), "El RUT de la empresa"))
    nombre = _texto(form.get("nombre"), "tu nombre", 80, errores)
    apellido = _texto(form.get("apellido"), "tu apellido", 80, errores)
    correo = _correo(form.get("email"), True, errores)
    errores += validar_password(form.get("password", ""), form.get("password2", ""))

    if not errores:
        if db.query_one("SELECT id FROM empresas WHERE rut = %s", (rut,)):
            errores.append("Ya existe una empresa registrada con ese RUT.")
        _verificar_unicos(rut, correo, errores)
    if errores:
        raise ErrorNegocio(" ".join(dict.fromkeys(errores)))

    prueba_hasta = datetime.now() + timedelta(days=current_app.config["DIAS_PRUEBA"])
    with db.transaccion() as cur:
        cur.execute(
            "INSERT INTO empresas (rut, razon_social, nombre_fantasia, prueba_hasta) VALUES (%s, %s, %s, %s)",
            (rut, razon_social[:150], nombre_fantasia, prueba_hasta),
        )
        empresa_id = cur.lastrowid
        cur.execute(
            """INSERT INTO usuarios (empresa_id, rol_id, email, password_hash, estado, aprobado_en)
               VALUES (%s, %s, %s, %s, 'ACTIVO', NOW())""",
            (empresa_id, ROL_ID["ADMIN"], correo, hash_password(form["password"])),
        )
        usuario_id = cur.lastrowid
        cur.execute(
            "INSERT INTO trabajadores (empresa_id, usuario_id, rut, nombre, apellido, cargo) VALUES (%s, %s, %s, %s, %s, %s)",
            (empresa_id, usuario_id, rut, nombre, apellido, CARGO["ADMIN"]),
        )
        crear_categorias_base(cur, empresa_id)
        auditar(cur, "INSERT", "empresas", empresa_id, f"registro {nombre_fantasia}, prueba hasta {prueba_hasta:%d-%m-%Y}",
                empresa_id=empresa_id, usuario_id=usuario_id)
    return usuario_id


# ---------------------------------------------------------------------------
# Solicitud de ingreso de un trabajador
# ---------------------------------------------------------------------------
def solicitar_ingreso(form: dict[str, Any]) -> None:
    """Registra la solicitud de un trabajador. Queda PENDIENTE hasta que el administrador la apruebe."""
    errores: list[str] = []
    nombre_empresa = _comparable(form.get("nombre_empresa"))
    rut_empresa = normalizar_rut(form.get("rut_empresa", ""))
    if not rut_empresa:
        errores.append(motivo_rut_invalido(form.get("rut_empresa", ""), "El RUT de la empresa"))
    nombre = _texto(form.get("nombre"), "tu nombre", 80, errores)
    apellido = _texto(form.get("apellido"), "tu apellido", 80, errores)
    rut = normalizar_rut(form.get("rut", ""))
    if not rut:
        errores.append(motivo_rut_invalido(form.get("rut", ""), "Tu RUT personal"))
    correo = _correo(form.get("email"), False, errores)
    rol = form.get("rol", "")
    if rol not in ("VENDEDOR", "BODEGUERO"):
        errores.append("Elige si trabajarás como vendedor o bodeguero.")
    errores += validar_password(form.get("password", ""), form.get("password2", ""))

    empresa = None
    if rut_empresa:
        empresa = db.query_one("SELECT * FROM empresas WHERE rut = %s", (rut_empresa,))
        nombres = {_comparable(empresa["nombre_fantasia"]), _comparable(empresa["razon_social"])} if empresa else set()
        if not empresa:
            errores.append("No hay ninguna empresa registrada con ese RUT. Confirma el RUT con tu empleador.")
        elif nombre_empresa not in nombres:
            errores.append("El nombre de la empresa no coincide con el RUT. Escribe el nombre de fantasía o la razón social.")

    existente = None
    if rut and not errores:
        existente = db.query_one(
            "SELECT u.*, t.id AS trabajador_id FROM usuarios u JOIN trabajadores t ON t.usuario_id = u.id WHERE t.rut = %s",
            (rut,),
        )
        # Un trabajador rechazado puede volver a postular a la misma empresa.
        if existente and not (existente["estado"] == "RECHAZADO" and existente["empresa_id"] == empresa["id"]):
            errores.append(_rut_ocupado(existente, empresa["id"]))
        else:
            _verificar_unicos(None, correo, errores, excluir_usuario=existente["id"] if existente else None)
    if errores:
        raise ErrorNegocio(" ".join(dict.fromkeys(errores)))

    with db.transaccion() as cur:
        if existente:
            cur.execute(
                """UPDATE usuarios SET rol_id = %s, email = %s, password_hash = %s, estado = 'PENDIENTE',
                   creado_en = NOW(), aprobado_en = NULL WHERE id = %s AND empresa_id = %s""",
                (ROL_ID[rol], correo, hash_password(form["password"]), existente["id"], empresa["id"]),
            )
            cur.execute(
                "UPDATE trabajadores SET nombre = %s, apellido = %s, cargo = %s WHERE id = %s AND empresa_id = %s",
                (nombre, apellido, CARGO[rol], existente["trabajador_id"], empresa["id"]),
            )
            usuario_id = existente["id"]
        else:
            cur.execute(
                "INSERT INTO usuarios (empresa_id, rol_id, email, password_hash, estado) VALUES (%s, %s, %s, %s, 'PENDIENTE')",
                (empresa["id"], ROL_ID[rol], correo, hash_password(form["password"])),
            )
            usuario_id = cur.lastrowid
            cur.execute(
                "INSERT INTO trabajadores (empresa_id, usuario_id, rut, nombre, apellido, cargo) VALUES (%s, %s, %s, %s, %s, %s)",
                (empresa["id"], usuario_id, rut, nombre, apellido, CARGO[rol]),
            )
        auditar(cur, "INSERT", "usuarios", usuario_id, f"solicitud de ingreso como {CARGO[rol]}",
                empresa_id=empresa["id"], usuario_id=usuario_id)


# ---------------------------------------------------------------------------
# Administración de trabajadores (solo administrador)
# ---------------------------------------------------------------------------
TRANSICIONES = {
    "aprobar":   (("PENDIENTE",), "ACTIVO",    "aprobado_en = NOW(), baja_en = NULL", "Solicitud de {n} aprobada."),
    "rechazar":  (("PENDIENTE",), "RECHAZADO", None,                                  "Solicitud de {n} rechazada."),
    "baja":      (("ACTIVO",),    "BAJA",      "baja_en = NOW()",                     "Se dio de baja a {n}. Su historial se conserva."),
    "reactivar": (("BAJA",),      "ACTIVO",    "baja_en = NULL",                      "Se reactivó la cuenta de {n}."),
}


def obtener_trabajador(empresa_id: int, usuario_id: int) -> dict[str, Any] | None:
    """Ficha de un trabajador de la empresa (nunca de otra empresa, RN01)."""
    return db.query_one(
        """SELECT u.id, u.email, u.estado, u.creado_en, u.aprobado_en, u.baja_en, u.ultimo_ingreso,
                  u.debe_cambiar_password, r.codigo AS rol, r.nombre_rol,
                  t.id AS trabajador_id, t.rut, t.nombre, t.apellido, t.cargo, t.telefono
           FROM usuarios u JOIN roles r ON r.id = u.rol_id JOIN trabajadores t ON t.usuario_id = u.id
           WHERE u.id = %s AND u.empresa_id = %s AND r.codigo <> 'ADMIN'""",
        (usuario_id, empresa_id),
    )


def cambiar_estado(empresa_id: int, usuario_id: int, accion: str) -> str:
    """Aprueba, rechaza, da de baja o reactiva a un trabajador. Devuelve el mensaje para el usuario."""
    if accion not in TRANSICIONES:
        raise ErrorNegocio("Acción no válida.")
    desde, nuevo, extra, mensaje = TRANSICIONES[accion]
    with db.transaccion() as cur:
        cur.execute(
            """SELECT u.estado, CONCAT(t.nombre, ' ', t.apellido) AS nombre FROM usuarios u
               JOIN trabajadores t ON t.usuario_id = u.id
               WHERE u.id = %s AND u.empresa_id = %s AND u.rol_id <> %s FOR UPDATE""",
            (usuario_id, empresa_id, ROL_ID["ADMIN"]),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorNegocio("Trabajador no encontrado.")
        if fila["estado"] not in desde:
            raise ErrorNegocio("Esa acción no corresponde al estado actual del trabajador.")
        sets = "estado = %s" + (f", {extra}" if extra else "")
        cur.execute(f"UPDATE usuarios SET {sets} WHERE id = %s AND empresa_id = %s", (nuevo, usuario_id, empresa_id))
        auditar(cur, "UPDATE", "usuarios", usuario_id, f"{accion}: {fila['estado']} -> {nuevo}")
    return mensaje.format(n=fila["nombre"])


def crear_trabajador(empresa_id: int, form: dict[str, Any]) -> tuple[int, str]:
    """El administrador crea directamente un trabajador con credencial temporal.

    Devuelve (usuario_id, contraseña_temporal). La contraseña se muestra una sola vez.
    """
    errores: list[str] = []
    nombre = _texto(form.get("nombre"), "el nombre", 80, errores)
    apellido = _texto(form.get("apellido"), "el apellido", 80, errores)
    rut = normalizar_rut(form.get("rut", ""))
    if not rut:
        errores.append(motivo_rut_invalido(form.get("rut", ""), "El RUT del trabajador"))
    correo = _correo(form.get("email"), False, errores)
    rol = form.get("rol", "")
    if rol not in ("VENDEDOR", "BODEGUERO"):
        errores.append("Elige el rol del trabajador.")
    if not errores:
        _verificar_unicos(rut, correo, errores, empresa_id=empresa_id)
    if errores:
        raise ErrorNegocio(" ".join(errores))

    temporal = password_temporal()
    with db.transaccion() as cur:
        cur.execute(
            """INSERT INTO usuarios (empresa_id, rol_id, email, password_hash, estado, debe_cambiar_password, aprobado_en)
               VALUES (%s, %s, %s, %s, 'ACTIVO', 1, NOW())""",
            (empresa_id, ROL_ID[rol], correo, hash_password(temporal)),
        )
        usuario_id = cur.lastrowid
        cur.execute(
            "INSERT INTO trabajadores (empresa_id, usuario_id, rut, nombre, apellido, cargo) VALUES (%s, %s, %s, %s, %s, %s)",
            (empresa_id, usuario_id, rut, nombre, apellido, CARGO[rol]),
        )
        auditar(cur, "INSERT", "usuarios", usuario_id, f"alta directa como {CARGO[rol]} con credencial temporal")
    return usuario_id, temporal


def restablecer_password(empresa_id: int, usuario_id: int) -> str:
    """Asigna una nueva credencial temporal a un trabajador de la empresa."""
    if obtener_trabajador(empresa_id, usuario_id) is None:
        raise ErrorNegocio("Trabajador no encontrado.")
    temporal = password_temporal()
    with db.transaccion() as cur:
        cur.execute(
            "UPDATE usuarios SET password_hash = %s, debe_cambiar_password = 1 WHERE id = %s AND empresa_id = %s",
            (hash_password(temporal), usuario_id, empresa_id),
        )
        auditar(cur, "UPDATE", "usuarios", usuario_id, "credencial temporal restablecida")
    return temporal


def actualizar_empresa(empresa_id: int, form: dict[str, Any], logo_url: str | None) -> None:
    """Actualiza los datos de la empresa que aparecen en la interfaz y en los comprobantes."""
    errores: list[str] = []
    razon_social = _texto(form.get("razon_social"), "la razón social", 150, errores)
    nombre_fantasia = _texto(form.get("nombre_fantasia"), "el nombre de fantasía", 120, errores)
    direccion = (form.get("direccion") or "").strip()
    telefono = (form.get("telefono") or "").strip()
    if len(direccion) > 200:
        errores.append("La dirección admite hasta 200 caracteres.")
    if len(telefono) > 30:
        errores.append("El teléfono admite hasta 30 caracteres.")
    if errores:
        raise ErrorNegocio(" ".join(errores))
    with db.transaccion() as cur:
        cur.execute(
            """UPDATE empresas SET razon_social = %s, nombre_fantasia = %s, direccion = %s, telefono = %s,
                      logo_url = COALESCE(%s, logo_url)
               WHERE id = %s""",
            (razon_social, nombre_fantasia, direccion or None, telefono or None, logo_url, empresa_id),
        )
        auditar(cur, "UPDATE", "empresas", empresa_id, f"configuración: {nombre_fantasia}" + (" + logo" if logo_url else ""))
