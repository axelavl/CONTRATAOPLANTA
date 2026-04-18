from __future__ import annotations

from typing import Any

from .generic_site import GenericSiteScraper


class PlaywrightScraper(GenericSiteScraper):
    """Wrapper opcional para fuentes JS intensivas.

    Mientras Playwright no este instalado en el entorno, se usa un fallback
    HTTP/HTML best-effort sin romper la corrida. La evidencia y el routing ya
    quedan registrados por el gatekeeper.
    """

    def __init__(self, *, fuente_id: int, institucion: dict[str, Any]) -> None:
        super().__init__(
            fuente_id=fuente_id,
            institucion=institucion,
            candidate_paths=("/careers", "/trabaja-con-nosotros", "/empleos", "/jobs"),
            extra_keywords=("vacante", "position", "job", "careers"),
            max_candidate_urls=4,
            detail_fetch_limit=10,
            trusted_host_only=False,
        )
