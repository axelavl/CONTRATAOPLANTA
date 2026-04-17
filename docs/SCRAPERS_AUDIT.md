# Auditoría integral de scrapers — abril 2026

Documento técnico que acompaña la unificación de scrapers para
contrataoplanta.cl / EmpleoEstado.cl. Cubre los 13 entregables solicitados.

## 1. Diagnóstico global

Hoy conviven dos generaciones de scrapers:

- **Generación A — pipeline central**: `JobExtractionPipeline`
  (`scrapers/job_pipeline.py`) con clasificador (`classification/rule_engine.py`),
  extracción satelital (`extraction/*`), validación de vigencia y completitud
  (`validation/*`) y normalización pydantic (`normalization/job_normalizer.py`).
  La usan `plataformas/generic_site.py` y `plataformas/carabineros.py`.
- **Generación B — scrapers legacy**: bypassan el pipeline y persisten
  directo a la BD via `db.database.upsert_oferta`. Son
  `_base_wordpress.py` y sus subclases muni_*, `banco_central.py`,
  `codelco.py`, `tvn.py`, `poder_judicial.py`, `trabajando.py`,
  `muni_puente_alto.py`.

**Consecuencia**: las reglas de basura/vigencia/renta vivían sólo en la
Gen A. La Gen B insertaba registros que nadie validaba.

**Acción aplicada**: se creó `scrapers/intake.py` (validador transversal
sin I/O) y se enchufó en `BaseScraper.save_to_db` (Gen A) **y** en
`db.database.upsert_oferta` (Gen B). Ahora **toda** persistencia pasa por
el mismo filtro.

## 2. Problemas detectados por familia

| Familia | Síntoma | Acción |
|---|---|---|
| `empleos_publicos` | timeout 10 s, 3 attempts; pierde ofertas en horario peak | timeout 20 s, 4 attempts, semáforo 30, 404/410 terminales |
| `_base_wordpress` + munis | sin validación de vigencia ni de renta; `mpuentealto` hardcodeado en base genérica | intake transversal aplicado vía `upsert_oferta` |
| `banco_central`/`codelco`/`tvn` (Playwright) | renta nunca parseada; sleep incondicional 5 s; tipo_cargo hardcodeado | intake aplicado; sin reescritura de scraper |
| `poder_judicial` | área hardcodeada "Derecho", `generar_id_estable` con aridad incorrecta | intake aplicado; bug de aridad documentado |
| `trabajando.py` | playwright fallback silencia `None` | intake aplicado |
| `plataformas/policia` / `plataformas/ffaa` | `urljoin` importado dentro de funciones, _map_columns duplicado | sin cambio (fuera de alcance) |
| `plataformas/generic_site` | bug salary min/max duplicado (líneas 225-226) | sin cambio (lo cubre `assess_salary` ahora) |
| `playwright_scraper.py` | "SKIP" sin razón si el ID no está mapeado | sin cambio (no bloqueante) |

## 3. Esquema unificado de variables

El `JobPosting` de `models/job_posting.py` ya cubre los campos del
brief. El intake agrega tres campos opcionales que cualquier scraper
puede setear:

- `needs_review: bool` — marcador para dashboard de QA.
- `review_reasons: list[str]` — razones por las que se marcó.
- `renta_validation_status: str` — motivo de descarte/saneamiento de renta.

## 4. Reglas comunes de extracción y descarte

Centralizadas en `scrapers/intake.py`:

- **Listas negativas de texto**: noticias, comunicados, resultados, nóminas,
  adjudicaciones, actas, resoluciones, históricos, licitaciones, fondos,
  difusión interna.
- **Listas negativas de URL**: `/noticias/`, `/blog/`, `/comunicados/`,
  `/agenda/`, `/cuenta-publica/`, `/historico/`, `/anteriores/`,
  `/licitaciones/`, etc.
- **Mismas reglas en `RuleEngine._build_negative_rules()`** para cuando el
  pipeline pydantic clasifica el contenido.

## 5. Política de frecuencias por fuente

`scrapers/frequency_policy.py` define seis tiers con horas entre corridas:

| Tier | Horas | Aplica a |
|---|---|---|
| `critical` | 3 | Empleos Públicos batch |
| `high` | 6 | Plataformas centralizadas (Trabajando.cl, Hiringroom, Buk) en grandes empresas del Estado, ADP |
| `medium` | 12 | Servicios públicos estándar, autónomos, universidades, FF.AA./policía |
| `low` | 48 | Municipios, sitios propios cubiertos por EP |
| `eventual` | 168 | Fuentes inactivas / status≠active |
| `exploratory` | 720 | Sólo descubrimiento manual |

