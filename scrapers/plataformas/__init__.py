"""Scrapers genericos y adaptadores por plataforma."""

from .buk import BukScraper
from .carabineros import CarabinerosScraper
from .ffaa import FfaaScraper
from .generic_site import GenericSiteScraper
from .hiringroom import HiringRoomScraper
from .pdi import PdiScraper
from .playwright_scraper import PlaywrightScraper
from .trabajando_cl import TrabajandoCLScraper
from .wordpress import WordPressScraper

__all__ = [
    "BukScraper",
    "CarabinerosScraper",
    "FfaaScraper",
    "GenericSiteScraper",
    "HiringRoomScraper",
    "PdiScraper",
    "PlaywrightScraper",
    "TrabajandoCLScraper",
    "WordPressScraper",
]
