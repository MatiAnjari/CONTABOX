"""Crea la base de datos de ContaBox, sus tablas y disparadores en MySQL.

Uso:
    python init_db.py           # crea lo que falte (no borra datos)
    python init_db.py --reset   # BORRA la base completa y la crea vacía
"""
from __future__ import annotations

import os
import re
import sys

import pymysql
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("MYSQL_DATABASE", "contabox")
CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database")


def _leer(nombre: str) -> str:
    with open(os.path.join(CARPETA, nombre), encoding="utf-8") as archivo:
        return archivo.read()


def main() -> None:
    """Conecta al servidor MySQL y ejecuta schema.sql y triggers.sql."""
    try:
        conexion = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            charset="utf8mb4",
        )
    except pymysql.err.OperationalError as error:
        print(f"No se pudo conectar a MySQL: {error.args[1]}")
        print("Revisa MYSQL_USER y MYSQL_PASSWORD en el archivo .env y que MySQL esté encendido.")
        sys.exit(1)

    with conexion.cursor() as cur:
        if "--reset" in sys.argv:
            cur.execute(f"DROP DATABASE IF EXISTS `{BASE}`")
            print(f"Base '{BASE}' eliminada.")
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{BASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute(f"USE `{BASE}`")

        esquema = re.sub(r"--[^\n]*", "", _leer("schema.sql"))
        for sentencia in esquema.split(";"):
            if sentencia.strip():
                cur.execute(sentencia)

        try:
            disparadores = re.sub(r"(?m)^--[^\n]*", "", _leer("triggers.sql"))
            for bloque in disparadores.split("$$"):
                if bloque.strip():
                    cur.execute(bloque)
        except pymysql.err.MySQLError as error:
            print(f"Aviso: no se pudieron crear los disparadores de inmutabilidad ({error.args[-1]}).")
            print("La aplicación igual impide editar ventas; para activarlos usa un usuario con privilegio TRIGGER.")

    conexion.commit()
    conexion.close()
    print(f"Listo: base '{BASE}' creada con todas las tablas.")


if __name__ == "__main__":
    main()
