/* Utilidades comunes de ContaBox (JavaScript ES6, sin dependencias). */
"use strict";

const ContaBox = {
  /** Formatea un número como pesos chilenos: 12990 -> "$12.990". */
  pesos(valor) {
    return "$" + Math.round(Number(valor) || 0).toLocaleString("es-CL");
  },

  /** Token CSRF para peticiones fetch. */
  csrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  },

  /** GET JSON con manejo de errores. Devuelve {ok, status, data}. */
  async obtenerJSON(url) {
    try {
      const respuesta = await fetch(url, {headers: {Accept: "application/json"}, credentials: "same-origin"});
      const data = await respuesta.json().catch(() => ({}));
      return {ok: respuesta.ok, status: respuesta.status, data};
    } catch (e) {
      return {ok: false, status: 0, data: {error: "Sin conexión con el servidor."}};
    }
  },
};

/* Campos de catálogo: muestra "perecible" en alimentos y "garantía" en categorías que la admiten. */
function prepararCatalogo(contenedor) {
  const select = contenedor.querySelector(".js-categoria");
  if (!select) return;
  const bloquePerecible = contenedor.querySelector(".js-perecible");
  const bloqueGarantia = contenedor.querySelector(".js-garantia");
  const actualizar = (inicial) => {
    const op = select.selectedOptions[0];
    const alimento = op && op.dataset.alimento === "1";
    const garantia = op && op.dataset.garantia === "1";
    bloquePerecible.classList.toggle("d-none", !alimento);
    bloquePerecible.querySelectorAll("input").forEach((i) => { i.required = alimento && !contenedor.closest(".d-none"); });
    bloqueGarantia.classList.toggle("d-none", !garantia);
    if (garantia && !inicial) bloqueGarantia.querySelector("select").value = op.dataset.meses || "0";
    document.dispatchEvent(new Event("contabox:perecible"));
  };
  select.addEventListener("change", () => actualizar(false));
  contenedor.querySelectorAll('[name="es_perecible"]').forEach((r) => r.addEventListener("change", () => document.dispatchEvent(new Event("contabox:perecible"))));
  actualizar(true);
}
document.querySelectorAll(".js-catalogo").forEach(prepararCatalogo);

/* Formularios de bodega: al escanear un SKU se muestra el producto y su stock. */
document.querySelectorAll(".js-form-sku").forEach((form) => {
  const input = form.querySelector(".js-sku");
  const info = form.querySelector(".js-sku-info");
  if (!input || !info) return;
  const soloNuevo = form.querySelector(".js-solo-nuevo");
  let perecibleExistente = false;

  // Fecha de vencimiento obligatoria para productos perecibles.
  const vencimiento = form.querySelector('[name="fecha_vencimiento"]');
  const ayudaVenc = form.querySelector(".js-venc-ayuda");
  const revisarVencimiento = () => {
    if (!vencimiento) return;
    const marcado = form.querySelector('[name="es_perecible"]:checked');
    const nuevoPerecible = soloNuevo && !soloNuevo.classList.contains("d-none") && marcado && marcado.value === "1"
      && !form.querySelector(".js-perecible").classList.contains("d-none");
    const obligatorio = perecibleExistente || Boolean(nuevoPerecible);
    vencimiento.required = obligatorio;
    if (ayudaVenc) ayudaVenc.textContent = obligatorio ? "(obligatoria: producto perecible)" : "(opcional)";
  };
  document.addEventListener("contabox:perecible", revisarVencimiento);

  const consultar = async () => {
    const sku = input.value.trim();
    if (!sku) return;
    const {ok, data} = await ContaBox.obtenerJSON(`/bodega/api/producto?sku=${encodeURIComponent(sku)}`);
    if (!ok) { info.textContent = data.error || "No se pudo consultar el código."; return; }
    if (!data.encontrado) {
      perecibleExistente = false;
      if (soloNuevo) {
        soloNuevo.classList.remove("d-none");
        soloNuevo.querySelectorAll('[name="nombre"], [name="categoria_id"], [name="marca"]').forEach((i) => { i.required = true; });
      }
      info.innerHTML = form.dataset.modo === "lote"
        ? '<span class="text-primary">SKU nuevo: completa nombre, categoría y marca.</span>'
        : '<span class="text-danger">No existe un producto con ese código.</span>';
      revisarVencimiento();
      return;
    }
    const p = data.producto;
    perecibleExistente = p.es_perecible === 1;
    if (soloNuevo) {
      soloNuevo.classList.add("d-none");
      soloNuevo.querySelectorAll("input, select").forEach((i) => { i.required = false; });
    }
    const caja = p.unidades_por_bulto > 1 ? ` · cajas de ${p.unidades_por_bulto}` : "";
    info.innerHTML = `<strong>${p.nombre}</strong> (${p.marca}, ${p.categoria}) · bodega ${p.stock_bodega} · tienda ${p.stock_tienda}` +
      ` · precio ${ContaBox.pesos(p.precio_venta)}${caja}${p.es_perecible === 1 ? " · <span class='text-danger'>perecible</span>" : ""}`;
    const precio = form.querySelector('[name="precio_venta"]');
    if (precio && !precio.value) precio.value = Math.round(Number(p.precio_venta));
    const porBulto = form.querySelector('[name="unidades_por_bulto"]');
    if (porBulto) { porBulto.value = p.unidades_por_bulto; porBulto.dispatchEvent(new Event("input")); }
    revisarVencimiento();
  };

  input.addEventListener("change", consultar);
  // Los lectores de código de barras envían "Enter" al final: se consulta y se pasa al siguiente campo.
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      consultar();
      const siguiente = form.querySelector('input[type="number"]');
      if (siguiente) siguiente.focus();
    }
  });
  if (input.value) consultar();
});

/* Recepción de lotes: stock total = cajas x unidades por caja, y costo unitario en vivo. */
document.querySelectorAll(".js-form-lote").forEach((form) => {
  const bultos = form.querySelector('[name="cantidad_bultos"]');
  const porBulto = form.querySelector('[name="unidades_por_bulto"]');
  const costo = form.querySelector('[name="costo"]');
  const total = form.querySelector(".js-total-unidades");
  const calc = form.querySelector(".js-costo-calc");
  const numero = (t) => Number(String(t).replace(/\$|\s/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".")) || 0;
  const actualizar = () => {
    const b = parseInt(bultos.value, 10) || 0, u = parseInt(porBulto.value, 10) || 0, unidades = b * u;
    total.innerHTML = `Stock total del lote: <strong>${b} × ${u} = ${unidades.toLocaleString("es-CL")} unidades</strong>`;
    const modo = form.querySelector('[name="modo_costo"]:checked').value;
    const valor = numero(costo.value);
    if (!unidades || !costo.value) { calc.textContent = ""; return; }
    const inversion = modo === "TOTAL" ? valor : modo === "BULTO" ? valor * b : valor * unidades;
    calc.textContent = `Inversión del lote: ${ContaBox.pesos(inversion)} · costo por unidad: $${(inversion / unidades).toLocaleString("es-CL", {maximumFractionDigits: 2})}`;
  };
  [bultos, porBulto, costo].forEach((i) => i.addEventListener("input", actualizar));
  form.querySelectorAll('[name="modo_costo"]').forEach((r) => r.addEventListener("change", actualizar));
  actualizar();
});

/* Al imprimir se usa el tema claro para ahorrar tinta; luego se vuelve al oscuro. */
window.addEventListener("beforeprint", () => document.documentElement.setAttribute("data-bs-theme", "light"));
window.addEventListener("afterprint", () => document.documentElement.setAttribute("data-bs-theme", "dark"));
