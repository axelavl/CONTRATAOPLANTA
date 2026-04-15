"""
EmpleoEstado.cl — Scraper: Municipalidad de La Florida

URLs verificadas:
  - /sitio/concurso-publico/       → Planta municipal
  - /sitio/bolsa-de-empleo/        → Bolsa general
  - /sitio/ofertas-laborales-municipales/ → Honorarios directos

Uso:
    python scrapers/muni_la_florida.py
    python scrapers/muni_la_florida.py --dry-run --verbose
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scrapers._base_wordpress import MuniWordPressBase


class MuniLaFlorida(MuniWordPressBase):
    FUENTE_ID   = 22
    BASE_URL    = "https://www.laflorida.cl"
    URL_EMPLEO  = "https://www.laflorida.cl/sitio/concurso-publico/"
    URLS_EXTRA  = [
        "https://www.laflorida.cl/sitio/bolsa-de-empleo/",
        "https://www.laflorida.cl/sitio/ofertas-laborales-municipales/",
    ]
    INSTITUCION = "Municipalidad de La Florida"
    CIUDAD      = "La Florida"
    REGION      = "Metropolitana de Santiago"


def ejecutar(dry_run=False, verbose=False):
    return MuniLaFlorida().ejecutar(dry_run=dry_run, verbose=verbose)


if __name__ == "__main__":
    import os; os.makedirs("logs", exist_ok=True)
    p = argparse.ArgumentParser(description="Scraper Municipalidad de La Florida")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    a = p.parse_args()
    ejecutar(dry_run=a.dry_run, verbose=a.verbose)
