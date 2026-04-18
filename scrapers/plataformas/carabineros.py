from __future__ import annotations

from typing import Any

from .pdf_first import PdfFirstScraper


CARABINEROS_URLS = (
    "/",
    "/concursos",
    "/convocatorias",
    "/transparencia/concursos/",
)

CARABINEROS_KEYWORDS = (
    "personal civil",
    "descriptor",
    "perfil",
    "dotacion",
    "dotación",
    "postulacion",
)


class CarabinerosScraper(PdfFirstScraper):
    """Adaptador runtime-compatible para postulaciones de Carabineros."""

    def __init__(self, *, fuente_id: int, institucion: dict[str, Any]) -> None:
        super().__init__(
            fuente_id=fuente_id,
            institucion=institucion,
            candidate_paths=CARABINEROS_URLS,
            extra_keywords=CARABINEROS_KEYWORDS,
            max_candidate_urls=4,
            detail_fetch_limit=10,
            trusted_host_only=False,
        )
