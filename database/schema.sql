-- =====================================================================
-- ContaBox - Esquema relacional MySQL 8.0 (InnoDB, 3FN)
-- Plataforma multiempresa: toda tabla operativa lleva empresa_id (RN01).
-- Ejecutar con:  python init_db.py
-- =====================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------
-- Catálogo de roles (única tabla sin empresa_id)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    id           TINYINT UNSIGNED PRIMARY KEY,
    codigo       VARCHAR(20)  NOT NULL,
    nombre_rol   VARCHAR(50)  NOT NULL,
    descripcion  VARCHAR(255) NOT NULL,
    UNIQUE KEY uq_roles_codigo (codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO roles (id, codigo, nombre_rol, descripcion) VALUES
    (1, 'ADMIN',     'Administrador', 'Configura la PYME, administra trabajadores, ve KPIs, auditoría y reportes'),
    (2, 'BODEGUERO', 'Bodeguero',     'Recepción de lotes, transferencias a tienda, existencias y mermas'),
    (3, 'VENDEDOR',  'Vendedor',      'Punto de venta, verificación de stock y carga de comprobantes');

-- ---------------------------------------------------------------------
-- Empresas (PYMEs). Una por cada administrador registrado.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS empresas (
    id                    INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    rut                   VARCHAR(12)  NOT NULL,
    razon_social          VARCHAR(150) NOT NULL,
    nombre_fantasia       VARCHAR(120) NOT NULL,             -- nombre visible para los trabajadores
    direccion             VARCHAR(200) NULL,
    telefono              VARCHAR(30)  NULL,
    logo_url              VARCHAR(255) NULL,                 -- ruta relativa dentro de /static
    fecha_registro        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prueba_hasta          DATETIME     NOT NULL,             -- fin del mes gratis
    suscripcion_hasta     DATETIME     NULL,                 -- fin del período pagado
    ultimo_numero_boleta  INT UNSIGNED NOT NULL DEFAULT 0,   -- correlativo de boletas
    ultimo_numero_factura INT UNSIGNED NOT NULL DEFAULT 0,   -- correlativo de facturas
    ultimo_numero_nc      INT UNSIGNED NOT NULL DEFAULT 0,   -- correlativo de notas de crédito
    UNIQUE KEY uq_empresas_rut (rut)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Usuarios: credenciales de acceso. Nunca se eliminan: se dan de BAJA.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usuarios (
    id                     INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id             INT UNSIGNED NOT NULL,
    rol_id                 TINYINT UNSIGNED NOT NULL,
    email                  VARCHAR(150) NULL,
    password_hash          VARCHAR(100) NOT NULL,           -- bcrypt
    estado                 ENUM('PENDIENTE','ACTIVO','RECHAZADO','BAJA') NOT NULL DEFAULT 'PENDIENTE',
    debe_cambiar_password  TINYINT(1)   NOT NULL DEFAULT 0, -- credencial temporal
    creado_en              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    aprobado_en            DATETIME     NULL,
    baja_en                DATETIME     NULL,
    ultimo_ingreso         DATETIME     NULL,
    UNIQUE KEY uq_usuarios_email (email),
    KEY ix_usuarios_empresa_estado (empresa_id, estado),
    CONSTRAINT fk_usuarios_empresa FOREIGN KEY (empresa_id) REFERENCES empresas(id),
    CONSTRAINT fk_usuarios_rol     FOREIGN KEY (rol_id)     REFERENCES roles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Trabajadores: ficha personal asociada a cada usuario.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trabajadores (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id  INT UNSIGNED NOT NULL,
    usuario_id  INT UNSIGNED NOT NULL,
    rut         VARCHAR(12)  NOT NULL,
    nombre      VARCHAR(80)  NOT NULL,
    apellido    VARCHAR(80)  NOT NULL,
    cargo       VARCHAR(60)  NOT NULL,
    telefono    VARCHAR(30)  NULL,
    UNIQUE KEY uq_trabajadores_usuario (usuario_id),
    UNIQUE KEY uq_trabajadores_rut (rut),
    KEY ix_trabajadores_empresa (empresa_id),
    CONSTRAINT fk_trabajadores_empresa FOREIGN KEY (empresa_id) REFERENCES empresas(id),
    CONSTRAINT fk_trabajadores_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Catálogo maestro: categorías estilo supermercado y marcas
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categorias (
    id                      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id              INT UNSIGNED NOT NULL,
    nombre                  VARCHAR(100) NOT NULL,
    es_alimento             TINYINT(1)   NOT NULL DEFAULT 0,   -- exige definir perecible / no perecible
    admite_garantia         TINYINT(1)   NOT NULL DEFAULT 0,   -- tecnología, electrónica, muebles...
    garantia_meses_defecto  INT          NOT NULL DEFAULT 0,
    activo                  TINYINT(1)   NOT NULL DEFAULT 1,
    UNIQUE KEY uq_categorias_empresa_nombre (empresa_id, nombre),
    CONSTRAINT ck_categorias_garantia CHECK (garantia_meses_defecto BETWEEN 0 AND 120),
    CONSTRAINT fk_categorias_empresa FOREIGN KEY (empresa_id) REFERENCES empresas(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marcas (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id  INT UNSIGNED NOT NULL,
    nombre      VARCHAR(100) NOT NULL,
    UNIQUE KEY uq_marcas_empresa_nombre (empresa_id, nombre),
    CONSTRAINT fk_marcas_empresa FOREIGN KEY (empresa_id) REFERENCES empresas(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Productos e inventario (stock en bodega central y en salón de venta)
-- Un mismo artículo de distinta marca es otro producto (otro SKU y precio).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS productos (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id          INT UNSIGNED  NOT NULL,
    categoria_id        INT UNSIGNED  NOT NULL,
    marca_id            INT UNSIGNED  NOT NULL,
    codigo_sku          VARCHAR(50)   NOT NULL,
    nombre              VARCHAR(150)  NOT NULL,
    precio_venta        DECIMAL(12,2) NOT NULL,
    es_perecible        TINYINT(1)    NULL,              -- obligatorio (0/1) en categorías de alimentos
    meses_garantia      INT           NOT NULL DEFAULT 0,
    unidades_por_bulto  INT           NOT NULL DEFAULT 1, -- empaque habitual (para mostrar cajas)
    activo              TINYINT(1)    NOT NULL DEFAULT 1,
    creado_en           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_productos_empresa_sku (empresa_id, codigo_sku),
    KEY ix_productos_categoria (categoria_id),
    KEY ix_productos_marca (marca_id),
    CONSTRAINT ck_productos_precio   CHECK (precio_venta >= 0),
    CONSTRAINT ck_productos_garantia CHECK (meses_garantia BETWEEN 0 AND 120),
    CONSTRAINT ck_productos_bulto    CHECK (unidades_por_bulto >= 1),
    CONSTRAINT fk_productos_empresa   FOREIGN KEY (empresa_id)   REFERENCES empresas(id),
    CONSTRAINT fk_productos_categoria FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    CONSTRAINT fk_productos_marca     FOREIGN KEY (marca_id)     REFERENCES marcas(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inventario (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id     INT UNSIGNED NOT NULL,
    producto_id    INT UNSIGNED NOT NULL,
    stock_bodega   INT NOT NULL DEFAULT 0,
    stock_tienda   INT NOT NULL DEFAULT 0,
    stock_critico  INT NOT NULL DEFAULT 5,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_inventario_producto (producto_id),
    KEY ix_inventario_empresa (empresa_id),
    -- RN03: el motor rechaza cualquier stock negativo como última barrera
    CONSTRAINT ck_inventario_bodega  CHECK (stock_bodega >= 0),
    CONSTRAINT ck_inventario_tienda  CHECK (stock_tienda >= 0),
    CONSTRAINT ck_inventario_critico CHECK (stock_critico >= 0),
    CONSTRAINT fk_inventario_empresa  FOREIGN KEY (empresa_id)  REFERENCES empresas(id),
    CONSTRAINT fk_inventario_producto FOREIGN KEY (producto_id) REFERENCES productos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Lotes de adquisición, recibidos en bultos (cajas) de N unidades.
-- cantidad_disponible se consume por ventas y mermas: primero lo que vence
-- antes (FEFO) y luego lo más antiguo (FIFO), para el costo real de lo vendido.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lotes (
    id                   INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id           INT UNSIGNED  NOT NULL,
    producto_id          INT UNSIGNED  NOT NULL,
    usuario_id           INT UNSIGNED  NOT NULL,
    numero_lote          VARCHAR(50)   NOT NULL,
    cantidad_bultos      INT           NOT NULL,
    unidades_por_bulto   INT           NOT NULL,
    cantidad_ingresada   INT           NOT NULL,          -- bultos x unidades por bulto
    cantidad_disponible  INT           NOT NULL,
    inversion_total      DECIMAL(14,2) NOT NULL,          -- monto exacto pagado por el lote
    costo_unitario       DECIMAL(14,4) NOT NULL,          -- 4 decimales para no perder pesos al dividir
    fecha_vencimiento    DATE          NULL,
    fecha_ingreso        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_lotes_producto_numero (producto_id, numero_lote),
    KEY ix_lotes_fifo (producto_id, fecha_ingreso, id),
    KEY ix_lotes_empresa_fecha (empresa_id, fecha_ingreso),
    CONSTRAINT ck_lotes_ingresada  CHECK (cantidad_ingresada > 0),
    CONSTRAINT ck_lotes_bultos     CHECK (cantidad_bultos > 0 AND unidades_por_bulto > 0
                                          AND cantidad_ingresada = cantidad_bultos * unidades_por_bulto),
    CONSTRAINT ck_lotes_disponible CHECK (cantidad_disponible >= 0 AND cantidad_disponible <= cantidad_ingresada),
    CONSTRAINT ck_lotes_costo      CHECK (costo_unitario >= 0),
    CONSTRAINT fk_lotes_empresa  FOREIGN KEY (empresa_id)  REFERENCES empresas(id),
    CONSTRAINT fk_lotes_producto FOREIGN KEY (producto_id) REFERENCES productos(id),
    CONSTRAINT fk_lotes_usuario  FOREIGN KEY (usuario_id)  REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Movimientos de inventario: kardex completo de entradas y salidas.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id       INT UNSIGNED NOT NULL,
    producto_id      INT UNSIGNED NOT NULL,
    usuario_id       INT UNSIGNED NOT NULL,
    tipo_movimiento  ENUM('INGRESO_LOTE','TRANSFERENCIA','VENTA','MERMA','ANULACION_VENTA') NOT NULL,
    cantidad         INT NOT NULL,
    origen           ENUM('PROVEEDOR','BODEGA','TIENDA','CLIENTE') NOT NULL,
    destino          ENUM('BODEGA','TIENDA','CLIENTE','BAJA') NOT NULL,
    referencia_id    INT UNSIGNED NULL,          -- id del lote, venta o pérdida que lo originó
    fecha_hora       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_mov_empresa_fecha (empresa_id, fecha_hora),
    KEY ix_mov_producto (producto_id),
    KEY ix_mov_usuario (usuario_id),
    CONSTRAINT ck_mov_cantidad CHECK (cantidad > 0),
    CONSTRAINT fk_mov_empresa  FOREIGN KEY (empresa_id)  REFERENCES empresas(id),
    CONSTRAINT fk_mov_producto FOREIGN KEY (producto_id) REFERENCES productos(id),
    CONSTRAINT fk_mov_usuario  FOREIGN KEY (usuario_id)  REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Ventas (boletas), detalle y comprobantes de pago
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ventas (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id     INT UNSIGNED  NOT NULL,
    trabajador_id  INT UNSIGNED  NOT NULL,
    tipo_documento  ENUM('BOLETA','FACTURA') NOT NULL DEFAULT 'BOLETA',
    folio           INT UNSIGNED  NOT NULL,       -- correlativo por tipo de documento
    subtotal        DECIMAL(12,2) NOT NULL,       -- suma de productos (IVA incluido)
    descuento       DECIMAL(12,2) NOT NULL DEFAULT 0,
    monto_total     DECIMAL(12,2) NOT NULL,       -- subtotal - descuento (lo pagado)
    monto_neto      DECIMAL(12,2) NOT NULL,       -- total / 1,19
    monto_iva       DECIMAL(12,2) NOT NULL,       -- 19 %
    costo_total     DECIMAL(12,2) NOT NULL,       -- costo FIFO/FEFO de los lotes vendidos
    medio_pago      ENUM('EFECTIVO','DEBITO','CREDITO','TRANSFERENCIA') NOT NULL,
    cliente_rut           VARCHAR(12)  NULL,      -- datos del receptor (solo factura)
    cliente_razon_social  VARCHAR(150) NULL,
    cliente_giro          VARCHAR(100) NULL,
    cliente_direccion     VARCHAR(200) NULL,
    estado         ENUM('CONFIRMADA','ANULADA') NOT NULL DEFAULT 'CONFIRMADA',
    fecha_hora     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ventas_empresa_folio (empresa_id, tipo_documento, folio),
    KEY ix_ventas_empresa_fecha (empresa_id, fecha_hora),
    KEY ix_ventas_trabajador (trabajador_id),
    CONSTRAINT ck_ventas_monto CHECK (monto_total >= 0 AND descuento >= 0 AND descuento <= subtotal),
    CONSTRAINT fk_ventas_empresa    FOREIGN KEY (empresa_id)    REFERENCES empresas(id),
    CONSTRAINT fk_ventas_trabajador FOREIGN KEY (trabajador_id) REFERENCES trabajadores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS detalle_ventas (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    venta_id         INT UNSIGNED  NOT NULL,
    producto_id               INT UNSIGNED  NOT NULL,
    producto_nombre           VARCHAR(150)  NOT NULL,   -- copia fija para el comprobante
    marca_nombre              VARCHAR(100)  NOT NULL,
    cantidad                  INT           NOT NULL,
    precio_unitario           DECIMAL(12,2) NOT NULL,
    costo_unitario            DECIMAL(14,4) NOT NULL,
    subtotal                  DECIMAL(12,2) NOT NULL,
    meses_garantia_aplicados  INT           NOT NULL DEFAULT 0,
    fecha_fin_garantia        DATE          NULL,       -- fecha de venta + meses de garantía
    KEY ix_detalle_producto (producto_id),
    CONSTRAINT ck_detalle_cantidad CHECK (cantidad > 0),
    CONSTRAINT fk_detalle_venta    FOREIGN KEY (venta_id)    REFERENCES ventas(id),
    CONSTRAINT fk_detalle_producto FOREIGN KEY (producto_id) REFERENCES productos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS comprobantes (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    venta_id         INT UNSIGNED NOT NULL,
    usuario_id       INT UNSIGNED NOT NULL,
    archivo_url      VARCHAR(255) NOT NULL,
    nombre_original  VARCHAR(255) NULL,
    fecha_carga      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_comprobantes_venta (venta_id),
    CONSTRAINT fk_comprobantes_venta   FOREIGN KEY (venta_id)   REFERENCES ventas(id),
    CONSTRAINT fk_comprobantes_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Notas de crédito (RN06): única vía para anular una venta confirmada.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notas_credito (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id       INT UNSIGNED  NOT NULL,
    venta_id         INT UNSIGNED  NOT NULL,
    usuario_id       INT UNSIGNED  NOT NULL,     -- administrador que la autorizó
    numero           INT UNSIGNED  NOT NULL,
    motivo           VARCHAR(255)  NOT NULL,
    monto            DECIMAL(12,2) NOT NULL,
    costo_revertido  DECIMAL(12,2) NOT NULL,
    fecha_hora       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_nc_venta (venta_id),
    UNIQUE KEY uq_nc_empresa_numero (empresa_id, numero),
    CONSTRAINT fk_nc_empresa FOREIGN KEY (empresa_id) REFERENCES empresas(id),
    CONSTRAINT fk_nc_venta   FOREIGN KEY (venta_id)   REFERENCES ventas(id),
    CONSTRAINT fk_nc_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Pérdidas / mermas (RN04: motivo obligatorio y estandarizado)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perdidas (
    id           INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id   INT UNSIGNED  NOT NULL,
    producto_id  INT UNSIGNED  NOT NULL,
    usuario_id   INT UNSIGNED  NOT NULL,
    cantidad     INT           NOT NULL,
    motivo       ENUM('VENCIMIENTO','DETERIORO_FISICO','ROBO_HURTO','OTRO') NOT NULL,
    observacion  VARCHAR(255)  NULL,
    origen       ENUM('BODEGA','TIENDA') NOT NULL,
    costo_total  DECIMAL(12,2) NOT NULL,          -- inversión perdida (costo FIFO)
    fecha_hora   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_perdidas_empresa_fecha (empresa_id, fecha_hora),
    CONSTRAINT ck_perdidas_cantidad CHECK (cantidad > 0),
    CONSTRAINT fk_perdidas_empresa  FOREIGN KEY (empresa_id)  REFERENCES empresas(id),
    CONSTRAINT fk_perdidas_producto FOREIGN KEY (producto_id) REFERENCES productos(id),
    CONSTRAINT fk_perdidas_usuario  FOREIGN KEY (usuario_id)  REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Consumo de lotes: qué lote abasteció cada venta o merma (FIFO).
-- Permite revertir exactamente una venta anulada.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consumo_lotes (
    id                INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    lote_id           INT UNSIGNED  NOT NULL,
    detalle_venta_id  INT UNSIGNED  NULL,
    perdida_id        INT UNSIGNED  NULL,
    cantidad          INT           NOT NULL,
    costo_unitario    DECIMAL(14,4) NOT NULL,
    KEY ix_consumo_detalle (detalle_venta_id),
    KEY ix_consumo_perdida (perdida_id),
    CONSTRAINT ck_consumo_cantidad CHECK (cantidad > 0),
    CONSTRAINT fk_consumo_lote    FOREIGN KEY (lote_id)          REFERENCES lotes(id),
    CONSTRAINT fk_consumo_detalle FOREIGN KEY (detalle_venta_id) REFERENCES detalle_ventas(id),
    CONSTRAINT fk_consumo_perdida FOREIGN KEY (perdida_id)       REFERENCES perdidas(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Conteos físicos de inventario (base del KPI %PPI)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conteos_inventario (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id     INT UNSIGNED NOT NULL,
    producto_id    INT UNSIGNED NOT NULL,
    usuario_id     INT UNSIGNED NOT NULL,
    stock_sistema  INT NOT NULL,
    stock_fisico   INT NOT NULL,
    observacion    VARCHAR(255) NULL,
    fecha_hora     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_conteos_empresa_producto (empresa_id, producto_id, fecha_hora),
    CONSTRAINT ck_conteos_fisico CHECK (stock_fisico >= 0),
    CONSTRAINT fk_conteos_empresa  FOREIGN KEY (empresa_id)  REFERENCES empresas(id),
    CONSTRAINT fk_conteos_producto FOREIGN KEY (producto_id) REFERENCES productos(id),
    CONSTRAINT fk_conteos_usuario  FOREIGN KEY (usuario_id)  REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Auditoría (RN05): toda escritura deja usuario, entidad y fecha_hora.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auditoria (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    empresa_id  INT UNSIGNED NULL,
    usuario_id  INT UNSIGNED NULL,
    accion      ENUM('INSERT','UPDATE','DELETE','LOGIN','LOGOUT','LOGIN_FALLIDO') NOT NULL,
    entidad     VARCHAR(50)  NOT NULL,
    entidad_id  INT UNSIGNED NULL,
    detalle     VARCHAR(500) NULL,
    ip          VARCHAR(45)  NULL,
    fecha_hora  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_auditoria_empresa_fecha (empresa_id, fecha_hora),
    KEY ix_auditoria_usuario (usuario_id),
    CONSTRAINT fk_auditoria_empresa FOREIGN KEY (empresa_id) REFERENCES empresas(id),
    CONSTRAINT fk_auditoria_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
