"""Configuración de Gunicorn para producción (Ubuntu Server 22.04).

Uso: gunicorn -c deploy/gunicorn.conf.py run:app
"""
import multiprocessing

bind = "127.0.0.1:8000"          # Nginx actúa como proxy inverso delante
workers = multiprocessing.cpu_count() * 2 + 1
threads = 2
worker_class = "gthread"
timeout = 30                      # holgura sobre el SLA de 1,5 s por venta
graceful_timeout = 20
keepalive = 5
max_requests = 2000               # recicla workers para evitar fugas de memoria
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
loglevel = "info"
