"""
EmpleoEstado.cl — Orquestador de Scrapers
Ejecuta todos los scrapers según su frecuencia configurada.
Maneja errores individuales sin detener el proceso completo.

Uso:
    python run_scrapers.py                  # ejecutar los que corresponde
    python run_scrapers.py --todos          # forzar todos
    python run_scrapers.py --fuente 1       # solo una fuente específica
    python run_scrapers.py --dry-run        # sin escribir en BD
    python run_scrapers.py --listar         # mostrar estado de fuentes
"""

import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import config
from db.database import SessionLocal
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"{config.LOG_DIR}/orquestador.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("orquestador")

# ── Registro de scrapers disponibles ─────────────────────────────────────────
# Cada entrada: (fuente_id, nombre, módulo, función, frequency_tier).
# El tier se aplica a fuentes.frecuencia_hrs en cada corrida (ver
# ``aplicar_politica_frecuencia``) para que la política viva en código y no
# en la BD. Tiers: critical|high|medium|low|eventual|exploratory
# (ver scrapers/frequency_policy.py).
SCRAPERS = [
    (1, "Portal Empleos Públicos",   "scrapers.empleos_publicos", "ejecutar", "critical"),
    (2, "Alta Dirección Pública",    "scrapers.adp",              "ejecutar", "high"),
    (3, "Poder Judicial",            "scrapers.poder_judicial",   "ejecutar", "medium"),
    (4, "Banco Central",             "scrapers.banco_central",    "ejecutar", "medium"),
    (5, "Contraloría",               "scrapers.contraloria",      "ejecutar", "medium"),
    (6, "Fiscalía de Chile",         "scrapers.fiscalia",         "ejecutar", "medium"),
    (7, "Dirección del Trabajo",     "scrapers.dir_trabajo",      "ejecutar", "medium"),
    (8, "Gobiernos Regionales",      "scrapers.gobiernos_regionales", "ejecutar", "low"),
    (9, "Plataforma Trabajando.cl",  "scrapers.trabajando",       "ejecutar", "high"),
]


def aplicar_politica_frecuencia(db) -> None:
    """Sincroniza fuentes.frecuencia_hrs con el tier declarado en SCRAPERS.

    Idempotente: cada corrida re-aplica el tier configurado en código.
    Mantiene la fuente como única fuente de verdad sobre las frecuencias.
    """
    try:
        from scrapers.frequency_policy import FrequencyTier, hours_for_tier
    except ImportError:
        return
    for entry in SCRAPERS:
        if len(entry) < 5:
            continue
        fuente_id, _nombre, _mod, _fn, tier_name = entry
        try:
            tier = FrequencyTier(tier_name)
        except ValueError:
            continue
        try:
            db.execute(
                text("UPDATE fuentes SET frecuencia_hrs = :hrs WHERE id = :id"),
                {"hrs": hours_for_tier(tier), "id": fuente_id},
            )
        except Exception:
            db.rollback()
            return
    db.commit()


def debe_ejecutar(db, fuente_id: int, forzar: bool = False) -> bool:
    """
    Determina si una fuente debe ejecutarse ahora
    según su última ejecución y frecuencia configurada.
    """
    if forzar:
        return True

    row = db.execute(text("""
        SELECT ultima_exitosa, frecuencia_hrs, activa
        FROM fuentes WHERE id = :id
    """), {"id": fuente_id}).fetchone()

    if not row or not row.activa:
        return False

    if not row.ultima_exitosa:
        return True  # nunca se ejecutó

    proxima = row.ultima_exitosa + timedelta(hours=row.frecuencia_hrs)
    return datetime.now(proxima.tzinfo) >= proxima


def listar_estado(db):
    """Muestra el estado actual de todas las fuentes."""
    rows = db.execute(text("""
        SELECT
            f.id, f.nombre, f.sector, f.frecuencia_hrs, f.activa,
            f.ultima_exitosa, f.total_ofertas,
            l.estado AS ultimo_estado, l.ofertas_nuevas AS ultimas_nuevas
        FROM fuentes f
        LEFT JOIN LATERAL (
            SELECT estado, ofertas_nuevas
            FROM logs_scraping
            WHERE fuente_id = f.id
            ORDER BY iniciado_en DESC LIMIT 1
        ) l ON TRUE
        ORDER BY f.id
    """)).fetchall()

    print("\n" + "="*70)
    print(f"  {'ID':>3}  {'Fuente':<35} {'Sector':<12} {'Hrs':>4} {'Total':>6}  {'Último':<8}")
    print("="*70)
    for r in rows:
        estado = r.ultimo_estado or "—"
        color  = "✓" if estado == "OK" else "✗" if estado == "ERROR" else "·"
        print(f"  {r.id:>3}  {r.nombre[:34]:<35} {(r.sector or '')[:11]:<12} "
              f"{r.frecuencia_hrs:>4} {(r.total_ofertas or 0):>6}  {color} {estado:<8}")
    print("="*70 + "\n")


