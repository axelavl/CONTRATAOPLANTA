from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scrapers.base import (
    OfertaRaw,
    clean_text,
    normalize_region,
    normalize_tipo_contrato,
    parse_date,
    parse_renta,
    setup_logging,
)

from .generic_site import GenericSiteScraper


# ── Constantes ─────────────────────────────────────────────────────────────────

ATS_TRABAJANDO_KEYWORDS = (
    "trabajando",
    "vacante",
    "trabajo",
    "postula",
    "oferta",
    "cargo",
)

_MAX_PAGINAS = 10  # máximo de páginas a iterar por empresa

log = setup_logging("scraper.trabajando_cl")


class TrabajandoCLScraper(GenericSiteScraper):
    """
    Scraper para portales Trabajando.cl de instituciones públicas.

    Trabajando.cl es un SPA Nuxt.js que incluye estado del servidor (SSR) en
    un script inline.  Parseamos ese estado para extraer las ofertas sin
    necesitar Playwright.

    URL típica: https://{empresa}.trabajando.cl/trabajo-empleo
    Paginación: ?pagina=2, ?pagina=3, …
    Detalle:    https://{empresa}.trabajando.cl/oferta/{id}
    """

    def __init__(self, *, fuente_id: int, institucion: dict[str, Any]) -> None:
        super().__init__(
            fuente_id=fuente_id,
            institucion=institucion,
            candidate_paths=("/offers", "/trabajo-empleo", "/ofertas", "/empleos"),
            extra_keywords=ATS_TRABAJANDO_KEYWORDS,
            max_candidate_urls=2,
            detail_fetch_limit=20,
            trusted_host_only=False,
        )

    # ── Punto de entrada ──────────────────────────────────────────────────────

    async def descubrir_ofertas(self) -> list[OfertaRaw]:
        """Extrae todas las ofertas iterando las páginas del SSR de Nuxt."""
        if self.http is None:
            raise RuntimeError("TrabajandoCLScraper requiere HttpClient activo.")

        base_url = await self._resolve_base_url()
        log.info(
            "[%s] base_url resuelta=%s (url_empleo=%s, sitio_web=%s)",
            self.institucion_id,
            base_url or "<vacía>",
            self.url_empleo or "<vacía>",
            self.sitio_web or "<vacía>",
        )
        if not base_url:
            log.warning("[%s] sin base_url ATS; no se puede descubrir ofertas", self.institucion_id)
            return []

        offers: list[OfertaRaw] = []
        seen_ids: set[str] = set()
        consecutive_empty_pages = 0

        for pagina in range(1, _MAX_PAGINAS + 1):
            url = f"{base_url}?pagina={pagina}" if pagina > 1 else base_url
            result = await self.http.fetch(url)
            html = result.body
            body_len = len(html) if isinstance(html, str) else 0
            has_nuxt = bool(html and ("__NUXT" in html or "ShallowReactive" in html or "cantidadPaginas" in html))
            log.info(
                "[%s] GET pagina=%s url=%s status=%s len_html=%s has_nuxt=%s final_url=%s",
                self.institucion_id,
                pagina,
                url,
                result.status,
                body_len,
                has_nuxt,
                result.final_url,
            )
            if not isinstance(html, str) or not html.strip():
                log.warning("[%s] página vacía/None en pagina=%s; se corta paginación", self.institucion_id, pagina)
                break

            page_offers, total_paginas = self._parse_nuxt_state(html, base_url)
            nuevas = 0
            for oferta in page_offers:
                if oferta.url not in seen_ids:
                    seen_ids.add(oferta.url)
                    offers.append(oferta)
                    nuevas += 1

            log.info(
                "[%s] pagina=%s total_paginas=%s ofertas_detectadas=%s nuevas=%s acumuladas=%s",
                self.institucion_id,
                pagina,
                total_paginas,
                len(page_offers),
                nuevas,
                len(offers),
            )

            if nuevas == 0:
                consecutive_empty_pages += 1
            else:
                consecutive_empty_pages = 0

            if pagina >= total_paginas:
                break
            # Evita cortar demasiado pronto cuando la primera página viene con
            # layout raro o sin SSR parseable.
            if consecutive_empty_pages >= 2:
                log.warning(
                    "[%s] 2 páginas consecutivas sin nuevas ofertas; se corta paginación",
                    self.institucion_id,
                )
                break

        log.info("[%s] fin descubrir_ofertas: total_ofertas=%s", self.institucion_id, len(offers))
        return offers

    async def _resolve_base_url(self) -> str:
        """
        Resuelve la URL base ATS para scraping.

        Prioridad:
        1) url_empleo/sitio_web ya en trabajando.cl
        2) descubrir enlace trabajando.cl desde página institucional
        """
        direct = self._canonical_base()
        if direct:
            validated = await self._find_working_listing_url(direct)
            if validated:
                return validated
        discovered = await self._discover_base_from_institutional_site()
        if discovered:
            validated = await self._find_working_listing_url(discovered)
            if validated:
                return validated
        return ""

    async def _find_working_listing_url(self, base_url: str) -> str:
        """
        Dada una URL ATS, prueba rutas conocidas de listado y retorna la primera
        que responda con contenido útil.
        """
        if self.http is None:
            return base_url.rstrip("/")

        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        candidates: list[str] = []
        if path:
            candidates.append(f"{origin}{path}")
        candidates.extend(
            [
                f"{origin}/offers",
                f"{origin}/trabajo-empleo",
                f"{origin}/ofertas",
                f"{origin}/empleos",
            ]
        )

        for candidate in dict.fromkeys(candidates):
            result = await self.http.fetch(candidate)
            html = result.body or ""
            has_signal = any(token in html for token in ("__NUXT", "ShallowReactive", "offers/detail", "cantidadPaginas"))
            log.info(
                "[%s] probe listing candidate=%s status=%s len_html=%s signal=%s",
                self.institucion_id,
                candidate,
                result.status,
                len(html),
                has_signal,
            )
            if result.status and result.status < 400 and html.strip() and has_signal:
                return candidate.rstrip("/")
        return base_url.rstrip("/")

    # ── Parseo del estado SSR de Nuxt ─────────────────────────────────────────

    def _parse_nuxt_state(
        self, html: str, base_url: str
    ) -> tuple[list[OfertaRaw], int]:
        """
        Extrae ofertas del estado SSR embebido por Nuxt.
        Devuelve (lista_de_ofertas, total_paginas).
        """
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        nuxt_state = ""
        matched_by = "none"
        for s in scripts:
            if (
                "ShallowReactive" in s
                or "__NUXT" in s
                or "cantidadPaginas" in s
                or "oferta" in s.lower()
            ):
                nuxt_state = s
                matched_by = "script"
                break

        if not nuxt_state:
            # Segundo intento: buscar en todo el HTML cuando el estado viene
            # serializado fuera del primer script relevante.
            if "cantidadPaginas" in html or re.search(r"(\d{6,8}),\"([^\"]{6,180})\"", html):
                nuxt_state = html
                matched_by = "full_html"
            else:
                # Fallback: parseo HTML genérico
                log.info(
                    "[%s] sin estado Nuxt parseable; fallback HTML",
                    self.institucion_id,
                )
                return self._parse_html_fallback(html, base_url), 1

        if not nuxt_state:
            # Fallback: parseo HTML genérico
            return self._parse_html_fallback(html, base_url), 1

        # Total de páginas
        m_pages = re.search(r"cantidadPaginas[^:]*[:.,]\s*(\d+)", nuxt_state)
        total_paginas = int(m_pages.group(1)) if m_pages else 1

        # Extraer pares (id_oferta, nombre_cargo) del estado serializado.
        # El estado de Nuxt serializa como: ID_OFERTA,"NombreCargo",...
        # Los IDs de oferta son números de 7-8 dígitos; los títulos son strings ≥10 chars.
        raw_pairs = re.findall(r"(\d{6,8}),\"([^\"]{6,180})\"", nuxt_state)
        # Filtrar ruido: solo pares cuyo título tiene letras
        pairs = [(id_v, t) for id_v, t in raw_pairs if re.search(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]", t)]
        discarded_pairs = len(raw_pairs) - len(pairs)

        # Extraer más campos por oferta (descripción, ubicación, fecha publicación)
        # A veces aparecen embebidos en el mismo bloque
        desc_map: dict[str, str] = {}
        loc_map: dict[str, str] = {}
        fecha_map: dict[str, str] = {}

        # Ubicaciones suelen estar después del título
        for m in re.finditer(
            r"(\d{6,8}),\"[^\"]{6,180}\",\"[^\"]*\",\"[^\"]*\",\"([^\"]{5,200})\",",
            nuxt_state,
        ):
            loc_map[m.group(1)] = m.group(2)

        # Fechas de publicación (formato "YYYY-MM-DD HH:MM")
        for m in re.finditer(
            r"(\d{6,8}).*?\"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\"", nuxt_state
        ):
            fecha_map.setdefault(m.group(1), m.group(2))

        ofertas: list[OfertaRaw] = []
        for id_oferta, nombre_cargo in pairs:
            url_oferta = f"{base_url.rstrip('/')}/../oferta/{id_oferta}"
            # Normalizar URL: reemplazar /trabajo-empleo/../oferta por /oferta
            url_oferta = self._resolve_offer_url(base_url, id_oferta)
            descripcion = desc_map.get(id_oferta, "")
            ubicacion_raw = loc_map.get(id_oferta, "")
            fecha_raw = fecha_map.get(id_oferta, "")
            region = self._region_from_ubicacion(ubicacion_raw)
            fecha_pub = parse_date(fecha_raw.split(" ")[0] if fecha_raw else "")
            renta_min, renta_max, grado = parse_renta(descripcion)

            ofertas.append(
                OfertaRaw(
                    url=url_oferta,
                    cargo=clean_text(nombre_cargo),
                    institucion_nombre=str(
                        self.institucion.get("nombre") or self.nombre_fuente
                    ),
                    descripcion=descripcion or None,
                    sector=self.institucion.get("sector"),
                    tipo_cargo=normalize_tipo_contrato(nombre_cargo),
                    region=region or normalize_region(self.institucion.get("region")),
                    ciudad=self._ciudad_from_ubicacion(ubicacion_raw),
                    renta_texto=None,
                    renta_min=renta_min,
                    renta_max=renta_max,
                    grado_eus=grado,
                    fecha_publicacion=fecha_pub,
                    fecha_cierre=None,
                    area_profesional=self._infer_area(nombre_cargo),
                    url_bases=None,
                )
            )

        log.info(
            "[%s] parse_nuxt matched_by=%s total_paginas=%s raw_pairs=%s descartadas=%s ofertas=%s",
            self.institucion_id,
            matched_by,
            total_paginas,
            len(raw_pairs),
            discarded_pairs,
            len(ofertas),
        )
        return ofertas, total_paginas

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _canonical_base(self) -> str:
        """Devuelve la URL base canónica (sin ?query) para la empresa."""
        url = self.url_empleo or self.sitio_web
        if not url:
            return ""
        # Si la URL ya apunta a /trabajo-empleo, usarla directamente
        if "trabajando.cl" in url:
            return url.rstrip("/")
        return ""

    async def _discover_base_from_institutional_site(self) -> str:
        """Busca enlaces a *.trabajando.cl desde url_empleo/sitio_web institucional."""
        if self.http is None:
            return ""
        for seed in (self.url_empleo, self.sitio_web):
            if not seed:
                continue
            result = await self.http.fetch(seed)
            html = result.body
            log.info(
                "[%s] discover_base seed=%s status=%s len_html=%s",
                self.institucion_id,
                seed,
                result.status,
                len(html) if isinstance(html, str) else 0,
            )
            if not isinstance(html, str) or not html.strip():
                continue
            discovered = self._extract_trabajando_url_from_html(html, seed)
            if discovered:
                log.info("[%s] discover_base encontró ATS=%s", self.institucion_id, discovered)
                return discovered
            log.info("[%s] discover_base sin links trabajando en seed=%s", self.institucion_id, seed)
        return ""

    def _extract_trabajando_url_from_html(self, html: str, base_url: str) -> str:
        """Extrae y normaliza el primer enlace a trabajando.cl encontrado."""
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            href = clean_text(anchor.get("href"))
            if not href:
                continue
            full = urljoin(base_url, href)
            if "trabajando.cl" not in full.lower():
                continue
            if "/trabajo-empleo" in full or "/ofertas" in full or "/empleos" in full:
                return full.rstrip("/")
            parsed = urlparse(full)
            return f"{parsed.scheme}://{parsed.netloc}/trabajo-empleo"
        return ""

    def _resolve_offer_url(self, base_url: str, id_oferta: str) -> str:
        """Construye la URL de detalle de una oferta."""
        parsed = urlparse(base_url)
        domain_base = f"{parsed.scheme}://{parsed.netloc}"
        return f"{domain_base}/offers/detail/{id_oferta}"

    def _region_from_ubicacion(self, ubicacion: str) -> str | None:
        """Extrae nombre de región a partir de texto 'Ciudad, Región'."""
        if not ubicacion:
            return None
        parts = ubicacion.split(",")
        region_hint = parts[-1].strip() if len(parts) > 1 else ubicacion.strip()
        return normalize_region(region_hint)

    def _ciudad_from_ubicacion(self, ubicacion: str) -> str | None:
        if not ubicacion:
            return None
        parts = ubicacion.split(",")
        return clean_text(parts[0]) if parts else None

    def _parse_html_fallback(self, html: str, base_url: str) -> list[OfertaRaw]:
        """Fallback: parseo HTML genérico si no hay estado Nuxt."""
        soup = BeautifulSoup(html, "html.parser")
        offers: list[OfertaRaw] = []
        cards_total = 0
        descartadas_sin_titulo = 0
        descartadas_no_oferta = 0
        for card in soup.select("article, .job-card, .oferta-card, .card"):
            cards_total += 1
            title_el = card.select_one("h2, h3, .job-title, .titulo")
            if not title_el:
                descartadas_sin_titulo += 1
                continue
            title = clean_text(title_el.get_text(" ", strip=True))
            link = card.select_one("a[href]")
            href = clean_text(link.get("href") if link else "")
            url = urljoin(base_url, href) if href else base_url
            is_offer, _ = self._score_offer_candidate(title, "", url=url)
            if not title or not is_offer:
                descartadas_no_oferta += 1
                continue
            offers.append(
                OfertaRaw(
                    url=url,
                    cargo=title,
                    institucion_nombre=str(
                        self.institucion.get("nombre") or self.nombre_fuente
                    ),
                    descripcion=None,
                    sector=self.institucion.get("sector"),
                    tipo_cargo=None,
                    region=normalize_region(self.institucion.get("region")),
                    ciudad=None,
                    renta_texto=None,
                    renta_min=None,
                    renta_max=None,
                    grado_eus=None,
                    fecha_publicacion=None,
                    fecha_cierre=None,
                    area_profesional=None,
                    url_bases=None,
                )
            )
        log.info(
            "[%s] fallback_html cards=%s ofertas=%s descartes_sin_titulo=%s descartes_no_oferta=%s",
            self.institucion_id,
            cards_total,
            len(offers),
            descartadas_sin_titulo,
            descartadas_no_oferta,
        )
        return offers
