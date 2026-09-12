"""
tools/affiliate.py  —  Affiliate Link Service (Phase 8).

One place to build a tracked link for ANY retailer. It (1) routes to the retailer's adapter,
(2) builds from the real product id/URL, (3) validates the result, (4) records the source
retailer, and (5) NEVER invents tracking parameters (G13/blueprint §49).
"""
from __future__ import annotations

from tools.retailers import get_adapter
from utils.logger import log


def _valid(url: str) -> bool:
    return isinstance(url, str) and url.startswith("http") and len(url) > 12


class AffiliateLinkService:
    async def create_link(self, retailer: str, product: dict, page=None) -> dict:
        """Return {ok, retailer, url, error}. Fails closed (ok:false) rather than
        returning a fabricated/untracked link."""
        adapter = get_adapter(retailer)
        if adapter is None:
            return {"ok": False, "retailer": retailer, "url": "",
                    "error": f"retailer '{retailer}' not enabled/implemented"}
        try:
            url = await adapter.build_affiliate_link(page, product)
        except Exception as e:
            log.warning(f"[affiliate] link build failed for {retailer}: {e}")
            return {"ok": False, "retailer": retailer, "url": "", "error": str(e)}
        if not _valid(url):
            return {"ok": False, "retailer": retailer, "url": url or "",
                    "error": "invalid affiliate URL"}
        return {"ok": True, "retailer": retailer, "url": url, "error": None}


affiliate_link_service = AffiliateLinkService()
