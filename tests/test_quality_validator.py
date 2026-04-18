from __future__ import annotations

from datetime import date

from scrapers.evaluation.models import QualityDecision
from scrapers.evaluation.reason_codes import ReasonCode
from scrapers.evaluation.quality_validator import QualityValidator


def test_landing_page_concursos_is_not_publishable():
    validator = QualityValidator(valid_institution_ids={1})
    result = validator.validate(
        {
            "institucion_id": 1,
            "institucion_nombre": "Municipalidad X",
            "cargo": "Concursos",
            "descripcion": "Listado general de concursos y noticias.",
            "fecha_publicacion": "2026-04-10",
            "estado": "activo",
        }
    )
    assert result.decision == QualityDecision.REJECT
    assert ReasonCode.LISTING_PAGE_ONLY in result.reason_codes


def test_placeholder_privacy_pdf_is_invalid_url_bases():
    validator = QualityValidator(valid_institution_ids={1})
    result = validator.validate(
        {
            "institucion_id": 1,
            "institucion_nombre": "Servicio X",
            "cargo": "Analista",
            "descripcion": "Convocatoria a contrata.",
            "fecha_publicacion": "2026-04-10",
            "url_bases": "https://www.empleospublicos.cl/documentos/politicaprivacidad.pdf",
        }
    )
    assert result.decision == QualityDecision.REJECT
    assert ReasonCode.PLACEHOLDER_BASES_URL in result.reason_codes


def test_invalid_institution_id_is_rejected():
    validator = QualityValidator(valid_institution_ids={1, 2, 3})
    result = validator.validate(
        {
            "institucion_id": 705,
            "institucion_nombre": "Institucion Fantasma",
            "cargo": "Profesional",
            "descripcion": "Cargo a contrata",
        }
    )
    assert result.decision == QualityDecision.REJECT
    assert ReasonCode.INVALID_INSTITUTION_REFERENCE in result.reason_codes


def test_salary_outlier_is_rejected():
    validator = QualityValidator(valid_institution_ids={1})
    result = validator.validate(
        {
            "institucion_id": 1,
            "institucion_nombre": "Municipalidad Y",
            "cargo": "Ingeniero",
            "descripcion": "Cargo profesional.",
            "renta_bruta_max": 18000000,
        }
    )
    assert result.decision == QualityDecision.REJECT
    assert ReasonCode.SALARY_OUTLIER in result.reason_codes


def test_stale_active_offer_is_rejected():
    validator = QualityValidator(valid_institution_ids={1})
    result = validator.validate(
        {
            "institucion_id": 1,
            "institucion_nombre": "Servicio Civil",
            "cargo": "Abogado",
            "descripcion": "Concurso a contrata.",
            "fecha_cierre": "2026-04-01",
            "estado": "activo",
        },
        today=date(2026, 4, 18),
    )
    assert result.decision == QualityDecision.REJECT
    assert ReasonCode.STALE_ACTIVE_OFFER in result.reason_codes
