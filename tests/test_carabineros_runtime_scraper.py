from __future__ import annotations

import asyncio

from scrapers.base import HttpFetchResult
from scrapers.plataformas.carabineros import CarabinerosScraper


class _StubHttp:
    def __init__(self, responses: dict[str, list[HttpFetchResult]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def fetch(self, url: str, **_: object) -> HttpFetchResult:
        self.calls.append(url)
        queue = self.responses.get(url, [])
        if queue:
            return queue.pop(0)
        return HttpFetchResult(url=url, final_url=url, status=404, body="")


def _scraper(enable_secondary_fallback: bool = False) -> CarabinerosScraper:
    institucion = {
        "id": 161,
        "nombre": "Carabineros de Chile - Personal Civil",
        "url_empleo": "https://postulaciones.carabineros.cl/",
        "sitio_web": "https://www.carabineros.cl/",
        "enable_secondary_host_fallback": enable_secondary_fallback,
    }
    return CarabinerosScraper(fuente_id=161, institucion=institucion)


def test_candidate_urls_prioritizes_primary_host_only():
    scraper = _scraper(enable_secondary_fallback=False)
    urls = scraper._candidate_urls(max_urls=6)
    assert urls
    assert all("postulaciones.carabineros.cl" in url for url in urls)
    assert not any("www.carabineros.cl" in url for url in urls)


def test_candidate_urls_can_opt_in_secondary_fallback():
    scraper = _scraper(enable_secondary_fallback=True)
    urls = scraper._candidate_urls(max_urls=6)
    assert any("www.carabineros.cl" in url for url in urls)


def test_retry_once_only_for_primary_host_transient_errors():
    scraper = _scraper()
    primary_url = "https://postulaciones.carabineros.cl/concursos"
    secondary_url = "https://www.carabineros.cl/transparencia/concursos/"

    transient = HttpFetchResult(
        url=primary_url,
        final_url=primary_url,
        status=None,
        body=None,
        error_type="timeout",
        error_detail="asyncio.TimeoutError",
    )
    ok = HttpFetchResult(url=primary_url, final_url=primary_url, status=200, body="<html></html>")
    secondary_fail = HttpFetchResult(
        url=secondary_url,
        final_url=secondary_url,
        status=None,
        body=None,
        error_type="timeout",
        error_detail="asyncio.TimeoutError",
    )

    stub = _StubHttp(
        {
            primary_url: [transient, ok],
            secondary_url: [secondary_fail],
        }
    )
    scraper.http = stub  # type: ignore[assignment]

    primary_result = asyncio.run(scraper._fetch_listing(primary_url))
    secondary_result = asyncio.run(scraper._fetch_listing(secondary_url))

    assert primary_result.status == 200
    assert secondary_result.error_type == "timeout"
    assert stub.calls.count(primary_url) == 2
    assert stub.calls.count(secondary_url) == 1
