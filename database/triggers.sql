-- =====================================================================
-- ContaBox - Disparadores de inmutabilidad (RN06 y RN05)
-- Cada bloque se separa con la línea  $$  (lo procesa init_db.py).
-- =====================================================================

DROP TRIGGER IF EXISTS trg_ventas_no_editar
$$
CREATE TRIGGER trg_ventas_no_editar BEFORE UPDATE ON ventas FOR EACH ROW
BEGIN
    -- Solo se permite pasar de CONFIRMADA a ANULADA (vía nota de crédito).
    IF NOT (OLD.estado = 'CONFIRMADA' AND NEW.estado = 'ANULADA'
            AND NEW.monto_total = OLD.monto_total AND NEW.costo_total = OLD.costo_total
            AND NEW.subtotal = OLD.subtotal AND NEW.descuento = OLD.descuento
            AND NEW.monto_neto = OLD.monto_neto AND NEW.monto_iva = OLD.monto_iva
            AND NEW.tipo_documento = OLD.tipo_documento AND NEW.folio = OLD.folio
            AND NEW.trabajador_id = OLD.trabajador_id
            AND NEW.cliente_rut <=> OLD.cliente_rut AND NEW.cliente_razon_social <=> OLD.cliente_razon_social
            AND NEW.medio_pago = OLD.medio_pago AND NEW.fecha_hora = OLD.fecha_hora
            AND NEW.empresa_id = OLD.empresa_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RN06: una venta confirmada es inmutable';
    END IF;
END
$$
DROP TRIGGER IF EXISTS trg_ventas_no_borrar
$$
CREATE TRIGGER trg_ventas_no_borrar BEFORE DELETE ON ventas FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RN06: no se permite borrar ventas'
$$
DROP TRIGGER IF EXISTS trg_detalle_no_editar
$$
CREATE TRIGGER trg_detalle_no_editar BEFORE UPDATE ON detalle_ventas FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RN06: el detalle de una venta es inmutable'
$$
DROP TRIGGER IF EXISTS trg_detalle_no_borrar
$$
CREATE TRIGGER trg_detalle_no_borrar BEFORE DELETE ON detalle_ventas FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RN06: el detalle de una venta es inmutable'
$$
DROP TRIGGER IF EXISTS trg_auditoria_no_editar
$$
CREATE TRIGGER trg_auditoria_no_editar BEFORE UPDATE ON auditoria FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RN05: la auditoría no se puede modificar'
$$
DROP TRIGGER IF EXISTS trg_auditoria_no_borrar
$$
CREATE TRIGGER trg_auditoria_no_borrar BEFORE DELETE ON auditoria FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RN05: la auditoría no se puede borrar'
$$