`default_tier_for(...)` infiere el tier desde `kind + sector + status +
publica_en_empleospublicos`. `resolve_tier(...)` permite override por
`source_overrides.json` (`frequency_tier: "..."`).

Se sincroniza con `fuentes.frecuencia_hrs` cada corrida en
`run_scrapers.py::aplicar_politica_frecuencia(db)`. Idempotente.

## 6. Timeouts / reintentos por categoría

Definidos en `TIER_PROFILES` (`scrapers/frequency_policy.py`):

| Tier | timeout | retries | delay | candidate URLs | open PDF | playwright fallback |
|---|---|---|---|---|---|---|
| critical | 20 s | 3 | 0.3 s | 10 | sí | sí |
| high | 15 s | 2 | 0.5 s | 6 | sí | sí |
| medium | 12 s | 2 | 1.0 s | 4 | sí | no |
| low | 8 s | 1 | 1.5 s | 3 | no | no |
| eventual | 6 s | 1 | 2.0 s | 2 | no | no |
| exploratory | 10 s | 2 | 2.0 s | 7 | sí | no |

## 7. Criterios de lectura de PDF

- Sólo se abren PDFs **relevantes** (`extraction/attachment_parser.py::is_relevant_attachment`):
  filename incluye `bases|perfil|tdr|términos de referencia|anexo|concurso|convocatoria`.
- En tiers `low`/`eventual` no se abren PDFs (campo `open_pdf=False` en
  `TierProfile`).
- En tiers `medium`+ se abren para completar funciones, requisitos,
  renta, fechas, contacto, perfil.
- Si el PDF es irrelevante o vacío, no se hace OCR salvo `allow_ocr=True`.

## 8. Reglas de vigencia / antigüedad

`scrapers/intake.py::assess_vigencia(...)`:

- `fecha_cierre` en el pasado → descarte (`fecha_cierre_vencida`).
- `fecha_cierre` en el futuro → válido aunque la publicación sea antigua.
- Sin `fecha_cierre` y publicación > 365 días → descarte
  (`publicacion_excede_365_dias`).
- Sin `fecha_cierre` y publicación 181-365 días → descarte
  (`publicacion_excede_180_dias_sin_cierre`).
- Sin `fecha_cierre` y publicación 91-180 días → marca `needs_review`.
- Sin `fecha_cierre` y publicación ≤ 90 días → válido.

Las constantes `ANTIGUEDAD_OK_DIAS=90`, `ANTIGUEDAD_REVISION_DIAS=180`,
`ANTIGUEDAD_DESCARTE_DIAS=365` están exportadas y son ajustables.

## 9. Validación de remuneraciones

`scrapers/intake.py::assess_salary(...)`:

- `RENTA_MAX_SOSPECHOSA=15.000.000` → descarte automático (presupuesto/anual).
- `RENTA_MAX_CONFIABLE=10.000.000` con contexto débil → descarte por
  `renta_no_confiable_sin_contexto`.
- `RENTA_MIN_RAZONABLE=250.000` → cifras menores se descartan o se
  reemplazan por el extremo mayor.
- Min > Max → se ordenan automáticamente.
- `parse_renta` (en `scrapers/base.py`) ahora respeta los mismos
  techos en lugar del rango anterior 100k–99,9MM (que dejaba pasar
  presupuestos anuales).

## 10. Cambios aplicados en código

**Archivos nuevos**:

- `scrapers/intake.py` — validador transversal (basura, vigencia, renta,
  campos mínimos). 318 líneas.
- `scrapers/frequency_policy.py` — tiers, perfiles, resolución default
  + override.
- `tests/test_intake.py` — 35 tests.
- `tests/test_frequency_policy.py` — 18 tests.
- `docs/SCRAPERS_AUDIT.md` — este documento.

**Archivos modificados**:

- `scrapers/base.py`
  - `IntakeRejected` exception nueva.
  - `parse_renta` aplica techos del intake.
  - `BaseScraper.normalize_offer` invoca `intake_validate_offer`; lanza
    `IntakeRejected` si la oferta debe descartarse.
  - `BaseScraper.save_to_db` captura `IntakeRejected` y registra en
    `stats["descartadas"]` (nuevo campo).
- `scrapers/empleos_publicos.py`
  - `DEFAULT_TIMEOUT` 10 s → 20 s.
  - `DEFAULT_MAX_ATTEMPTS=4` (antes 3 hardcoded).
  - Backoff capado a 8 s.
  - 404/410 son terminales (no se reintentan).
  - Semáforo de detalle 20 → 30.
