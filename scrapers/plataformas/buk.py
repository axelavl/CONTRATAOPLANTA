from __future__ import annotations

from typing import Any

from .generic_site import GenericSiteScraper


BUK_KEYWORDS = (
    "buk",
    "vacante",
    "job",
    "empleo",
    "position",
    "postula",
)


class BukScraper(GenericSiteScraper):
    """Adaptador async para portales Buk."""

    def __init__(self, *, fuente_id: int, institucion: dict[str, Any]) -> None:
        super().__init__(
            fuente_id=fuente_id,
            institucion=institucion,
            candidate_paths=("/jobs", "/careers", "/trabajos", "/empleos"),
            extra_keywords=BUK_KEYWORDS,
            max_candidate_urls=3,
            detail_fetch_limit=16,
            trusted_host_only=False,
        )
