from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Iterable

from .models import QualityDecision, QualityValidationResult
from .reason_codes import ReasonCode, reason_detail


PRIVACY_PLACEHOLDER_URLS = {
    "https://www.empleospublicos.cl/documentos/politicaprivacidad.pdf",
}

LANDING_PAGE_TITLES = {
    "concursos",
    "concursos abiertos",
    "concursos cerrados",
    "concursos abiertos/cerrados",
    "licitaciones",
    "tramites",
    "feria",
}

NEGATIVE_TEXT_MARKERS = (
    "subsidio",
    "beca",
    "fondo concursable",
    "noticia",
    "actividad",
    "taller",
    "operativo",
    "beneficio",
    "tramite",
    "cuenta publica",
    "licitacion",
    "feria",
    "jefas de hogar",
)


def _norm(value: Any) -> str:
    if value in (None, ""):
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    return " ".join(normalized.encode("ascii", "ignore").decode("ascii").lower().split())


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(re.sub(r"[^\d]", "", str(value)))
    except ValueError:
        return None


def build_duplicate_fingerprint(oferta: dict[str, Any]) -> str:
    token = "|".join(
        [
            str(oferta.get("institucion_id") or oferta.get("institucion_nombre") or ""),
            _norm(oferta.get("cargo")),
            str(oferta.get("fecha_cierre") or ""),
        ]
    )
    return hashlib.sha1(token.encode("utf-8")).hexdigest()


class QualityValidator:
    def __init__(self, *, valid_institution_ids: Iterable[int] | None = None) -> None:
        self.valid_institution_ids = set(valid_institution_ids or [])

    def validate(self, oferta: dict[str, Any], *, seen_fingerprints: set[str] | None = None, today: date | None = None) -> QualityValidationResult:
        today = today or date.today()
        reasons: list[ReasonCode] = []
        detail_parts: list[str] = []
        score = 1.0

        cargo = _norm(oferta.get("cargo"))
        institucion_nombre = _norm(oferta.get("institucion_nombre"))
        institucion_id = oferta.get("institucion_id")
        descripcion = _norm(oferta.get("descripcion") or oferta.get("requisitos"))
        titulo = cargo
        url_bases = str(oferta.get("url_bases") or "").strip()
        fecha_publicacion = _as_date(oferta.get("fecha_publicacion"))
        fecha_cierre = _as_date(oferta.get("fecha_cierre"))
        renta_min = _as_int(oferta.get("renta_bruta_min") or oferta.get("renta_min"))
        renta_max = _as_int(oferta.get("renta_bruta_max") or oferta.get("renta_max"))
        estado = _norm(oferta.get("estado") or ("activo" if oferta.get("activa") else ""))

        if not cargo:
            reasons.append(ReasonCode.EXTRACTOR_FAILED_VALIDATION)
            detail_parts.append("Falta cargo.")
            score -= 0.5

        if not institucion_nombre and not institucion_id:
            reasons.append(ReasonCode.INVALID_INSTITUTION_REFERENCE)
            detail_parts.append("Falta referencia de institucion.")
            score -= 0.5

        if institucion_id not in (None, "") and self.valid_institution_ids and institucion_id not in self.valid_institution_ids:
            reasons.append(ReasonCode.INVALID_INSTITUTION_REFERENCE)
            detail_parts.append(f"institucion_id fuera de catalogo: {institucion_id}.")
            score -= 0.7

        if titulo in LANDING_PAGE_TITLES:
            reasons.append(ReasonCode.LISTING_PAGE_ONLY)
            detail_parts.append("La publicacion parece una portada de listados.")
            score -= 0.8

        if any(marker in f"{titulo} {descripcion}" for marker in NEGATIVE_TEXT_MARKERS):
            reasons.append(ReasonCode.NOT_JOB_RELATED)
            detail_parts.append("El contenido parece noticia, beneficio o actividad no laboral.")
            score -= 0.7

        if url_bases and url_bases.lower() in PRIVACY_PLACEHOLDER_URLS:
            reasons.append(ReasonCode.PLACEHOLDER_BASES_URL)
            detail_parts.append("url_bases apunta a politicaprivacidad.pdf.")
            score -= 0.8

        if fecha_publicacion and fecha_cierre and fecha_cierre < fecha_publicacion:
            reasons.append(ReasonCode.EXTRACTOR_FAILED_VALIDATION)
            detail_parts.append("fecha_cierre es anterior a fecha_publicacion.")
            score -= 0.5

        if fecha_cierre and fecha_cierre < today:
            if estado == "activo":
                reasons.append(ReasonCode.STALE_ACTIVE_OFFER)
                detail_parts.append("La oferta sigue activa con fecha_cierre vencida.")
            else:
                reasons.append(ReasonCode.CLOSING_DATE_PASSED)
                detail_parts.append("fecha_cierre ya paso.")
            score -= 0.8

        if renta_max is not None and (renta_max > 15_000_000 or renta_max < 250_000):
            reasons.append(ReasonCode.SALARY_OUTLIER)
            detail_parts.append(f"renta_max fuera de rango: {renta_max}.")
            score -= 0.7
        elif renta_min is not None and renta_min < 250_000:
            reasons.append(ReasonCode.SALARY_OUTLIER)
            detail_parts.append(f"renta_min fuera de rango: {renta_min}.")
            score -= 0.4

        if seen_fingerprints is not None:
            fingerprint = build_duplicate_fingerprint(oferta)
            if fingerprint in seen_fingerprints:
                reasons.append(ReasonCode.DUPLICATE_CANDIDATE)
                detail_parts.append("Fingerprint duplicado de institucion+cargo+fecha_cierre.")
                score -= 0.7
            else:
                seen_fingerprints.add(fingerprint)

        primary = reasons[0] if reasons else None
        if primary in {
            ReasonCode.PLACEHOLDER_BASES_URL,
            ReasonCode.INVALID_INSTITUTION_REFERENCE,
            ReasonCode.STALE_ACTIVE_OFFER,
            ReasonCode.LISTING_PAGE_ONLY,
            ReasonCode.NOT_JOB_RELATED,
            ReasonCode.CLOSING_DATE_PASSED,
        }:
            decision = QualityDecision.REJECT
        elif primary in {ReasonCode.SALARY_OUTLIER, ReasonCode.DUPLICATE_CANDIDATE, ReasonCode.EXTRACTOR_FAILED_VALIDATION}:
            decision = QualityDecision.MANUAL_REVIEW if score >= 0.35 else QualityDecision.REJECT
        else:
            decision = QualityDecision.PUBLISH

        return QualityValidationResult(
            decision=decision,
            reason_codes=reasons,
            reason_detail=" ".join(detail_parts) if detail_parts else None,
            quality_score=max(0.0, round(score, 4)),
            signals_json={
                "cargo_present": bool(cargo),
                "institucion_present": bool(institucion_nombre or institucion_id),
                "fecha_publicacion": fecha_publicacion.isoformat() if fecha_publicacion else None,
                "fecha_cierre": fecha_cierre.isoformat() if fecha_cierre else None,
                "url_bases": url_bases or None,
                "renta_min": renta_min,
                "renta_max": renta_max,
                "primary_reason_detail": reason_detail(primary),
            },
        )
