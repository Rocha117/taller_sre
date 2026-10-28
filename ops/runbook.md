# Runbook: clientes asignados al país equivocado

> Un runbook es lo que haría cualquier persona del equipo si esto vuelve a
> pasar a las 3 a. m. Escríbanlo para alguien que no estuvo hoy.

## Cómo me doy cuenta

- **Señales:**
  - El job `validation` del *DataOps Pipeline* falla con `❌ Catálogo de países inconsistente` (check `country_catalog`) y `gold` no corre.
  - O Marketing avisa que una campaña de un país le llegó a clientes de otro, aunque el total de contactables no cambió.
- **Qué reviso primero** (SQL Editor de Databricks):
  1. El resultado del check:
     ```sql
     SELECT run_id, checked_ts, status, value, details
     FROM gameday.ops_dq_results
     WHERE check_name = 'country_catalog'
     ORDER BY checked_ts DESC;
     ```
  2. Clientes por código y nombre de país. Cada código debe aparecer **una sola vez** y con su nombre correcto:
     ```sql
     SELECT country_code, country_name, region, COUNT(*) AS clientes
     FROM gameday.silver_customers_v2 GROUP BY ALL ORDER BY 1;
     ```
  3. El catálogo. Cada código debe tener un nombre distinto:
     ```sql
     SELECT * FROM gameday.ref_paises ORDER BY country_code;
     ```
  4. Qué cambió en el repo: `git log --oneline -- data/ref/paises.csv` y `git show <commit>`.

## Contener (que no empeore)

- **No hacer push** a `data/**` ni a `framework/**`, y **no correr a mano** el *DataOps Pipeline*: cualquiera de las dos cosas vuelve a publicar Gold con el catálogo malo.
- Si hay riesgo de que alguien haga push, deshabilitar el workflow: **Actions → DataOps Pipeline → ⋯ → Disable workflow** (rehabilitarlo antes del merge del arreglo).
- **Retener los lotes nuevos** en `data/incoming/`; no moverlos a `data/source/` hasta corregir el catálogo.
- Avisar a Marketing que **no use** `gameday.gold_contactables_por_pais` para segmentar hasta nuevo aviso.

## Recuperar

1. **Código:** revertir el commit que cambió el catálogo, en una rama y por PR (no borrar historia):
   ```bash
   git checkout -b fix/revertir-catalogo
   git revert --no-edit <commit_culpable>      # el de hoy fue 3915bc8
   ```
   Si el commit es un merge, usar `git revert -m 1 <merge>`. Revisar que `data/ref/paises.csv` quede con cada código en su país.
2. **Datos:** volver Silver a la última versión buena, **antes** del merge del PR:
   ```sql
   DESCRIBE HISTORY gameday.silver_customers_v2;   -- buscar la última versión antes de la corrida con el catálogo malo
   RESTORE TABLE gameday.silver_customers_v2 TO VERSION AS OF <version_buena>;  -- hoy fue la 1
   ```
   Alternativa si solo hay unos pocos registros afectados: corregir con un `UPDATE ... SET country_name, region ... WHERE country_code = '<código>'`. Preferimos `RESTORE` + reproceso porque pasa los datos por el pipeline normal.
   `ref_paises` y `gold_contactables_por_pais` no hace falta restaurarlas: el pipeline las reescribe completas en cada corrida.
3. **Reproceso:** hacer merge del PR a `main`; eso dispara el *DataOps Pipeline*. Silver carga con `MERGE` por `customer_id` (solo inserta clientes que no existen), así que reprocesar **no duplica**. Gold se reescribe con `INSERT OVERWRITE`.
4. **Pendientes:** en el mismo PR (o en uno siguiente), mover los lotes retenidos:
   ```bash
   git mv data/incoming/<lote>.csv data/source/<lote>.csv
   ```

## Cómo confirmo que volvimos a un estado bueno

- El *DataOps Pipeline* termina en verde, incluido `validation` (check `country_catalog` en `PASS`).
- **Game day – Verificar** en verde (Recuperar, Sin duplicar y Contener ✅).
- En Silver, cada código aparece una sola vez con su nombre; hoy debía quedar: **26 clientes** (PE 7, CL 11, AR 4, CO 4).
- Sin duplicados (debe devolver 0 filas):
  ```sql
  SELECT customer_id, COUNT(*) FROM gameday.silver_customers_v2 GROUP BY customer_id HAVING COUNT(*) > 1;
  ```
- Sin clientes sin país (debe devolver 0 filas):
  ```sql
  SELECT customer_id, country_code FROM gameday.silver_customers_v2 WHERE country_name IS NULL;
  ```

## A quién aviso

| Momento | A quién | Qué le digo |
| --- | --- | --- |
| Al detectar | Marketing (Valeria) y el equipo de datos | "Confirmamos un error en el país de algunos clientes en `gold_contactables_por_pais`. No usen esa tabla para segmentar hasta nuevo aviso. Les escribimos al recuperar." |
| Al recuperar | Marketing (Valeria) | "La tabla ya está corregida y con el lote del lunes cargado. Los clientes afectados fueron [N] de [país]; [M] recibieron la campaña equivocada. Pueden volver a usarla para la campaña de la tarde." |
| Después | Equipo de datos | Enlace al postmortem (`ops/postmortem.md`) y a las acciones preventivas con dueño y fecha. |
