"""
EmpleoEstado.cl — Scraper: Poder Judicial de Chile
URL: https://www.pjud.cl/transparencia/trabaje-con-nosotros

Publica concursos para jueces, notarios, conservadores y personal administrativo.
Los llamados se concentran en dos viernes de cada mes.

Uso:
    python scrapers/poder_judicial.py
    python scrapers/poder_judicial.py --dry-run
"""

import sys
import time
import logging
import argparse
import random
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import config
from db.database import (
    SessionLocal, upsert_oferta, marcar_ofertas_cerradas,
    registrar_log, normalizar_region, normalizar_area, limpiar_texto, generar_id_estable
)

LOG_DIR = Path(config.LOG_DIR)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "poder_judicial.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("scraper.poder_judicial")

FUENTE_ID = 3
BASE_URL  = "https://www.pjud.cl"
LIST_URL  = f"{BASE_URL}/transparencia/trabaje-con-nosotros"
INST_NOMBRE = "Poder Judicial de Chile"


def crear_sesion() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    retries = Retry(
        total=config.MAX_REINTENTOS,
        connect=config.MAX_REINTENTOS,
        read=config.MAX_REINTENTOS,
        status=config.MAX_REINTENTOS,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": random.choice(config.USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CL,es;q=0.9",
        "Referer": BASE_URL,
    })
    return s


def parsear_pagina(html: str) -> list[dict]:
    """
    Extrae los concursos publicados en la página del Poder Judicial.
    El sitio publica los llamados como texto estructurado con fechas.
    """
    soup = BeautifulSoup(html, "html.parser")
    ofertas = []

    # El PJUD publica los concursos como párrafos o divs con texto
    # Buscar secciones que contengan convocatorias
    contenido = (
        soup.find("div", class_=re.compile("content|contenido|concurso|trabaje", re.I))
        or soup.find("main")
        or soup.find("article")
        or soup.body
    )

    if not contenido:
        logger.warning("No se encontró contenido principal en pjud.cl")
        return []

    texto_completo = contenido.get_text("\n", strip=True)
    lineas = [l.strip() for l in texto_completo.split("\n") if l.strip()]

    # Buscar bloques que parecen convocatorias
    # Patrón típico: "LLAMA A CONCURSO..." seguido de cargo y plazo
    for i, linea in enumerate(lineas):
        if not re.search(r"concurso|convocatoria|cargo|juez|notario|conservador", linea, re.I):
            continue
        if len(linea) < 20:
            continue

        # Extraer información del bloque
        bloque = " ".join(lineas[max(0, i-1):min(len(lineas), i+5)])

        # Detectar región
        region = None
        reg_match = re.search(
            r"(?:región de|corte de apelaciones de|tribunal de)\s+([\w\s]+?)(?:\.|,|$)",
            bloque, re.I
        )
        if reg_match:
            region = normalizar_region(reg_match.group(1).strip())

        # Detectar fecha de cierre
        fecha_cierre = None
        fecha_match = re.search(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b", bloque)
        if fecha_match:
            try:
                d, m, y = fecha_match.groups()
                fecha_cierre = date(int(y), int(m), int(d))
            except ValueError:
                pass

        cargo = limpiar_texto(linea)[:500]
        id_externo = generar_id_estable(FUENTE_ID, cargo, bloque)

        # Buscar link asociado
        link = None
        for a in contenido.find_all("a", href=True):
            if any(w in a.get_text().lower() for w in ["concurso", "postul", "cargo", "edicto"]):
                href = a["href"]
                link = href if href.startswith("http") else BASE_URL + href
                break

        url_original = f"{(link or LIST_URL)}#oferta-{id_externo}"

        oferta = {
            "id_externo":        id_externo,
            "fuente_id":         FUENTE_ID,
            "url_original":      url_original,
            "cargo":             cargo,
            "descripcion":       bloque[:2000],
            "institucion_nombre": INST_NOMBRE,
            "sector":            "Judicial",
            "area_profesional":  "Derecho",
            "tipo_cargo":        "Planta",
            "nivel":             "Directivo" if any(w in cargo.lower() for w in ["juez", "ministro", "fiscal"]) else "Profesional",
            "region":            region,
            "ciudad":            None,
            "renta_bruta_min":   None,
            "renta_bruta_max":   None,
            "renta_texto":       None,
            "fecha_publicacion": date.today(),
            "fecha_cierre":      fecha_cierre,
            "requisitos_texto":  None,
        }
        ofertas.append(oferta)

    # Deduplicar por URL
    vistos = set()
    resultado = []
    for o in ofertas:
        key = o["url_original"] + o["cargo"][:50]
        if key not in vistos:
            vistos.add(key)
            resultado.append(o)

    return resultado


def ejecutar(dry_run: bool = False):
    inicio = time.time()
    logger.info("=" * 60)
    logger.info("INICIO - Scraper Poder Judicial")
    logger.info("=" * 60)

    sesion = crear_sesion()
    db = SessionLocal()
    stats = {"nuevas": 0, "actualizadas": 0, "cerradas": 0, "errores": 0}
    urls_activas: list[str] = []

    try:
        resp = sesion.get(LIST_URL, timeout=config.TIMEOUT_REQUEST)
        resp.raise_for_status()
        resp.encoding = "utf-8"

        ofertas = parsear_pagina(resp.text)
        logger.info(f"  -> {len(ofertas)} concursos encontrados")

        for datos in ofertas:
            urls_activas.append(datos["url_original"])
            if dry_run:
                logger.info(f"  [DRY] {datos['cargo'][:70]}")
                continue

            try:
                nueva, actualizada = upsert_oferta(db, datos)
                if nueva:
                    stats["nuevas"] += 1
                elif actualizada:
                    stats["actualizadas"] += 1
            except Exception as e:
                db.rollback()
                stats["errores"] += 1
                logger.exception(
                    "Error procesando oferta %s: %s",
                    datos.get("id_externo") or datos["url_original"],
                    e,
                )
                continue

        if not dry_run and urls_activas:
            stats["cerradas"] = marcar_ofertas_cerradas(db, FUENTE_ID, sorted(urls_activas))

    except Exception as e:
        if not dry_run:
            db.rollback()
        logger.exception(f"Error en scraper PJUD: {e}")
        stats["errores"] += 1
        raise
    finally:
        duracion = time.time() - inicio
        logger.info(
            f"  Nuevas: {stats['nuevas']} | Actualizadas: {stats['actualizadas']} | "
            f"Cerradas: {stats['cerradas']} | Errores: {stats['errores']} | {duracion:.1f}s"
        )
        if not dry_run:
            try:
                db.rollback()
                registrar_log(
                    db, FUENTE_ID,
                    "OK" if stats["errores"] == 0 else "PARCIAL",
                    ofertas_nuevas=stats["nuevas"],
                    ofertas_actualizadas=stats["actualizadas"],
                    ofertas_cerradas=stats["cerradas"],
                    paginas=1,
                    duracion=duracion,
                )
            except Exception:
                logger.exception("No se pudo registrar el log final de PJUD")
        db.close()

    return stats


if __name__ == "__main__":
    import os
    os.makedirs(config.LOG_DIR, exist_ok=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ejecutar(dry_run=args.dry_run)
