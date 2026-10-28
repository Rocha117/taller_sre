# Postmortem: clientes de Chile segmentados como Perú

> Sin culpables: buscamos qué falló en el proceso, no quién se equivocó.

**Equipo y roles:** Comandante: [ ... ] · Operador: [ ... ] · Comunicador: [ ... ]

## Resumen

El viernes entró a `main` un cambio en el catálogo de países (`data/ref/paises.csv`) que dejó el código `CL` con el nombre "Perú" y la región "Andina". Los 4 clientes chilenos del lote del viernes quedaron clasificados como peruanos en Silver y en Gold, y el lunes la campaña de Perú les llegó a 3 de ellos. Revertimos el cambio, restauramos Silver a la versión del jueves y reprocesamos los lotes del viernes y del lunes con el catálogo correcto: producción volvió a un estado bueno en 7,8 minutos.

## Impacto

- **Clientes:** 4 clientes de Chile del lote del viernes (IDs 13, 14, 16 y 19) quedaron como Perú / Andina. 3 de ellos son contactables (el 19 no tiene email), así que recibieron la promoción de Perú, que no vale en Chile.
- **Campañas:** en `gold_contactables_por_pais`, Perú tenía 10 clientes en vez de 6 y Chile 4 en vez de 8. La campaña de Chile salió sin esos 3 contactables.
- **Por qué no se vio:** el total de clientes no cambió (los clientes solo se movieron de país), así que el dashboard de Marketing mostraba el mismo número.
- **Tablas afectadas:** `gameday.ref_paises` (catálogo), `gameday.silver_customers_v2` (desde la versión 2) y `gameday.gold_contactables_por_pais`.
- **Proceso retenido:** el lote del lunes (6 clientes) esperó en `data/incoming/` hasta que el catálogo estuvo corregido.
- **Costo extra:** una corrida extra del pipeline y una de *Verificar*, más el tiempo del equipo durante el incidente.

## Línea de tiempo

Horas en hora de Perú (UTC-5). Las del viernes y la alerta son las del escenario; las del game day son las reales.

| Hora | Qué pasó |
| --- | --- |
| Vie 23/10 17:48 | Commit `3915bc8` "normaliza catálogo de países a ISO 3166": al ordenar el catálogo, `CL` queda como `Perú / Andina` |
| Vie 23/10 18:05 | Merge `9ad2866` directo a `main`, sin revisión; el pipeline corre y publica Gold con el catálogo malo |
| Lun 9:05 | Alerta de Marketing: clientes de Chile recibieron la campaña de Perú |
| Mar 27/10 21:41 | Lanzamos *Game day – Preparar producción* (run 40622412902) |
| Mar 27/10 21:43 | Corrida "jueves": MERGE en Silver = versión 1 (estado bueno, 12 clientes) |
| Mar 27/10 21:45 | Corrida "viernes": MERGE en Silver = versión 2 (inserta 8 clientes; 4 de CL quedan como Perú) |
| Mar 27/10 21:47 | **Inicio del incidente**: termina *Preparar producción* (job `inicio-incidente`) |
| Mar 27/10 21:49 | **Detectamos la causa**: en Silver, `CL` aparece 4 veces como Chile (jueves) y 4 como Perú (viernes); el culpable es `3915bc8` |
| Mar 27/10 21:49 | **Contenemos**: no se carga el lote del lunes ni se corre el pipeline hasta revertir el catálogo; se avisa a Marketing que no use `gold_contactables_por_pais` |
| Mar 27/10 21:49 | Con `DESCRIBE HISTORY` identificamos que Silver está mal desde la versión 2; la versión 1 es la buena |
| Mar 27/10 21:50 | **Recuperamos datos**: `RESTORE TABLE gameday.silver_customers_v2 TO VERSION AS OF 1` (vuelve al jueves; queda 1 archivo, 4602 bytes) |
| Mar 27/10 21:51 | **Recuperamos código**: merge del PR #1 (revert de `3915bc8` + lote del lunes) a `main`, commit `1bda3c6`; dispara el pipeline (run 40623378367) |
| Mar 27/10 21:54 | Pipeline en verde: Silver con 26 clientes (jueves + viernes + lunes) y Gold reconstruido con el catálogo correcto |
| Mar 27/10 21:55 | **Primer *Verificar* en verde** (run 40623665011): 55/55 técnico — Recuperar ✅, Sin duplicar ✅, Contener ✅ |

