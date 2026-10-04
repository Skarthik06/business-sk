"""One-time backfill: point existing affiliate posts' DM-card product images at the
GitHub-raw copies (IG-fetchable) instead of the Amazon CDN URLs (which IG can't fetch).

The carousel slides for these posts were already re-hosted at post time under the
deterministic name sk_media/<md5(hi_res_url)>.jpg, so we reconstruct that raw URL and
verify it serves (200). Anything missing is re-hosted on the spot. Safe to re-run."""
import hashlib
import json
import os

import requests

from app import settings
from app.engagement import store
from app.services import hosting
from app.api import _hi_res, _rehost_for_ig

ACCOUNT_ID = int(os.getenv("BACKFILL_ACCOUNT_ID", "8"))


def _existing_raw(hi_url: str) -> str | None:
    """The raw URL a hi-res image WOULD have if it was re-hosted before. None if it 404s."""
    user, repo, branch = hosting._git_cfg()
    if not (user and repo):
        return None
    rel = f"sk_media/{hashlib.md5(hi_url.encode()).hexdigest()}.jpg"
    url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{rel}"
    try:
        if requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200:
            return url
    except Exception:
        pass
    return None


def main() -> None:
    posts = store.list_affiliate_posts(ACCOUNT_ID, with_products=True)
    print(f"[backfill] account={ACCOUNT_ID} posts={len(posts)}")
    for post in posts:
        prods = post.get("products")
        if isinstance(prods, str):
            prods = json.loads(prods or "[]")
        if not prods:
            continue
        need_rehost: list[tuple[dict, str]] = []
        changed = False
        for p in prods:
            cur = p.get("image_url") or p.get("image") or ""
            if not cur:
                continue
            if "raw.githubusercontent.com" in cur:
                continue                                  # already good
            hi = _hi_res(cur)
            raw = _existing_raw(hi)
            if raw:
                p["image_url"] = raw
                changed = True
            else:
                need_rehost.append((p, hi))
        if need_rehost:
            rehosted = _rehost_for_ig([hi for _, hi in need_rehost])
            for (p, _), new in zip(need_rehost, rehosted):
                if new and "raw.githubusercontent.com" in new:
                    p["image_url"] = new
                    changed = True
        if changed:
            store.register_affiliate_post(
                ACCOUNT_ID, post["ig_media_id"],
                category=post.get("category") or "", caption=post.get("caption") or "",
                permalink=post.get("permalink"),
                media_type=post.get("media_type") or "CAROUSEL_ALBUM", products=prods)
            fixed = sum(1 for p in prods if "raw.githubusercontent.com" in (p.get("image_url") or ""))
            print(f"[backfill] {post['ig_media_id']}: {fixed}/{len(prods)} cards on GitHub raw")
        else:
            print(f"[backfill] {post['ig_media_id']}: no change")
    print("[backfill] done")


if __name__ == "__main__":
    main()