def ejecutar_scraper(fuente_id: int, modulo: str, funcion: str, dry_run: bool = False, *_extra) -> bool:
    """Importa y ejecuta un scraper dinámicamente."""
    try:
        import importlib
        mod = importlib.import_module(modulo)
        fn  = getattr(mod, funcion)
        fn(dry_run=dry_run)
        return True
    except ModuleNotFoundError:
        logger.warning(f"  Módulo {modulo} no implementado aún. Saltando.")
        return False
    except Exception as e:
        logger.error(f"  Error ejecutando {modulo}: {e}")
        return False


def main():
    import os
    os.makedirs(config.LOG_DIR, exist_ok=True)

    parser = argparse.ArgumentParser(description="Orquestador de scrapers EmpleoEstado.cl")
    parser.add_argument("--todos",   action="store_true", help="Forzar todos los scrapers")
    parser.add_argument("--fuente",  type=int, default=None, help="Ejecutar solo fuente con este ID")
    parser.add_argument("--dry-run", action="store_true", help="Sin escribir en BD")
    parser.add_argument("--listar",  action="store_true", help="Mostrar estado de fuentes y salir")
    args = parser.parse_args()

    db = SessionLocal()

    # Sincroniza fuentes.frecuencia_hrs con la política declarada en SCRAPERS.
    # Idempotente; sin esto, una fuente nunca obtiene su tier por defecto.
    if not args.dry_run:
        aplicar_politica_frecuencia(db)

    if args.listar:
        listar_estado(db)
        db.close()
        return

    logger.info("=" * 60)
    logger.info(f"ORQUESTADOR — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  dry_run={args.dry_run} | forzar_todos={args.todos} | fuente={args.fuente}")
    logger.info("=" * 60)

    ejecutados = 0
    exitosos   = 0

    for entry in SCRAPERS:
        # Soporta entradas con y sin tier (5 vs 4 elementos) por compatibilidad.
        fuente_id, nombre, modulo, funcion, *_rest = entry
        # Filtrar por fuente específica si se indicó
        if args.fuente and fuente_id != args.fuente:
            continue

        # Verificar si debe ejecutarse
        if not debe_ejecutar(db, fuente_id, forzar=args.todos or bool(args.fuente)):
            logger.info(f"  [{fuente_id}] {nombre}: No corresponde aún. Saltando.")
            continue

        logger.info(f"\n  [{fuente_id}] INICIANDO: {nombre}")
        inicio = time.time()
        ok = ejecutar_scraper(fuente_id, modulo, funcion, dry_run=args.dry_run)

        ejecutados += 1
        if ok:
            exitosos += 1
            logger.info(f"  [{fuente_id}] ✓ Completado en {time.time()-inicio:.1f}s")
        else:
            logger.warning(f"  [{fuente_id}] ✗ Falló: {nombre}")

        # Pausa entre scrapers para no sobrecargar la red
        if len(SCRAPERS) > 1:
            time.sleep(2)

    db.close()
    logger.info(f"\n  RESUMEN: {exitosos}/{ejecutados} scrapers completados exitosamente")

    # ── Validación post-scraping de URLs de ofertas activas ──────────────
    # Mantiene url_oferta_valida / url_bases_valida al día para que el
    # frontend pueda gatear el botón "Ver bases" / "Postular".
    if not args.dry_run and ejecutados > 0:
        try:
            from validate_offer_urls import main as validar_urls
            logger.info("\n  Validando URLs de ofertas activas…")
            t0 = time.time()
            res = validar_urls(limit=None, workers=20, max_edad_h=24)
            logger.info(f"  Validación URLs: {res} ({time.time()-t0:.1f}s)")
        except Exception as exc:
            logger.warning(f"  Validador de URLs falló (no bloqueante): {exc}")


if __name__ == "__main__":
    main()
