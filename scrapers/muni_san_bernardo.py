"""
EmpleoEstado.cl — Scraper: Municipalidad de San Bernardo
Hereda de MuniWordPressBase.

Uso:
    python scrapers/muni_san_bernardo.py
    python scrapers/muni_san_bernardo.py --dry-run --verbose
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scrapers._base_wordpress import MuniWordPressBase


class MuniSanBernardo(MuniWordPressBase):
    FUENTE_ID   = 21
    BASE_URL    = "https://www.sanbernardo.cl"
    URL_EMPLEO  = "https://www.sanbernardo.cl/concursos-publicos/"
    URLS_EXTRA  = ["https://www.sanbernardo.cl/ofertas-laborales/"]
    INSTITUCION = "Municipalidad de San Bernardo"
    CIUDAD      = "San Bernardo"
    REGION      = "Metropolitana de Santiago"


def ejecutar(dry_run=False, verbose=False):
    return MuniSanBernardo().ejecutar(dry_run=dry_run, verbose=verbose)


if __name__ == "__main__":
    import os; os.makedirs("logs", exist_ok=True)
    p = argparse.ArgumentParser(description="Scraper Municipalidad de San Bernardo")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    a = p.parse_args()
    ejecutar(dry_run=a.dry_run, verbose=a.verbose)
