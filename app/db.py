"""Capa de persistencia: conexión a MySQL con PyMySQL y consultas parametrizadas.

Todas las funciones usan placeholders (%s), nunca concatenación de texto,
para prevenir inyección SQL (OWASP A03).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import pymysql
from flask import current_app, g
from pymysql.cursors import DictCursor

Fila = dict[str, Any]


def conectar() -> pymysql.connections.Connection:
    """Abre una conexión nueva usando la configuración de la app."""
    cfg = current_app.config
    return pymysql.connect(
        host=cfg["MYSQL_HOST"],
        port=cfg["MYSQL_PORT"],
        user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DATABASE"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
    )


def get_db() -> pymysql.connections.Connection:
    """Devuelve la conexión de la petición actual (una por petición)."""
    if "db" not in g:
        g.db = conectar()
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        try:
            db.rollback()  # descarta cualquier transacción que haya quedado abierta
        finally:
            db.close()


def query(sql: str, params: Sequence[Any] | None = None) -> list[Fila]:
    """Ejecuta un SELECT y devuelve todas las filas."""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        return list(cur.fetchall())


def query_one(sql: str, params: Sequence[Any] | None = None) -> Fila | None:
    """Ejecuta un SELECT y devuelve la primera fila o None."""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


@contextmanager
def transaccion() -> Iterator[DictCursor]:
    """Bloque ACID: START TRANSACTION ... COMMIT, con ROLLBACK automático ante cualquier error.

    Uso:
        with transaccion() as cur:
            cur.execute(...)
    """
    db = get_db()
    db.rollback()  # garantiza que la transacción parte limpia
    cur = db.cursor()
    try:
        cur.execute("START TRANSACTION")
        yield cur
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        cur.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
