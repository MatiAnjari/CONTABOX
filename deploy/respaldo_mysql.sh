#!/usr/bin/env bash
# Respaldo de la base cada 15 minutos (RPO <= 15 min). Programar con cron:
#   */15 * * * * /opt/contabox/deploy/respaldo_mysql.sh >> /var/log/contabox-respaldo.log 2>&1
set -euo pipefail
source /opt/contabox/.env
DESTINO=/var/backups/contabox
mkdir -p "$DESTINO"
ARCHIVO="$DESTINO/contabox_$(date +%Y%m%d_%H%M).sql.gz"
mysqldump --single-transaction --routines --triggers -h "$MYSQL_HOST" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" | gzip > "$ARCHIVO"
# Copia los comprobantes de pago
tar czf "$DESTINO/comprobantes_$(date +%Y%m%d).tar.gz" -C /opt/contabox archivos_privados 2>/dev/null || true
# Conserva 7 días
find "$DESTINO" -type f -mtime +7 -delete
echo "$(date '+%F %T') respaldo OK: $ARCHIVO"
