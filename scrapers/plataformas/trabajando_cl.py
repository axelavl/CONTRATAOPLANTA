from __future__ import annotations

from typing import Any

from .generic_site import GenericSiteScraper


ATS_TRABAJANDO_KEYWORDS = (
    "trabajando",
    "vacante",
    "job",
    "trabajo",
    "position",
    "postula",
)


class TrabajandoCLScraper(GenericSiteScraper):
    """Adaptador async para portales Trabajando.cl."""

    def __init__(self, *, fuente_id: int, institucion: dict[str, Any]) -> None:
        super().__init__(
            fuente_id=fuente_id,
            institucion=institucion,
            candidate_paths=("/ofertas", "/trabajos", "/empleos"),
            extra_keywords=ATS_TRABAJANDO_KEYWORDS,
            max_candidate_urls=3,
            detail_fetch_limit=16,
            trusted_host_only=False,
        )
