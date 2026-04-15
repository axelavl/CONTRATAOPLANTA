"""
EmpleoEstado.cl — Scraper: Municipalidad de Temuco

Particularidad: Temuco usa un subdominio por año para su concurso de planta:
  concurso2024.temuco.cl (cerrado)
  concurso2025.temuco.cl (cerrado)
  concurso2026.temuco.cl (detectar automáticamente)

URLs monitoreadas:
  - temuco.cl/postulaciones-a-cargos-publicos/  → Concursos vigentes
  - concurso{AÑO}.temuco.cl                     → Concurso anual de planta
  - daemtemuco.cl/vacantes-laborales/            → Cargos docentes DAEM

Uso:
    python scrapers/muni_temuco.py
    python scrapers/muni_temuco.py --dry-run --verbose
"""
import sys, argparse, re, requests
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scrapers._base_wordpress import MuniWordPressBase


class MuniTemuco(MuniWordPressBase):
    FUENTE_ID   = 23
    BASE_URL    = "https://www.temuco.cl"
    URL_EMPLEO  = "https://www.temuco.cl/postulaciones-a-cargos-publicos/"
    URLS_EXTRA  = ["https://www.daemtemuco.cl/vacantes-laborales/"]
    INSTITUCION = "Municipalidad de Temuco"
    CIUDAD      = "Temuco"
    REGION      = "La Araucanía"

    def _detectar_subdominio_anual(self) -> str | None:
        """
        Verifica si existe el subdominio del año actual.
        Temuco crea: concurso{AÑO}.temuco.cl cada año para concurso de planta.
        """
        año = date.today().year
        url = f"https://concurso{año}.temuco.cl/"
        try:
            r = self.sesion.get(url, timeout=8, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 500:
                self.logger.info(f"  Subdominio anual detectado: {url}")
                return url
        except Exception:
            pass
        self.logger.debug(f"  Subdominio concurso{año}.temuco.cl no disponible aún")
        return None

    def ejecutar(self, dry_run=False, verbose=False):
        # Añadir subdominio anual si está disponible
        subdominio = self._detectar_subdominio_anual()
        if subdominio and subdominio not in self.URLS_EXTRA:
            self.URLS_EXTRA = [subdominio] + self.URLS_EXTRA
        return super().ejecutar(dry_run=dry_run, verbose=verbose)


def ejecutar(dry_run=False, verbose=False):
    return MuniTemuco().ejecutar(dry_run=dry_run, verbose=verbose)


if __name__ == "__main__":
    import os; os.makedirs("logs", exist_ok=True)
    p = argparse.ArgumentParser(description="Scraper Municipalidad de Temuco")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    a = p.parse_args()
    ejecutar(dry_run=a.dry_run, verbose=a.verbose)
