# Despliegue en producción (Ubuntu Server 22.04 LTS · AWS / Azure)

Arquitectura: **Nginx** (TLS 1.3, proxy inverso) → **Gunicorn** (WSGI) → **Flask** → **MySQL 8.0 InnoDB**.

## 1. Servidor

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv mysql-server nginx certbot python3-certbot-nginx
sudo adduser --system --group --home /opt/contabox contabox
```

## 2. Base de datos

```bash
sudo mysql_secure_installation
sudo mysql -e "CREATE USER 'contabox'@'localhost' IDENTIFIED BY 'UNA_CLAVE_LARGA';
               GRANT ALL PRIVILEGES ON contabox.* TO 'contabox'@'localhost';
               GRANT TRIGGER ON contabox.* TO 'contabox'@'localhost'; FLUSH PRIVILEGES;"
# Si el log binario está activo, para crear los disparadores con un usuario sin SUPER:
sudo mysql -e "SET GLOBAL log_bin_trust_function_creators = 1;"
```

## 3. Aplicación

```bash
sudo -u contabox -H bash
cd /opt/contabox            # copia aquí el proyecto (git clone o scp)
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # completa los datos y además:
#   APP_DEBUG=0
#   SESSION_COOKIE_SECURE=1
#   SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
python init_db.py
exit
```

## 4. Servicios

```bash
sudo cp /opt/contabox/deploy/contabox.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now contabox

sudo cp /opt/contabox/deploy/nginx-contabox.conf /etc/nginx/sites-available/contabox
sudo ln -s /etc/nginx/sites-available/contabox /etc/nginx/sites-enabled/
sudo certbot --nginx -d contabox.tudominio.cl
sudo nginx -t && sudo systemctl reload nginx
```

## 5. Respaldos (RPO ≤ 15 min)

```bash
sudo crontab -e
*/15 * * * * /opt/contabox/deploy/respaldo_mysql.sh >> /var/log/contabox-respaldo.log 2>&1
```

Copia `/var/backups/contabox` a almacenamiento externo (S3 / Azure Blob) para cumplir el RTO ≤ 2 h.
Restaurar: `gunzip < contabox_AAAAMMDD_HHMM.sql.gz | mysql -u contabox -p contabox`.

## 6. Operación y SLA

| Objetivo | Cómo se apoya |
|---|---|
| Uptime ≥ 99,5 % (L-S 08:00-20:00) | systemd reinicia Gunicorn ante fallas (`Restart=always`); monitorea `https://…/login` con un servicio externo. |
| UI ≤ 2,0 s | Estáticos servidos por Nginx con caché y gzip; consultas indexadas por `empresa_id` y fecha. |
| Venta ≤ 1,5 s | Transacción única con bloqueo por fila (`SELECT … FOR UPDATE`); timeout de Gunicorn de 30 s. |
| RPO ≤ 15 min | `respaldo_mysql.sh` cada 15 minutos. |
| RTO ≤ 2 h / MTTR ≤ 1 h | Restauración documentada arriba; logs en `journalctl -u contabox`. |
