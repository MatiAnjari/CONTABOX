"""Generación del comprobante de venta (boleta o factura) en PDF con fpdf2."""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from flask import current_app
from fpdf import FPDF

from ..utils.rut import formatear_rut
from .ventas import MEDIOS_PAGO, TIPOS_DOCUMENTO

AZUL = (31, 78, 121)
GRIS = (110, 110, 110)
ROJO = (190, 30, 45)


def _t(texto: Any) -> str:
    """Las fuentes base del PDF usan Latin-1: se reemplazan los caracteres no soportados."""
    return str(texto if texto is not None else "").encode("latin-1", "replace").decode("latin-1")


def _pesos(valor: Any) -> str:
    return "$" + f"{float(valor or 0):,.0f}".replace(",", ".")


class _Documento(FPDF):
    def __init__(self, pie: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.pie = pie
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(15, 15, 15)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*GRIS)
        self.cell(0, 4, _t(self.pie), align="C")
        self.ln(4)
        self.cell(0, 4, _t(f"Página {self.page_no()}/{{nb}}"), align="C")


def generar_pdf(venta: dict[str, Any], empresa: dict[str, Any]) -> bytes:
    """Construye el PDF del comprobante a partir de la venta (inmutable) y los datos de la empresa."""
    tipo = TIPOS_DOCUMENTO[venta["tipo_documento"]]
    pdf = _Documento("Documento generado por ContaBox. Representación interna; no reemplaza el documento "
                     "tributario electrónico emitido ante el SII.")
    pdf.alias_nb_pages()
    pdf.add_page()
    ancho = pdf.w - pdf.l_margin - pdf.r_margin

    # --- Encabezado: logo + datos de la PYME | recuadro del documento
    y0 = pdf.get_y()
    x_texto = pdf.l_margin
    if empresa.get("logo_url"):
        ruta_logo = os.path.join(current_app.static_folder, empresa["logo_url"])
        if os.path.exists(ruta_logo):
            try:
                pdf.image(ruta_logo, x=pdf.l_margin, y=y0, h=22)
                x_texto = pdf.l_margin + 30
            except Exception:  # logo dañado: el documento se emite igual
                current_app.logger.warning("No se pudo insertar el logo %s", ruta_logo)
    pdf.set_xy(x_texto, y0)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*AZUL)
    pdf.cell(110, 7, _t(empresa["nombre_fantasia"]), new_x="LEFT", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0, 0, 0)
    for linea in (empresa["razon_social"], f"RUT {formatear_rut(empresa['rut'])}",
                  empresa.get("direccion") or "", f"Tel. {empresa['telefono']}" if empresa.get("telefono") else ""):
        if linea:
            pdf.cell(110, 4.5, _t(linea), new_x="LEFT", new_y="NEXT")

    caja_x, caja_w = pdf.w - pdf.r_margin - 62, 62
    pdf.set_draw_color(*ROJO)
    pdf.set_line_width(0.6)
    pdf.rect(caja_x, y0, caja_w, 26)
    pdf.set_text_color(*ROJO)
    pdf.set_xy(caja_x, y0 + 3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(caja_w, 5, _t(f"R.U.T.: {formatear_rut(empresa['rut'])}"), align="C", new_x="LEFT", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(caja_w, 7, _t(tipo.upper()), align="C", new_x="LEFT", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(caja_w, 6, _t(f"N° {venta['folio']}"), align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(200, 200, 200)
    pdf.set_y(max(pdf.get_y(), y0 + 30))

    # --- Datos de la transacción
    pdf.set_font("Helvetica", "", 9)
    datos = [
        ("Folio", f"{tipo} N° {venta['folio']}"),
        ("Fecha y hora", venta["fecha_hora"].strftime("%d/%m/%Y %H:%M:%S")),
        ("Vendedor", f"{venta['vendedor']} (RUT {formatear_rut(venta['rut_vendedor'])})"),
        ("Medio de pago", MEDIOS_PAGO[venta["medio_pago"]]),
    ]
    if venta["tipo_documento"] == "FACTURA":
        datos += [
            ("Cliente", f"{venta['cliente_razon_social']} (RUT {formatear_rut(venta['cliente_rut'])})"),
            ("Giro", venta["cliente_giro"]),
            ("Dirección", venta["cliente_direccion"]),
        ]
    pdf.set_fill_color(244, 246, 249)
    for etiqueta, valor in datos:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(32, 5.5, _t(etiqueta), fill=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(ancho - 32, 5.5, _t(valor), fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # --- Detalle de productos
    columnas = [("SKU", 24, "L"), ("Producto", 62, "L"), ("Marca", 30, "L"), ("Cant.", 14, "R"),
                ("P. unitario", 25, "R"), ("Subtotal", ancho - 155, "R")]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*AZUL)
    pdf.set_text_color(255, 255, 255)
    for titulo, w, alin in columnas:
        pdf.cell(w, 7, _t(titulo), border=0, align=alin, fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    for i, d in enumerate(venta["detalle"]):
        pdf.set_fill_color(*((248, 249, 251) if i % 2 else (255, 255, 255)))
        valores = [d["codigo_sku"], d["nombre"], d["marca"], str(d["cantidad"]),
                   _pesos(d["precio_unitario"]), _pesos(d["subtotal"])]
        for (_titulo, w, alin), valor in zip(columnas, valores):
            texto = _t(valor)
            while pdf.get_string_width(texto) > w - 2 and len(texto) > 3:
                texto = texto[:-2] + "."
            pdf.cell(w, 6.5, texto, align=alin, fill=True)
        pdf.ln()
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)

    # --- Totales
    def fila_total(etiqueta: str, valor: Any, negrita: bool = False, signo: str = "") -> None:
        pdf.set_x(pdf.w - pdf.r_margin - 80)
        pdf.set_font("Helvetica", "B" if negrita else "", 11 if negrita else 9)
        pdf.cell(45, 6.5 if negrita else 5.5, _t(etiqueta), align="R")
        pdf.cell(35, 6.5 if negrita else 5.5, _t(signo + _pesos(valor)), align="R", new_x="LMARGIN", new_y="NEXT")

    fila_total("Subtotal", venta["subtotal"])
    if Decimal(venta["descuento"]) > 0:
        fila_total("Descuento", venta["descuento"], signo="-")
    fila_total("Monto neto", venta["monto_neto"])
    fila_total("IVA (19%)", venta["monto_iva"])
    fila_total("TOTAL PAGADO", venta["monto_total"], negrita=True)
    pdf.ln(4)

    # --- Garantías
    con_garantia = [d for d in venta["detalle"] if d["meses_garantia_aplicados"]]
    if con_garantia:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*AZUL)
        pdf.cell(0, 6, _t("Garantías"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        for d in con_garantia:
            meses = d["meses_garantia_aplicados"]
            texto = (f"{d['codigo_sku']} {d['nombre']} ({d['marca']}): Garantía válida por {meses} "
                     f"mes{'es' if meses != 1 else ''} - Vigente hasta: {d['fecha_fin_garantia']:%d/%m/%Y}")
            pdf.multi_cell(0, 5, _t(texto), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*GRIS)
        pdf.multi_cell(0, 4, _t("Conserve este documento: es necesario para hacer efectiva la garantía. "
                                "La garantía legal mínima es de 6 meses según la Ley 19.496."),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    # --- Venta anulada
    if venta["estado"] == "ANULADA":
        nc = venta.get("nota_credito")
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*ROJO)
        detalle = f" por Nota de Crédito N° {nc['numero']} del {nc['fecha_hora']:%d/%m/%Y}" if nc else ""
        pdf.multi_cell(0, 6, _t(f"DOCUMENTO ANULADO{detalle}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())


def nombre_archivo(venta: dict[str, Any]) -> str:
    return f"{venta['tipo_documento'].lower()}_{venta['folio']:06d}.pdf"