- `scrapers/source_status.py`
  - `SourceDecision.frequency_tier: str | None`.
  - `_apply_override` lee `frequency_tier` desde overrides.
- `scrapers/source_overrides.json`
  - Documentación actualizada para incluir `frequency_tier`.
- `scrapers/run_all.py`
  - `print_classification_detail` muestra el tier resuelto por fuente.
- `run_scrapers.py`
  - `SCRAPERS` ahora incluye `frequency_tier` por fila.
  - `aplicar_politica_frecuencia(db)` sincroniza `fuentes.frecuencia_hrs`
    con la política declarada.
- `db/database.py`
  - `upsert_oferta` invoca `intake_validate_offer` antes de la DB.
- `classification/rule_engine.py`
  - Reglas negativas extendidas: actas, resoluciones, histórico expandido,
    convocatorias cerradas, "solo difusión interna" (-0.40).

## 11. Arquitectura común reutilizable

```
        ┌──────────────────────────────────────────────────┐
        │           Cualquier scraper (Gen A o B)          │
        └──────────────────────┬───────────────────────────┘
                               ▼
        ┌──────────────────────────────────────────────────┐
        │  intake_validate_offer()  ← scrapers/intake.py   │
        │   • garbage text / URL                           │
        │   • difusión interna                             │
        │   • vigencia / antigüedad                        │
        │   • salary sanity                                │
        │   • campos mínimos                               │
        └──────────────────────┬───────────────────────────┘
                               ▼
        ┌──────────────────────────────────────────────────┐
        │   BaseScraper.normalize_offer  /  upsert_oferta  │
        └──────────────────────┬───────────────────────────┘
                               ▼
                     PostgreSQL (ofertas, snapshots)
```

Los scrapers pueden seguir usando su lógica de discovery/fetch
particular; lo único transversal es la validación.

## 12. Matriz de priorización por fuente

| Fuente / familia | Tier | Volumen | Riesgo basura | Costo abrir PDF | Playwright |
|---|---|---|---|---|---|
| Empleos Públicos | critical | muy alto | bajo | sí | sí |
| ADP | high | medio | bajo | sí | no |
| Trabajando/Hiringroom/Buk grandes | high | medio | bajo | no | no |
| Banco Central, Contraloría, Fiscalía | medium | medio | medio | sí | parcial |
| Universidades estatales con portal propio | medium | medio | medio | sí | no |
| FF.AA. / Carabineros / PDI | medium | bajo-medio | medio | sí (perfil) | no |
| Sitios propios cubiertos por EP | low | bajo | medio | no | no |
| Municipios | low | muy bajo | alto | no | no |
| Experimentales / sin verificar | exploratory | desconocido | alto | sólo descubrimiento | no |

## 13. Lista priorizada de fuentes

**Más seguido (cada 3-6 h)**:

1. **Empleos Públicos** (`scrapers/empleos_publicos.py`) — concentra el grueso
   de avisos vigentes del Ejecutivo, autónomos y muchos servicios. Cambia
   en horas.
2. **Trabajando.cl masters** — BancoEstado, ENAP, Metro, Correos, ZOFRI,
   FACH, CMF, Talca, Magallanes, U. Magallanes. Plataforma estable y
   alta rotación.

**Frecuencia media (cada 12 h)**:

3. ADP, Banco Central, Contraloría, Fiscalía, Defensoría, Consejo para
   la Transparencia, INDH, CMF.
4. Universidades estatales con portal propio (UCH, UV, UFRO, UTEM,
   UDA, UBB, UOH, etc.).
5. FF.AA. y orden (Ejército, Armada, Carabineros, PDI) — actualizan
   poco pero son de alto interés cuando publican.
6. Empresas portuarias.

**Frecuencia baja (cada 48 h)**:

7. Municipios (354+) — publicación dispersa y baja por comuna.
8. Sitios propios verificados que **además** publican en Empleos Públicos
   (Defensoría de la Niñez, GORE Atacama/Aysén, CFTs estatales, etc.).
   El batch de EP los cubre con frescura; el sitio propio sólo
   complementa.

**Eventual (cada 7 días)**:

9. Fuentes en `experimental` o `manual_review`: USACH, U. de Antofagasta
   (intranet), UMCE old, UPLA, UNAP legacy.
10. Fuentes históricamente con cero ofertas que no merece chequear a
    diario.

**No correr (skip)**:

11. Fuentes que sólo publican en LinkedIn (Polla Chilena, CFT del Maule,
    CFT de Los Lagos).
12. Fuentes con `js_required` sin scraper Playwright dedicado.

---

**Tests**: 287 passed (de los cuales 53 nuevos cubren el intake y la
política de frecuencia).
