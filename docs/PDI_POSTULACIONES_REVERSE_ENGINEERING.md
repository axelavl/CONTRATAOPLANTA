# Ingeniería inversa — `https://postulaciones.investigaciones.cl/`

Fecha de análisis: **2026-04-17 (UTC)**.

## Evidencia verificada en este análisis

1. La raíz del portal responde como SPA y no trae contenido útil en HTML estático: el render visible es **"You need to enable JavaScript to run this app."** (capturado vía herramienta web).  
2. Existe `manifest.json` con nombre corto/largo **Reclutamiento**, consistente con una app frontend empaquetada.  
3. Desde este entorno, el acceso de red por `curl` está restringido por proxy (CONNECT 403 en `envoy`) y no permite inspección completa de tráfico HTTP directo del dominio (`research/pdi/03_curl_verbose.log`, `research/pdi/08_verbose_direct.log`).

## Diagnóstico técnico del portal (fase 1)

- **HTML inicial**: no contiene vacantes ni detalle.
- **Carga de datos**: altamente probable por JS (SPA React/webpack).
- **Fuente de datos**: no visible en HTML inicial; requiere inspección de red del frontend (XHR/fetch o endpoint interno).
- **Paginación/filtros**: no verificable sin capturar requests runtime.
- **Autenticación/sesión**: el instructivo público menciona flujo con inicio de sesión; por diseño es probable sesión/cookies para funciones de postulación.
- **Anti-bot**: no confirmado aún para el portal final; sí existe bloqueo de conectividad desde este entorno vía proxy.

## Fuente de verdad (fase 2): orden de decisión

1. **Endpoint JSON directo** descubierto por trazas de red.
2. **API interna reproducible** (GET/POST) con cookies/headers mínimos.
3. **XHR/fetch de frontend** (incluyendo posible GraphQL).
4. **HTML SSR** (descartado en raíz por evidencia).
5. **Headless browser end-to-end** sólo si no hay API reusable.

## Causas probables de fallas previas (fase 3, priorizadas)

1. Se parseó `GET /` esperando vacantes, pero la raíz no trae datos.
2. No se capturó la llamada secundaria que alimenta el listado.
3. Se omitieron cookies/tokens de sesión si el endpoint los exige.
4. Se replicó `GET` cuando la lista se resuelve por `POST` con body JSON.
5. No se reprodujo paginación/filtros exactos del frontend.
6. No se hizo segunda llamada al detalle por `id` de oferta.

## Estrategia exacta de scraping (fase 4)

### Paso A — Recon de red (obligatorio y único punto de verdad)

Ejecutar:

```bash
python -m scrapers.plataformas.pdi_postulaciones_recon --out research/pdi/recon_run
```

Salida esperada:

- `research/pdi/recon_run/network_events.jsonl`
- `research/pdi/recon_run/candidates.json`
- `research/pdi/recon_run/page.html`
- `research/pdi/recon_run/inline_json_signals.json`

### Paso B — Selección de endpoint canónico

Elegir sólo requests que cumplan:

- Responden JSON real con lista de vacantes (`items`, `results`, `data`, etc.).
- Son reproducibles fuera del navegador con `requests` (misma cookie/header necesarios).
- Permiten paginar (offset/page/limit/cursor) sin interacción visual.

### Paso C — Fórmula de extracción

1. **Bootstrap de sesión** (si aplica): `GET /` para cookies base.
2. **Listado vigente**: llamar endpoint canónico con filtros por vigencia.
3. **Paginación**: iterar hasta condición terminal (`next is null`, `len < page_size`, o total alcanzado).
4. **Detalle**: por cada oferta, disparar endpoint de detalle por ID/slug.
5. **Estado**: derivar `vigente/cerrada` por campo explícito o por fecha de cierre.
6. **Deduplicación**: `unique_key = source + job_id` (fallback SHA1(url_detalle + titulo + fecha_cierre)).
7. **Resiliencia**: retry selectivo (429/5xx), backoff exponencial, timeout por etapa.
8. **Evidencia**: guardar request/response mínima ante cambios (`.json` de muestra + hash de esquema).

## Extracción de campos (fase 5)

### Alta confianza (si el JSON los trae)

- `id_oferta`, `titulo`, `fecha_publicacion`, `fecha_cierre`, `estado`, `url_detalle`, `url_postulacion`.

### Media confianza (según estructura del detalle)

- `region`, `comuna`, `tipo_contrato`, `jornada`, `remuneracion`, `unidad`.

### Baja confianza (normalmente en texto libre)

- `funciones`, `requisitos`, `formacion`, `experiencia`, `competencias`, `documentos`.

Regla: primero usar campos estructurados; sólo después usar parser semántico por encabezados en texto.

## Normalización (fase 6)

1. `strip` + colapso de espacios/saltos.
2. Preservar párrafos y listas (`•`, `-`, numeraciones).
3. Segmentar por subtítulos (`Requisitos`, `Experiencia`, `Competencias`, etc.).
4. Limpiar HTML residual sin perder contenido.
5. Mantener `raw_text` original para auditoría.

## Implementación modular (fase 7)

- `pdi_client.py`: sesión, retries, timeouts, headers.
- `pdi_list_parser.py`: parse listado + paginación.
- `pdi_detail_parser.py`: parse detalle + campos.
- `pdi_normalizer.py`: limpieza y segmentación.
- `pdi_validator.py`: calidad mínima + cobertura.
- `pdi_runner.py`: orquestación + logging estructurado.

## Criterios de decisión (fase 8)

- **Usar `requests`** cuando el endpoint del listado/detalle sea reproducible.
- **Usar Playwright** sólo para descubrir endpoints o cuando exista challenge/session binding imposible de replicar.
- **Interceptar network** antes de intentar parsear DOM dinámico.
- **Evitar DOM scraping** si existe JSON interno estable.

## Validaciones de funcionamiento (fase 9)

1. Conteo de vacantes scrapeadas vs conteo visible en UI (misma fecha).
2. Muestreo manual de 10 ofertas: título/fechas/estado deben coincidir.
3. Cobertura de detalle > 95% de ofertas listadas.
4. Sin duplicados por corrida.
5. Alerta si cambia esquema JSON (hash de claves).

## Operación estable (fase 10)

- Correr diario + reintento nocturno.
- Rotar User-Agent realista y mantener sesión por corrida.
- Observabilidad: métricas de `offers_found`, `details_ok`, `details_fail`, `http_429`, `http_5xx`.
- Guardar muestras de payload para regresión.
- Fallback temporal: si cae endpoint canónico, activar modo browser sólo para no perder cobertura.

## Limitación actual de este entorno

La conectividad de este contenedor bloquea inspección HTTP completa del dominio mediante proxy (403 CONNECT), por lo que el paso definitivo de identificación del endpoint exacto debe ejecutarse en un entorno con salida web normal o con navegador interactivo con devtools. Ver `research/pdi/03_curl_verbose.log` y `research/pdi/08_verbose_direct.log`.
