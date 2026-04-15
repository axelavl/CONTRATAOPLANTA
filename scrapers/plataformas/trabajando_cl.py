from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrapers.plataformas.generic_site import GenericSiteScraper


class TrabajandoCLScraper(GenericSiteScraper):
    """Wrapper para portales Trabajando.cl con fallback generico."""

    def _candidate_urls(self) -> list[str]:
        urls = super()._candidate_urls()
        empleo = self.url_empleo
        if empleo and empleo not in urls:
            urls.insert(0, empleo)
        return urls


def load_instituciones(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return payload.get("instituciones") if isinstance(payload, dict) else payload


def ejecutar(
    institucion: dict[str, Any],
    instituciones_catalogo: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    max_results: int | None = None,
) -> dict[str, Any]:
    scraper = TrabajandoCLScraper(
        institucion=institucion,
        instituciones_catalogo=instituciones_catalogo,
        dry_run=dry_run,
        max_results=max_results,
    )
    return scraper.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper para Trabajando.cl")
    parser.add_argument("--json", required=True, help="Ruta al JSON maestro")
    parser.add_argument("--id", type=int, required=True, help="ID de la institucion")
    parser.add_argument("--dry-run", action="store_true", help="No guarda en PostgreSQL")
    parser.add_argument("--max", type=int, default=None, help="Limite de ofertas")
    args = parser.parse_args()

    instituciones = load_instituciones(args.json)
    objetivo = next((item for item in instituciones if item.get("id") == args.id), None)
    if not objetivo:
        raise SystemExit(f"No se encontro la institucion con id={args.id}")

    print(
        ejecutar(
            institucion=objetivo,
            instituciones_catalogo=instituciones,
            dry_run=args.dry_run,
            max_results=args.max,
        )
    )
