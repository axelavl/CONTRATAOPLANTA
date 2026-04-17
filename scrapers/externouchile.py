"""
EmpleoEstado.cl — Scraper: Universidad de Chile (Portal Externo Trabajando.cl)
URL: https://externouchile.trabajando.cl/

Portal del sitio externo de la Universidad de Chile implementado sobre la
plataforma Trabajando.cl. Publica ofertas laborales (Código del Trabajo y
honorarios) de las distintas facultades y servicios centrales de la UCH.

Estrategia:
    1. HTTP directo sobre varias rutas candidatas (Trabajando.cl renderiza
       listados en servidor, no necesita JS en la mayoría de los casos).
    2. Fallback a Playwright si el HTML no trae ofertas (bloqueo WAF o
       render tardío).
    3. Parseo heurístico robusto: tablas -> cards -> listas de enlaces.

Uso:
    python scrapers/externouchile.py
    python scrapers/externouchile.py --dry-run --verbose
    python scrapers/externouchile.py --max 5 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import config
from db.database import (
    SessionLocal,
    generar_id_estable,
    limpiar_texto,
    marcar_ofertas_cerradas,
    normalizar_area,
    normalizar_region,
    registrar_log,
    upsert_oferta,
)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError:
    sync_playwright = None  # type: ignore[assignment,misc]
    PwTimeout = Exception  # type: ignore[assignment,misc]

LOG_DIR = Path(config.LOG_DIR)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("scraper.externouchile")
logger.setLevel(getattr(logging, config.LOG_LEVEL))
if not logger.handlers:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.FileHandler(LOG_DIR / "externouchile.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
logger.propagate = False

FUENTE_ID = 242  # Universidad de Chile (ver repositorio_instituciones_publicas_chile.json)
INST_NOMBRE = "Universidad de Chile"
REGION = "Nacional"
SECTOR = "Universidad/Educación"
BASE_URL = "https://externouchile.trabajando.cl"

# Rutas candidatas ordenadas por probabilidad empírica de contener listado.
URLS_CANDIDATAS = [
    f"{BASE_URL}/",
    f"{BASE_URL}/trabajo-empleo",
    f"{BASE_URL}/empleos",
    f"{BASE_URL}/empleos/buscar-empleos",
    f"{BASE_URL}/buscar-empleos",
]

# Timeouts
HTTP_TIMEOUT = 20
PAGE_LOAD_TIMEOUT = 30_000

# Palabras clave que identifican una oferta (para filtrar ruido).
_KEYWORDS_OFERTA = (
    "cargo", "vacante", "oferta", "empleo", "postul",
    "profesional", "analista", "ingenier", "coordinador", "coordinadora",
    "asistente", "tecnico", "técnico", "academico", "académico", "docente",
    "administrativo", "administrativa", "secretario", "secretaria",
    "auxiliar", "encargado", "encargada", "jefe", "jefa",
    "investigador", "investigadora", "asesor", "asesora",
)

# Hosts aceptados al resolver URLs de detalle.
_ALLOWED_HOSTS = {"externouchile.trabajando.cl", "www.externouchile.trabajando.cl"}


# ── Fetch HTTP ──────────────────────────────────────────────────────────────
def _http_get(url: str) -> str | None:
    """Descarga una URL con headers de navegador real. Retorna HTML o None."""
    import requests

    headers = {
        "User-Agent": random.choice(config.USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT, allow_redirects=True)
        resp.encoding = resp.encoding or "utf-8"
        if resp.status_code >= 400:
            logger.info("  HTTP %s en %s", resp.status_code, url)
            return None
        html = resp.text
        logger.info("  HTTP OK %s (%d chars)", url, len(html))
        return html
    except Exception as exc:
        logger.info("  HTTP fallo %s: %s", url, type(exc).__name__)
        return None


# ── Fallback con Playwright ─────────────────────────────────────────────────
def _pw_get(url: str) -> str | None:
    """Renderiza la URL con Chromium headless cuando el HTTP directo falla."""
    if sync_playwright is None:
        logger.warning("  Playwright no disponible; se omite fallback")
        return None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=random.choice(config.USER_AGENTS),
            locale="es-CL",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            logger.info("  Playwright navegando a %s", url)
            page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)

            for selector in (
                "a[href*='ficha']",
                "a[href*='empleo']",
                "a[href*='oferta']",
                "[class*='oferta']",
                "[class*='job']",
                "table tbody tr",
                "main",
            ):
                try:
                    page.wait_for_selector(selector, timeout=2500)
                    if page.locator(selector).count() > 0:
                        break
                except PwTimeout:
                    continue
            else:
                page.wait_for_timeout(4000)

            # Scroll por si hay lazy loading.
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)

            html = page.content()
            logger.info("  Playwright HTML (%d chars)", len(html))
            return html
        except Exception as exc:
            logger.info("  Playwright error %s: %s", url, type(exc).__name__)
            return None
        finally:
            context.close()
            browser.close()


def _fetch_html(url: str) -> str | None:
    """Intenta HTTP directo y, si no alcanza a detectar ofertas, usa Playwright."""
    html = _http_get(url)
    if html and _tiene_indicios_de_ofertas(html):
        return html
    logger.info("  HTML HTTP sin indicios de ofertas, probando Playwright")
    return _pw_get(url) or html


def _tiene_indicios_de_ofertas(html: str) -> bool:
    lower = html.lower()
    if len(lower) < 1500:
        return False
    hits = sum(1 for kw in ("ficha", "oferta", "empleo", "postul", "cargo", "vacante")
               if kw in lower)
    return hits >= 2


# ── Parseo ──────────────────────────────────────────────────────────────────
def parsear_html(html: str, url_fuente: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    ofertas: list[dict] = []

    # Trabajando.cl suele enlazar cada oferta con hrefs tipo:
    #   /ficha-de-empleo/xxxxx
    #   /empleos/ofertas/xxxxx
    #   /job-offer/xxxxx
    patron_detalle = re.compile(r"(ficha|oferta|empleo|job[-_ ]?offer)", re.I)

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if not patron_detalle.search(href):
            continue

        url_abs = urljoin(url_fuente, href)
        parsed = urlparse(url_abs)
        if parsed.netloc and parsed.netloc not in _ALLOWED_HOSTS:
            continue
        # Descarta hrefs que sean sólo la landing (sin id/slug).
        if parsed.path.rstrip("/") in {"", "/empleos", "/ofertas", "/ficha-de-empleo"}:
            continue

        titulo = limpiar_texto(a.get_text(" ", strip=True))
        contenedor = a.find_parent(["article", "li", "tr", "div", "section"])
        contexto = limpiar_texto(contenedor.get_text(" ", strip=True)) if contenedor else titulo

        if not titulo:
            titulo = contexto[:200]
        if not _parece_oferta(titulo, contexto):
            continue

        ofertas.append(_construir_oferta(titulo, contexto, url_abs))

    if ofertas:
        logger.info("  Parseo enlaces: %d ofertas", len(ofertas))
        return _deduplicar(ofertas)

    # Fallback: tablas o tarjetas (algunas variantes del tema Trabajando.cl).
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        texto = limpiar_texto(row.get_text(" ", strip=True))
        if not _parece_oferta(texto, texto):
            continue
        link = row.find("a", href=True)
        href = link["href"] if link else ""
        url_abs = urljoin(url_fuente, href) if href else url_fuente
        cargo = limpiar_texto(cells[0].get_text(" ", strip=True)) or texto[:200]
        ofertas.append(_construir_oferta(cargo, texto, url_abs))

    if ofertas:
        logger.info("  Parseo tablas: %d ofertas", len(ofertas))
        return _deduplicar(ofertas)

    # Último recurso: cualquier bloque tipo "card" con palabras clave.
    for sel in ("[class*='oferta']", "[class*='job']", "[class*='card']", "article"):
        for node in soup.select(sel):
            texto = limpiar_texto(node.get_text(" ", strip=True))
            if not _parece_oferta(texto, texto):
                continue
            titulo_el = node.select_one("h1, h2, h3, h4, h5, .title, [class*='title']")
            cargo = limpiar_texto(titulo_el.get_text(" ", strip=True)) if titulo_el else texto[:200]
            link = node.find("a", href=True)
            href = link["href"] if link else ""
            url_abs = urljoin(url_fuente, href) if href else url_fuente
            ofertas.append(_construir_oferta(cargo, texto, url_abs))
        if ofertas:
            logger.info("  Parseo cards '%s': %d ofertas", sel, len(ofertas))
            return _deduplicar(ofertas)

    return []


def _parece_oferta(titulo: str, contexto: str) -> bool:
    total = f"{titulo} {contexto}".lower()
    if len(total) < 15:
        return False
    return any(kw in total for kw in _KEYWORDS_OFERTA)


def _construir_oferta(cargo: str, contexto: str, url: str) -> dict:
    cargo_limpio = (cargo or "").strip()[:500]
    id_externo = generar_id_estable(FUENTE_ID, INST_NOMBRE, cargo_limpio, contexto[:300])

    # Trabajando.cl no expone fecha cierre/renta en el listado; se deja que el
    # pipeline posterior (detalle) la complete, o se omite si nunca llega.
    fecha_cierre = _extraer_fecha_cierre(contexto)
    region_detectada = _detectar_region(contexto) or REGION
    ciudad = _detectar_ciudad(contexto)

    return {
        "id_externo": id_externo,
        "fuente_id": FUENTE_ID,
        "url_original": url,
        "cargo": cargo_limpio,
        "descripcion": contexto[:2000] if len(contexto) > 30 else None,
        "institucion_nombre": INST_NOMBRE,
        "sector": SECTOR,
        "area_profesional": normalizar_area(cargo_limpio),
        "tipo_cargo": _detectar_tipo_cargo(contexto) or "Código del Trabajo",
        "nivel": _detectar_nivel(cargo_limpio),
        "region": region_detectada,
        "ciudad": ciudad,
        "renta_bruta_min": None,
        "renta_bruta_max": None,
        "renta_texto": None,
        "fecha_publicacion": date.today(),
        "fecha_cierre": fecha_cierre,
        "requisitos_texto": None,
    }


def _detectar_tipo_cargo(texto: str) -> str | None:
    t = texto.lower()
    if "planta" in t:
        return "Planta"
    if "contrata" in t:
        return "Contrata"
    if "honorario" in t:
        return "Honorarios"
    if "reemplazo" in t:
        return "Reemplazo"
    return None


def _detectar_nivel(cargo: str) -> str:
    c = cargo.lower()
    if any(w in c for w in ("decano", "decana", "director", "directora", "vicerrector", "gerente")):
        return "Directivo"
    if any(w in c for w in ("jefe", "jefa", "coordinador", "coordinadora", "supervisor")):
        return "Profesional"
    if any(w in c for w in ("tecnico", "técnico", "auxiliar", "operador", "administrativo")):
        return "Técnico"
    return "Profesional"


def _detectar_region(texto: str) -> str | None:
    t = texto.lower()
    if not t:
        return None
    # Universidad de Chile concentra mayormente ofertas en la RM.
    for palabra in ("santiago", "metropolitana", "providencia", "ñuñoa", "nunoa", "independencia",
                    "estacion central", "estación central"):
        if palabra in t:
            return "Metropolitana de Santiago"
    for region in ("arica", "tarapaca", "antofagasta", "atacama", "coquimbo", "valparaiso",
                   "valparaíso", "ohiggins", "maule", "nuble", "ñuble", "biobio", "biobío",
                   "araucania", "araucanía", "los rios", "los ríos", "los lagos", "aysen",
                   "aysén", "magallanes"):
        if region in t:
            return normalizar_region(region)
    return None


def _detectar_ciudad(texto: str) -> str | None:
    t = texto.lower()
    for ciudad in ("santiago", "providencia", "ñuñoa", "independencia", "estación central",
                   "antofagasta", "valparaíso", "viña del mar", "concepción", "temuco",
                   "valdivia", "puerto montt"):
        if ciudad in t:
            return ciudad.title()
    return None


def _extraer_fecha_cierre(texto: str) -> date | None:
    # dd/mm/yyyy o dd-mm-yyyy
    m = re.findall(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", texto)
    if m:
        try:
            d, mo, y = m[-1]
            y = int(y)
            if y < 100:
                y += 2000
            return date(y, int(mo), int(d))
        except ValueError:
            pass
    return None


def _deduplicar(ofertas: list[dict]) -> list[dict]:
    vistos: set[str] = set()
    resultado: list[dict] = []
    for o in ofertas:
        clave = o["url_original"] + "||" + o["cargo"][:50]
        if clave in vistos:
            continue
        vistos.add(clave)
        resultado.append(o)
    return resultado


# ── Orquestación ────────────────────────────────────────────────────────────
def _recolectar(max_results: int | None = None) -> list[dict]:
    vistos: set[str] = set()
    ofertas: list[dict] = []

    for url in URLS_CANDIDATAS:
        html = _fetch_html(url)
        if not html:
            continue
        pagina = parsear_html(html, url)
        for oferta in pagina:
            if oferta["url_original"] in vistos:
                continue
            vistos.add(oferta["url_original"])
            ofertas.append(oferta)
            if max_results and len(ofertas) >= max_results:
                return ofertas
        # Si una URL entregó resultados y no buscamos más, cortamos.
        if pagina and not max_results:
            break
    return ofertas


def ejecutar(
    dry_run: bool = False,
    verbose: bool = False,
    max_results: int | None = None,
) -> dict[str, Any]:
    inicio = time.time()
    logger.info("=" * 60)
    logger.info("INICIO - Scraper Universidad de Chile (externouchile.trabajando.cl)")
    logger.info("=" * 60)

    db = SessionLocal()
    stats: dict[str, int] = {"nuevas": 0, "actualizadas": 0, "cerradas": 0, "errores": 0}
    urls_activas: list[str] = []

    try:
        ofertas = _recolectar(max_results=max_results)
        logger.info("  → %d ofertas recolectadas", len(ofertas))

        for datos in ofertas:
            urls_activas.append(datos["url_original"])
            if verbose or dry_run:
                print(f"  [{datos['region']}] {datos['cargo'][:80]}")
                if verbose:
                    print(f"      {datos['url_original']}")
            if dry_run:
                continue
            try:
                nueva, actualizada = upsert_oferta(db, datos)
                if nueva:
                    stats["nuevas"] += 1
                elif actualizada:
                    stats["actualizadas"] += 1
            except Exception as exc:
                stats["errores"] += 1
                db.rollback()
                logger.exception("  Error en upsert: %s", exc)

        if not dry_run and urls_activas:
            stats["cerradas"] = marcar_ofertas_cerradas(db, FUENTE_ID, sorted(urls_activas))

    except Exception as exc:
        if not dry_run:
            db.rollback()
        stats["errores"] += 1
        logger.exception("  Error global: %s", exc)
        raise
    finally:
        dur = time.time() - inicio
        logger.info(
            "  Nuevas: %d | Act: %d | Cerradas: %d | Err: %d | %.1fs",
            stats["nuevas"], stats["actualizadas"],
            stats["cerradas"], stats["errores"], dur,
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
                    paginas=len(URLS_CANDIDATAS), duracion=dur,
                )
            except Exception:
                logger.exception("  No se pudo registrar log")
        db.close()

    return stats


if __name__ == "__main__":
    import os
    os.makedirs(config.LOG_DIR, exist_ok=True)
    parser = argparse.ArgumentParser(
        description="Scraper Universidad de Chile — externouchile.trabajando.cl"
    )
    parser.add_argument("--dry-run", action="store_true", help="No guarda en BD")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log detallado de ofertas")
    parser.add_argument("--max", type=int, default=None, help="Tope de ofertas a procesar")
    args = parser.parse_args()
    ejecutar(dry_run=args.dry_run, verbose=args.verbose, max_results=args.max)
