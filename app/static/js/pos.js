/* Punto de Venta: carrito en memoria, verificación de stock en tiempo real, totales con IVA,
   confirmación asíncrona y descarga automática del comprobante en PDF. */
"use strict";

(() => {
  const carrito = new Map(); // sku -> {sku, nombre, marca, precio, stock, cantidad, garantia}
  const $ = (id) => document.getElementById(id);
  const inputSku = $("sku"), inputCantidad = $("cantidad"), mensaje = $("mensajePos");
  const cuerpo = $("carrito"), btnConfirmar = $("btnConfirmar"), inputDescuento = $("descuento");

  const avisar = (texto, tipo = "danger") => {
    mensaje.className = `mt-2 small text-${tipo}`;
    mensaje.textContent = texto;
  };
  const escapar = (t) => String(t).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const leerPesos = (t) => Number(String(t).replace(/[^\d]/g, "")) || 0;

  function dibujar() {
    cuerpo.innerHTML = "";
    let subtotal = 0, unidades = 0;
    if (carrito.size === 0) {
      cuerpo.innerHTML = '<tr><td colspan="5" class="text-muted text-center py-4">Escanea un producto para comenzar.</td></tr>';
    }
    carrito.forEach((item) => {
      const linea = item.precio * item.cantidad;
      subtotal += linea; unidades += item.cantidad;
      const garantia = item.garantia ? `<span class="badge badge-garantia ms-1">Garantía ${item.garantia} meses</span>` : "";
      const fila = document.createElement("tr");
      fila.innerHTML = `
        <td><div class="fw-semibold">${escapar(item.nombre)}${garantia}</div>
            <div class="small text-muted">${escapar(item.sku)} · ${escapar(item.marca)} · stock tienda ${item.stock}</div></td>
        <td class="text-end">${ContaBox.pesos(item.precio)}</td>
        <td class="text-center" style="width:110px"><input type="number" class="form-control text-center" min="1" max="${item.stock}" value="${item.cantidad}"></td>
        <td class="text-end">${ContaBox.pesos(linea)}</td>
        <td class="text-end"><button type="button" class="btn btn-sm btn-outline-danger" aria-label="Quitar">✕</button></td>`;
      fila.querySelector("input").addEventListener("change", (e) => {
        const nueva = parseInt(e.target.value, 10);
        if (!(nueva > 0)) { e.target.value = item.cantidad; return; }
        if (nueva > item.stock) { avisar(`Solo hay ${item.stock} u. de ${item.nombre} en tienda.`); e.target.value = item.cantidad; return; }
        item.cantidad = nueva; dibujar();
      });
      fila.querySelector("button").addEventListener("click", () => { carrito.delete(item.sku); dibujar(); });
      cuerpo.appendChild(fila);
    });
    // Precios con IVA incluido: neto = total / 1,19
    const descuento = Math.min(leerPesos(inputDescuento.value), subtotal);
    const total = subtotal - descuento;
    const neto = Math.round(total / 1.19);
    $("tSubtotal").textContent = ContaBox.pesos(subtotal);
    $("tNeto").textContent = ContaBox.pesos(neto);
    $("tIva").textContent = ContaBox.pesos(total - neto);
    $("total").textContent = ContaBox.pesos(total);
    $("resumenUnidades").textContent = `${unidades} unidad${unidades === 1 ? "" : "es"}`;
    btnConfirmar.disabled = carrito.size === 0;
  }

  async function agregar(e) {
    e.preventDefault();
    const sku = inputSku.value.trim().toUpperCase();
    const cantidad = parseInt(inputCantidad.value, 10) || 1;
    if (!sku) return;
    const {ok, data} = await ContaBox.obtenerJSON(`${window.POS.urlProducto}?sku=${encodeURIComponent(sku)}`);
    inputSku.value = ""; inputCantidad.value = 1; inputSku.focus();
    if (!ok) { avisar(data.error || "Código no encontrado."); return; }
    const actual = carrito.get(data.sku);
    const nuevaCantidad = (actual ? actual.cantidad : 0) + cantidad;
    if (nuevaCantidad > data.stock_tienda) {
      avisar(`Stock insuficiente: ${data.nombre} tiene ${data.stock_tienda} u. en tienda` +
             (data.stock_bodega ? ` (y ${data.stock_bodega} en bodega; pide una transferencia).` : "."));
      return;
    }
    carrito.set(data.sku, {sku: data.sku, nombre: data.nombre, marca: data.marca, precio: data.precio,
                           stock: data.stock_tienda, cantidad: nuevaCantidad, garantia: data.meses_garantia});
    avisar(`${data.nombre} agregado.`, "success");
    dibujar();
  }

  /** Descarga el PDF con un enlace temporal (queda en la carpeta Descargas del navegador). */
  async function descargar(url) {
    try {
      const respuesta = await fetch(url, {credentials: "same-origin"});
      if (!respuesta.ok) return;
      const blob = await respuesta.blob();
      const nombre = (respuesta.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
      const enlace = document.createElement("a");
      enlace.href = URL.createObjectURL(blob);
      enlace.download = nombre ? nombre[1] : "comprobante.pdf";
      document.body.appendChild(enlace);
      enlace.click();
      setTimeout(() => { URL.revokeObjectURL(enlace.href); enlace.remove(); }, 1000);
    } catch (e) { /* si falla, el documento sigue disponible en la página de la venta */ }
  }

  async function confirmar(e) {
    e.preventDefault();
    if (carrito.size === 0) return;
    const datos = new FormData($("formVenta"));
    datos.set("descuento", String(leerPesos(inputDescuento.value)));
    datos.append("items", JSON.stringify([...carrito.values()].map((i) => ({sku: i.sku, cantidad: i.cantidad}))));
    btnConfirmar.disabled = true;
    btnConfirmar.textContent = "Procesando…";
    try {
      const respuesta = await fetch(window.POS.urlConfirmar, {
        method: "POST", body: datos, credentials: "same-origin",
        headers: {"X-CSRFToken": ContaBox.csrf(), Accept: "application/json"},
      });
      const data = await respuesta.json().catch(() => ({error: "Respuesta inválida del servidor."}));
      if (!respuesta.ok) { avisar(data.error || "No se pudo registrar la venta."); return; }
      avisar(`Venta registrada: ${data.tipo_documento === "FACTURA" ? "Factura" : "Boleta"} N° ${data.folio}. Descargando PDF…`, "success");
      await descargar(data.url_pdf);
      window.location.href = data.url_boleta;
    } catch (err) {
      avisar("Sin conexión con el servidor. La venta no se registró.");
    } finally {
      btnConfirmar.textContent = "Confirmar venta";
      btnConfirmar.disabled = carrito.size === 0;
    }
  }

  document.querySelectorAll('input[name="tipo_documento"]').forEach((r) => r.addEventListener("change", () => {
    const factura = document.getElementById("td-factura").checked;
    $("datosFactura").classList.toggle("d-none", !factura);
    $("datosFactura").querySelectorAll("input").forEach((i) => { i.required = factura; });
  }));
  inputDescuento.addEventListener("input", dibujar);
  $("formEscaneo").addEventListener("submit", agregar);
  $("formVenta").addEventListener("submit", confirmar);
  dibujar();
})();
