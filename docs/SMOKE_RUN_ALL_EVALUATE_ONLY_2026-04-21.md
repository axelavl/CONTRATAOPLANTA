# Smoke real — `run_all --evaluate-only` con subset `--ids` (2026-04-21)

## Objetivo
Ejecutar una batería smoke del gatekeeper con `--evaluate-only` sobre un subconjunto de instituciones y contrastar `publish-rate` contra baseline por fuente.

## Comando ejecutado

```bash
DB_PASSWORD=dummy python scrapers/run_all.py --mode development --evaluate-only --ids 157,161,162,315,387,562 --force-evaluate
```

## Resultado operativo del entorno

- **BD no disponible**: conexión rechazada a `localhost:5432`.
- **Sin persistencia de evaluaciones**: no fue posible escribir en `source_evaluations` ni `scraper_runs`.
- **Red saliente parcial/bloqueada** para algunos dominios evaluados: errores `Network is unreachable`.

## Resumen de la corrida

Gatekeeper reportó:

- `extract`: **3**
- `source_status_only`: **3**

Interpretación para smoke:

- **publish-rate observado (proxy)** = `extract / total` = `3/6` = **50.0%**.

> Nota: en modo `--evaluate-only`, usamos `decision=extract` como proxy de publicable a nivel fuente.

## Detalle por fuente (subset)

| ID | Institución | Resultado gatekeeper | Observación |
|---:|---|---|---|
| 157 | Ejército de Chile — Personal Civil | extract | bypass `custom_ffaa` |
| 161 | Carabineros de Chile — Personal Civil | extract | bypass `custom_policia` |
| 162 | Policía de Investigaciones — Personal Civil | extract | bypass `custom_policia` |
| 315 | Municipalidad de Copiapó | source_status_only | `reason=empty_response` tras errores de red |
| 387 | Municipalidad de Independencia | source_status_only | `reason=empty_response` tras errores de red |
| 562 | Municipalidad de Pucón | source_status_only | `reason=empty_response` tras errores de red |

## Comparación publish-rate vs baseline

### Baseline por fuente

No se pudo calcular baseline histórico por fuente porque la BD no estuvo disponible durante la sesión:

- No fue posible leer `source_evaluations` (historial).
- No fue posible persistir la corrida para usarla como punto incremental.

### Estado de comparación

- **Comparación vs baseline: pendiente por bloqueo de entorno (BD)**.
- Con la corrida actual, solo se pudo obtener el **publish-rate observado del smoke (50.0%)** sobre el subset.

## Recomendación para rerun válido (con comparación completa)

1. Exportar variables reales de DB (`DATABASE_URL` o `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`).
2. Confirmar conectividad saliente a dominios de fuentes (`copiapo.cl`, `independencia.cl`, `municipalidadpucon.cl`).
3. Repetir el comando anterior (idealmente sin `DB_PASSWORD=dummy`).
4. Consultar baseline por fuente en `source_evaluations` (ventana histórica) y calcular delta vs la corrida nueva.