**MTTR:** 7,8 minutos (21:47 inicio del incidente → 21:55 primer verificar en verde)

## Causa raíz

- **El cambio:** el commit `3915bc8` ("normaliza catálogo de países a ISO 3166", mergeado en `9ad2866`) agregó la columna `iso3` y ordenó el catálogo por código. Al reordenar, la columna de códigos se movió pero los nombres y regiones no quedaron alineados: la fila de `CL` quedó como `CL,CHL,Perú,Andina`. Desde entonces el catálogo tenía dos códigos con el nombre "Perú".
- **Por qué llegó a producción:**
  - El cambio se mergeó directo a `main`, sin PR ni revisión. Parecía cosmético ("chore", solo ordenar y agregar una columna) y fue generado con ayuda de un asistente de IA cuya salida nadie revisó fila por fila.
  - `load_ref.py` reemplaza el catálogo completo en cada corrida sin validar su contenido.
- **Por qué los checks no lo frenaron:**
  - `validate_silver.py` solo revisaba nombres nulos y menores de edad; no revisaba el país.
  - Además, la validación solo escribía resultados en `ops_dq_results` y nunca hacía fallar el job, así que Gold se publicaba igual.
  - Silver usa un `MERGE` que solo inserta clientes nuevos: los clientes del jueves conservaron su país correcto y los totales no cambiaron, lo que hizo el error invisible en el dashboard.

## Qué funcionó y qué no

**Funcionó:**
- El historial de versiones de Delta (`DESCRIBE HISTORY`) nos dijo exactamente desde qué versión estaba mal Silver (v2) y cuál era la buena (v1).
- `git revert` deshizo el cambio sin borrar historia: el error y su corrección quedan trazables.
- El `MERGE` por `customer_id` hizo que el reproceso fuera idempotente: 26 clientes, ninguno duplicado.
- Contener primero: retener el lote del lunes evitó publicar más datos malos (Contener 15/15).
- Elegimos `RESTORE` + reproceso en lugar de un `UPDATE` puntual porque vuelve a pasar los datos por el pipeline normal con el catálogo correcto y corrige cualquier columna derivada del catálogo, no solo las que vimos.

**No funcionó:**
- La detección la hizo un cliente, no un check: pasaron 3 días entre el cambio y la alerta.
- No había revisión obligatoria para cambios en datos de referencia.
- La validación no era bloqueante.
- Bronze recarga todos los CSV en cada corrida y los agrega de nuevo (crece con duplicados); Silver lo absorbe, pero es ruido y costo.

## Acción preventiva

- **Check nuevo en `framework/validation/validate_silver.py`** (`country_catalog`), que **bloquea Gold** si falla:
  - `nombres_repetidos`: un mismo nombre de país asignado a más de un código en `ref_paises`.
  - `codigo_varios_nombres`: un código de país con más de un nombre o región en Silver.
  - `sin_pais`: clientes cuyo código no está en el catálogo.
  - Guarda el resultado en `ops_dq_results` y, si hay fallas, termina con error: como `gold` depende de `validation`, Gold no se publica.
- **¿Lo probamos? Sí.** Probado localmente con los CSV del repo:
  - Catálogo corregido con los 3 lotes → pasa.
  - Estado del viernes (jueves bien + viernes con catálogo malo) → falla (`nombres_repetidos=1`, `codigo_varios_nombres=1`).
  - Reaplicar el cambio del viernes en una carga desde cero → falla (`nombres_repetidos=1`).
- **Otras acciones:**

  | Acción | Dueño | Fecha |
  | --- | --- | --- |
  | Proteger `main`: todo cambio por PR con al menos una revisión, en especial `data/ref/` | Comandante | 03/11/2026 |
  | Validar el catálogo en `load_ref.py` antes de reemplazar la tabla (códigos y nombres únicos) | Operador | 10/11/2026 |
  | Alerta por cambio brusco de clientes por país en Gold (no solo del total) | Operador | 10/11/2026 |
  | Revisar fila por fila los cambios en datos de referencia generados con IA | Todo el equipo | Desde hoy |
