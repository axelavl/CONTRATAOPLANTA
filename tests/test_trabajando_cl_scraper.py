from __future__ import annotations

from scrapers.plataformas.trabajando_cl import TrabajandoCLScraper


def _make_scraper(url_empleo: str = "https://demo.trabajando.cl/trabajo-empleo") -> TrabajandoCLScraper:
    inst = {
        "id": 9999,
        "nombre": "Institución Demo",
        "url_empleo": url_empleo,
        "sitio_web": "https://www.demo.gob.cl/trabaja-con-nosotros",
        "sector": "Autónomo",
        "region": "Nacional",
    }
    return TrabajandoCLScraper(fuente_id=9999, institucion=inst)


def test_parse_nuxt_state_extracts_offers_and_total_pages():
    scraper = _make_scraper()
    html = """
    <html><body>
      <script>
        window.__NUXT__={cantidadPaginas: 3, data:[
          1234567,\"Analista de Datos\",\"x\",\"y\",\"Santiago, Región Metropolitana\",\"2026-04-21 10:30\",
          2345678,\"Abogado\",\"x\",\"y\",\"Valparaíso, Región de Valparaíso\",\"2026-04-20 09:00\"
        ]};
      </script>
    </body></html>
    """

    offers, total_pages = scraper._parse_nuxt_state(html, "https://demo.trabajando.cl/trabajo-empleo")

    assert total_pages == 3
    assert len(offers) == 2
    assert offers[0].url == "https://demo.trabajando.cl/oferta/1234567"
    assert offers[0].cargo == "Analista de Datos"
    assert offers[1].url == "https://demo.trabajando.cl/oferta/2345678"


def test_extract_trabajando_url_from_html_uses_link_path_if_present():
    scraper = _make_scraper(url_empleo="https://www.demo.gob.cl/trabaja")
    html = """
    <html><body>
      <a href="https://fiscaliadechile.trabajando.cl/trabajo-empleo">Postula aquí</a>
    </body></html>
    """

    discovered = scraper._extract_trabajando_url_from_html(html, "https://www.demo.gob.cl/trabaja")

    assert discovered == "https://fiscaliadechile.trabajando.cl/trabajo-empleo"


def test_extract_trabajando_url_from_html_adds_default_path_when_missing():
    scraper = _make_scraper(url_empleo="https://www.demo.gob.cl/trabaja")
    html = """
    <html><body>
      <a href="https://fiscaliadechile.trabajando.cl">Portal de empleos</a>
    </body></html>
    """

    discovered = scraper._extract_trabajando_url_from_html(html, "https://www.demo.gob.cl/trabaja")

    assert discovered == "https://fiscaliadechile.trabajando.cl/trabajo-empleo"


def test_parse_nuxt_state_discards_button_labels_as_cargo():
    scraper = _make_scraper()
    html = """
    <html><body><script>
      window.__NUXT__={cantidadPaginas: 1, data:[
        1234567,\"Ver Detalle\",\"x\",\"y\",\"Santiago, Región Metropolitana\",\"2026-04-21 10:30\",
        1234568,\"Analista Contable\",\"x\",\"y\",\"Santiago, Región Metropolitana\",\"2026-04-21 10:30\"
      ]};
    </script></body></html>
    """
    offers, _ = scraper._parse_nuxt_state(html, "https://demo.trabajando.cl/trabajo-empleo")
    assert len(offers) == 1
    assert offers[0].cargo == "Analista Contable"


def test_clean_trabajando_text_removes_noise_but_preserves_content():
    scraper = _make_scraper()
    noisy = """
    Ingresa
    Crea tu cuenta
    Funciones del cargo:
    - Liderar conciliaciones
    Requisitos:
    - 2 años de experiencia
    Ver Detalle
    Postular
    """
    cleaned = scraper._clean_trabajando_text(noisy)
    assert "Ingresa" not in cleaned
    assert "Crea tu cuenta" not in cleaned
    assert "Ver Detalle" not in cleaned
    assert "Funciones del cargo:" in cleaned
    assert "Requisitos:" in cleaned


def test_parse_detail_page_extracts_main_fields():
    scraper = _make_scraper()
    fallback_offer, _ = scraper._parse_nuxt_state(
        """
        <html><body><script>
          window.__NUXT__={cantidadPaginas: 1, data:[
            1234567,\"Analista de Datos\",\"x\",\"y\",\"Santiago, Región Metropolitana\",\"2026-04-21 10:30\"
          ]};
        </script></body></html>
        """,
        "https://demo.trabajando.cl/trabajo-empleo",
    )
    detail_html = """
    <html><head><title>Analista de Datos</title></head><body>
      <main>
        <h1>Analista de Datos</h1>
        <div class="company-name">Empresa Demo</div>
        <div>Ubicación</div><div>Pudahuel, Región Metropolitana</div>
        <div class="job-description">
          Funciones del cargo:
          Crear reportes de gestión.
          Requisitos:
          SQL avanzado y Python.
          Ingresa
          Ver Detalle
        </div>
      </main>
    </body></html>
    """
    parsed = scraper._parse_detail_page(detail_html, fallback_offer[0])
    assert parsed is not None
    assert parsed.cargo == "Analista de Datos"
    assert parsed.institucion_nombre == "Empresa Demo"
    assert parsed.ciudad == "Pudahuel"
    assert "Funciones del cargo" in parsed.descripcion
    assert "Ver Detalle" not in parsed.descripcion
