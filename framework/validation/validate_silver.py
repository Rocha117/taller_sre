import os
from databricks import sql
from datetime import datetime, timezone

HOST = os.environ["DATABRICKS_HOST"]
HTTP_PATH = os.environ["DATABRICKS_HTTP_PATH"]
TOKEN = os.environ["DATABRICKS_TOKEN"]

RUN_ID = os.environ.get("RUN_ID", "manual")
SILVER_TABLE = os.environ.get("SILVER_TABLE", "gameday.silver_customers_v2")
REF_TABLE = os.environ.get("REF_TABLE", "gameday.ref_paises")

def fetch_one(cur, stmt: str):
    cur.execute(stmt)
    return cur.fetchall()[0][0]

def country_checks(cur):
    """Check del incidente del viernes: el catálogo de países debe ser consistente.

    - nombres_repetidos: un mismo nombre de país asignado a más de un código
      (el viernes, CL y PE quedaron los dos como "Perú").
    - codigo_varios_nombres: en Silver, un código con más de un nombre o región.
    - sin_pais: clientes de Silver cuyo código no está en el catálogo.
    """
    nombres_repetidos = fetch_one(cur, f"""
        SELECT COUNT(*) FROM (
            SELECT country_name FROM {REF_TABLE}
            GROUP BY country_name
            HAVING COUNT(DISTINCT country_code) > 1
        ) AS t
    """)
    codigo_varios_nombres = fetch_one(cur, f"""
        SELECT COUNT(*) FROM (
            SELECT country_code FROM {SILVER_TABLE}
            GROUP BY country_code
            HAVING COUNT(DISTINCT country_name) > 1 OR COUNT(DISTINCT region) > 1
        ) AS t
    """)
    sin_pais = fetch_one(cur, f"SELECT COUNT(*) FROM {SILVER_TABLE} WHERE country_name IS NULL")
    return nombres_repetidos, codigo_varios_nombres, sin_pais

def main():
    checked_at = datetime.now(timezone.utc).isoformat()
    print(f"✅ Validating table: {SILVER_TABLE}")
    print(f"✅ RUN_ID: {RUN_ID}")

    with sql.connect(server_hostname=HOST, http_path=HTTP_PATH, access_token=TOKEN) as conn:
        with conn.cursor() as cur:
            total = fetch_one(cur, f"SELECT COUNT(*) FROM {SILVER_TABLE}")
            name_null = fetch_one(cur, f"SELECT COUNT(*) FROM {SILVER_TABLE} WHERE dq_is_name_null = true")
            underage = fetch_one(cur, f"SELECT COUNT(*) FROM {SILVER_TABLE} WHERE dq_is_underage = true")

            # Guarda resultados en ops_dq_results
            cur.execute(
                "INSERT INTO gameday.ops_dq_results VALUES (?, current_timestamp(), ?, 'total_rows', 'INFO', ?, ?)",
                (RUN_ID, SILVER_TABLE, float(total), f"checked_at={checked_at}")
            )

            cur.execute(
                "INSERT INTO gameday.ops_dq_results VALUES (?, current_timestamp(), ?, 'name_null_count', ?, ?, ?)",
                (RUN_ID, SILVER_TABLE, "FAIL" if name_null > 0 else "PASS", float(name_null), "name is null/empty")
            )

            cur.execute(
                "INSERT INTO gameday.ops_dq_results VALUES (?, current_timestamp(), ?, 'underage_count', ?, ?, ?)",
                (RUN_ID, SILVER_TABLE, "WARN" if underage > 0 else "PASS", float(underage), "age < 18")
            )

            # Check bloqueante: catálogo de países consistente
            nombres_repetidos, codigo_varios_nombres, sin_pais = country_checks(cur)
            fallas_pais = nombres_repetidos + codigo_varios_nombres + sin_pais
            cur.execute(
                "INSERT INTO gameday.ops_dq_results VALUES (?, current_timestamp(), ?, 'country_catalog', ?, ?, ?)",
                (RUN_ID, SILVER_TABLE, "FAIL" if fallas_pais > 0 else "PASS", float(fallas_pais),
                 f"nombres_repetidos={nombres_repetidos}; codigo_varios_nombres={codigo_varios_nombres}; sin_pais={sin_pais}")
            )

    print(f"✅ Validation completed: total={total}, name_null={name_null}, underage={underage}")

    # Si el catálogo está mal, el job falla y Gold no se publica (gold depende de validation)
    if fallas_pais > 0:
        raise SystemExit(
            f"❌ Catálogo de países inconsistente: nombres_repetidos={nombres_repetidos}, "
            f"codigo_varios_nombres={codigo_varios_nombres}, sin_pais={sin_pais}. Se bloquea Gold."
        )
    print("✅ Country catalog check passed")

if __name__ == "__main__":
    main()
