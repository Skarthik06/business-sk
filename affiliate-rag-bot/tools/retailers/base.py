"""
tools/retailers/base.py  —  Retailer adapter interface (Phase 8).

Every retailer implements this so the rest of the system treats products uniformly.
Methods are async where they hit the network; normalization is deterministic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class RetailerAdapter(ABC):
    #: short id, e.g. "amazon"
    key: str = "base"
    #: marketplace/domain, e.g. "amazon.in"
    marketplace: str = ""

    @abstractmethod
    async def search(self, page, category: str, query: Optional[str] = None,
                     quality: Optional[dict] = None) -> list[dict]:
        """Return normalized ProductCandidate dicts for a query/category."""

    @abstractmethod
    def normalize_product(self, raw: dict) -> dict:
        """Map a retailer-specific raw product into the common contract."""

    @abstractmethod
    async def build_affiliate_link(self, page, product: dict) -> str:
        """Return a verified affiliate URL built from the real product id (never invented)."""

    @abstractmethod
    def health_check(self) -> bool:
        """True when this adapter can actually fetch + build links right now."""
