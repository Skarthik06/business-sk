"""
tools/retailers/amazon.py  —  Amazon adapter (Phase 8).

Wraps the existing, proven Amazon scraper + deep-link builder behind the RetailerAdapter
interface. The live content pipeline still calls tools/amazon directly; this adapter makes
Amazon a first-class retailer the multi-retailer layer + AffiliateLinkService can use, and is
the template every future retailer follows.
"""
from __future__ import annotations

from typing import Optional

from config import cfg
from .base import RetailerAdapter


class AmazonAdapter(RetailerAdapter):
    key = "amazon"

    @property
    def marketplace(self) -> str:
        return cfg.amazon.marketplace

    async def search(self, page, category: str, query: Optional[str] = None,
                     quality: Optional[dict] = None) -> list[dict]:
        from tools.amazon import scrape_products
        products = await scrape_products(page, category, self.marketplace, query=query, quality=quality)
        return [self.normalize_product(p) for p in products]

    def normalize_product(self, raw: dict) -> dict:
        """Amazon scraper already emits the common shape; stamp retailer + marketplace."""
        return {**raw, "retailer": "amazon", "marketplace": self.marketplace}

    async def build_affiliate_link(self, page, product: dict) -> str:
        from tools.amazon import get_affiliate_link
        return await get_affiliate_link(page, product, cfg.amazon.associate_tag, self.marketplace)

    def health_check(self) -> bool:
        # Amazon needs no API key for deep-link mode; scraping is public.
        return True
