"""Validación y formato de RUT chileno (con dígito verificador)."""
import re


def limpiar_rut(rut):
    return re.sub(r"[^0-9kK]", "", rut or "").upper()


def calcular_dv(cuerpo):
    suma, factor = 0, 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    return {11: "0", 10: "K"}.get(resto, str(resto))


def normalizar_rut(rut):
    """Devuelve el RUT como '12345678-9' si es válido, o None si no lo es."""
    limpio = limpiar_rut(rut)
    if len(limpio) < 2:
        return None
    cuerpo, dv = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit() or len(cuerpo) > 8:
        return None
    if calcular_dv(cuerpo) != dv:
        return None
    return f"{int(cuerpo)}-{dv}"


def motivo_rut_invalido(rut, que="El RUT"):
    """Explica por qué un RUT no es válido, para que la persona sepa qué corregir."""
    limpio = limpiar_rut(rut)
    if not limpio:
        return f"{que} es obligatorio."
    cuerpo, dv = limpio[:-1], limpio[-1]
    if len(limpio) < 2 or not cuerpo.isdigit() or len(cuerpo) > 8:
        return f"{que} no tiene un formato válido. Escríbelo con guion y dígito verificador, por ejemplo 12.345.678-5."
    correcto = calcular_dv(cuerpo)
    return (f"{que} {formatear_rut(f'{int(cuerpo)}-{dv}')} no es válido: "
            f"para {int(cuerpo):,} el dígito verificador es {correcto}.".replace(",", "."))


def formatear_rut(rut):
    """'12345678-9' -> '12.345.678-9' para mostrar en pantalla.

    Acepta también el RUT tal como lo escribió la persona (con o sin puntos).
    Si no se puede interpretar, lo devuelve sin cambios para no romper la página.
    """
    if not rut:
        return ""
    limpio = limpiar_rut(rut)
    cuerpo, dv = limpio[:-1], limpio[-1:]
    if not cuerpo.isdigit() or not dv:
        return str(rut)
    return f"{int(cuerpo):,}".replace(",", ".") + f"-{dv}"
