from __future__ import annotations

from typing import Any

from .generic_site import GenericSiteScraper


HIRINGROOM_KEYWORDS = (
    "hiringroom",
    "vacante",
    "job",
    "empleo",
    "position",
    "modalidad",
)


class HiringRoomScraper(GenericSiteScraper):
    """Adaptador async para portales HiringRoom."""

    def __init__(self, *, fuente_id: int, institucion: dict[str, Any]) -> None:
        super().__init__(
            fuente_id=fuente_id,
            institucion=institucion,
            candidate_paths=("/jobs", "/empleos", "/trabajos", "/careers"),
            extra_keywords=HIRINGROOM_KEYWORDS,
            max_candidate_urls=3,
            detail_fetch_limit=16,
            trusted_host_only=False,
        )
