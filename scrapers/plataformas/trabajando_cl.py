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
_INVALID_CARGO_PATTERNS = (
    r"^ver\s+detalle$",
    r"^ver\s+bases$",
    r"^postular?$",
    r"^postula(?:r)?$",
    r"^buscar\s+empleo$",
    r"^compartir$",
    r"^guardar$",
    r"^filtrar$",
    r"^orden(?:ar)?$",
)
_NOISE_PHRASES = {
    "ingresa",
    "crea tu cuenta",
    "publica tu oferta de empleo",
    "hazte premium",
    "mujeres stem",
    "empleo +50",
    "empleo joven",
    "blog",
    "buscar empleo",
    "ver detalle",
    "ver bases",
    "postular",
    "compartir",
    "guardar",
    "filtrar",
    "orden",
}


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
            candidate_paths=("/trabajo-empleo", "/ofertas", "/empleos"),
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
        if not base_url:
            self.log.info(
                "evento=oferta_descartada scraper=trabajando razon=sin_base_url fuente=%s",
                self.nombre_fuente,
            )
            return []

        self.log.info(
            "evento=inicio scraper=trabajando fuente=%s base_url=%s",
            self.nombre_fuente,
            base_url,
        )
        offers: list[OfertaRaw] = []
        seen_ids: set[str] = set()

        for pagina in range(1, _MAX_PAGINAS + 1):
            url = f"{base_url}?pagina={pagina}" if pagina > 1 else base_url
            html = await self.http.get(url)
            if not isinstance(html, str) or not html.strip():
                break

            page_offers, total_paginas = self._parse_nuxt_state(html, base_url)
            nuevas = 0
            for oferta in page_offers:
                if oferta.url not in seen_ids:
                    seen_ids.add(oferta.url)
                    offers.append(oferta)
                    nuevas += 1

            self.log.info(
                "evento=listado_obtenido scraper=trabajando pagina=%s cantidad_urls=%s total_paginas_hint=%s",
                pagina,
                len(page_offers),
                total_paginas,
            )
            if nuevas == 0 or pagina >= total_paginas:
                break

        enriched = await self._enrich_with_detail_pages(offers)
        self.log.info(
            "evento=fin scraper=trabajando fuente=%s total=%s validas=%s descartadas=%s",
            self.nombre_fuente,
            len(offers),
            len(enriched),
            max(0, len(offers) - len(enriched)),
        )
        return enriched

    async def _enrich_with_detail_pages(self, offers: list[OfertaRaw]) -> list[OfertaRaw]:
        if self.http is None:
            return offers
        enriched: list[OfertaRaw] = []
        for idx, offer in enumerate(offers):
            if idx >= self.detail_fetch_limit:
                enriched.append(offer)
                continue
            html = await self.http.get(offer.url)
            if not isinstance(html, str) or not html.strip():
                enriched.append(offer)
                continue
            parsed = self._parse_detail_page(html, offer)
            if parsed is None:
                self.log.info(
                    "evento=oferta_descartada scraper=trabajando razon=detalle_invalido url=%s",
                    offer.url,
                )
                continue
            self.log.info(
                "evento=detalle_parseado scraper=trabajando url=%s cargo=%s",
                parsed.url,
                parsed.cargo[:120],
            )
            enriched.append(parsed)
        return enriched

    def _parse_detail_page(self, html: str, fallback: OfertaRaw) -> OfertaRaw | None:
        soup = BeautifulSoup(html, "html.parser")
        cargo = self._clean_trabajando_text(self._extract_detail_title(soup)) or fallback.cargo
        if not self._is_valid_cargo(cargo):
            return None

        company = self._extract_company(soup) or fallback.institucion_nombre
        ubicacion = self._extract_label_value(soup, ("Ubicación", "Lugar", "Comuna", "Ciudad"))
        region = self._region_from_ubicacion(ubicacion) or fallback.region
        ciudad = self._ciudad_from_ubicacion(ubicacion) or fallback.ciudad
        fecha_pub_raw = self._extract_label_value(
            soup, ("Fecha publicación", "Publicado", "Publicación")
        )
        fecha_cierre_raw = self._extract_label_value(
            soup, ("Fecha cierre", "Cierre", "Postulaciones hasta", "Fecha límite")
        )

        descripcion = self._extract_detail_description(soup) or fallback.descripcion or ""
        descripcion = self._clean_trabajando_text(descripcion)
        if len(descripcion) < 30:
            return None

        return OfertaRaw(
            url=fallback.url,
            cargo=cargo,
            institucion_nombre=company,
            descripcion=descripcion,
            sector=fallback.sector,
            tipo_cargo=normalize_tipo_contrato(
                f"{cargo} {self._extract_label_value(soup, ('Tipo contrato', 'Contrato', 'Jornada'))}"
            ),
            region=region,
            ciudad=ciudad,
            renta_texto=self._extract_label_value(soup, ("Sueldo", "Renta", "Salario")) or fallback.renta_texto,
            renta_min=fallback.renta_min,
            renta_max=fallback.renta_max,
            grado_eus=fallback.grado_eus,
            fecha_publicacion=parse_date(fecha_pub_raw) or fallback.fecha_publicacion,
            fecha_cierre=parse_date(fecha_cierre_raw) or fallback.fecha_cierre,
            area_profesional=fallback.area_profesional,
            url_bases=fallback.url_bases,
        )

    def _extract_detail_title(self, soup: BeautifulSoup) -> str:
        h1 = soup.select_one("h1")
        if h1:
            return h1.get_text(" ", strip=True)
        og_title = soup.select_one("meta[property='og:title']")
        if og_title and og_title.get("content"):
            return str(og_title.get("content"))
        title = soup.select_one("title")
        if title:
            return title.get_text(" ", strip=True)
        return ""

    def _extract_company(self, soup: BeautifulSoup) -> str:
        selectors = (
            "[class*='company']",
            "[class*='empresa']",
            "[data-testid*='company']",
            "[data-testid*='empresa']",
        )
        for sel in selectors:
            node = soup.select_one(sel)
            if node:
                text = self._clean_trabajando_text(node.get_text(" ", strip=True))
                if text and self._is_valid_cargo(text):
                    return text
        return self._extract_label_value(soup, ("Empresa", "Institución", "Institucion")) or ""

    def _extract_label_value(self, soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
        for label in labels:
            pattern = re.compile(rf"^{re.escape(label)}\s*:?\s*$", re.IGNORECASE)
            for node in soup.find_all(string=pattern):
                parent = node.parent
                if not parent:
                    continue
                sibling = parent.find_next_sibling()
                if sibling:
                    val = self._clean_trabajando_text(sibling.get_text(" ", strip=True))
                    if val:
                        return val
                text = self._clean_trabajando_text(parent.get_text(" ", strip=True))
                text = re.sub(rf"^{re.escape(label)}\s*:?\s*", "", text, flags=re.IGNORECASE)
                if text:
                    return text
        return ""

    def _extract_detail_description(self, soup: BeautifulSoup) -> str:
        selectors = (
            "main [class*='description']",
            "main [class*='detalle']",
            "main [class*='content']",
            "#job-description",
            ".job-description",
            "article",
        )
        for sel in selectors:
            node = soup.select_one(sel)
            if not node:
                continue
            text = node.get_text("\n", strip=True)
            cleaned = self._clean_trabajando_text(text)
            if len(cleaned) >= 60:
                return cleaned
        return ""

    def _clean_trabajando_text(self, text: str | None) -> str:
        raw = (text or "").replace("\xa0", " ")
        if not raw:
            return ""
        lines = []
        seen: set[str] = set()
        for line in re.split(r"\n+|\r+", raw):
            item = clean_text(line)
            if not item:
                continue
            low = item.lower()
            if low in _NOISE_PHRASES:
                continue
            if re.fullmatch(r"[\W_]+", item):
                continue
            if len(item) > 200 and not re.search(r"[.!?:;]", item):
                continue
            if low in seen:
                continue
            seen.add(low)
            lines.append(item)
        return "\n".join(lines).strip()

    def _is_valid_cargo(self, title: str) -> bool:
        candidate = clean_text(title)
        if len(candidate) < 4:
            return False
        normalized = candidate.lower()
        if any(re.fullmatch(pattern, normalized) for pattern in _INVALID_CARGO_PATTERNS):
            return False
        return bool(re.search(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]", candidate))

    async def _resolve_base_url(self) -> str:
        """
        Resuelve la URL base ATS para scraping.

        Prioridad:
        1) url_empleo/sitio_web ya en trabajando.cl
        2) descubrir enlace trabajando.cl desde página institucional
        """
        direct = self._canonical_base()
        if direct:
            return direct
        discovered = await self._discover_base_from_institutional_site()
        return discovered.rstrip("/") if discovered else ""

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
        for s in scripts:
            if (
                "ShallowReactive" in s
                or "__NUXT" in s
                or "cantidadPaginas" in s
                or "oferta" in s.lower()
            ):
                nuxt_state = s
                break

        if not nuxt_state:
            # Segundo intento: buscar en todo el HTML cuando el estado viene
            # serializado fuera del primer script relevante.
            if "cantidadPaginas" in html or re.search(r"(\d{6,8}),\"([^\"]{6,180})\"", html):
                nuxt_state = html
            else:
                # Fallback: parseo HTML genérico
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
        pairs = re.findall(r"(\d{6,8}),\"([^\"]{6,180})\"", nuxt_state)
        # Filtrar ruido: solo pares cuyo título tiene letras
        pairs = [(id_v, t) for id_v, t in pairs if self._is_valid_cargo(t)]

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
                    cargo=self._clean_trabajando_text(nombre_cargo),
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
            html = await self.http.get(seed)
            if not isinstance(html, str) or not html.strip():
                continue
            discovered = self._extract_trabajando_url_from_html(html, seed)
            if discovered:
                return discovered
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
        return f"{domain_base}/oferta/{id_oferta}"

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
        for card in soup.select("article, .job-card, .oferta-card, .card"):
            title_el = card.select_one("h2, h3, .job-title, .titulo")
            if not title_el:
                continue
            title = clean_text(title_el.get_text(" ", strip=True))
            link = card.select_one("a[href]")
            href = clean_text(link.get("href") if link else "")
            url = urljoin(base_url, href) if href else base_url
            is_offer, _ = self._score_offer_candidate(title, "", url=url)
            if not title or not is_offer:
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
        return offers
