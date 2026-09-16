"""Instagram Graph API publisher — multi-account, single image + carousel.

Each call receives a full account record (with its own business id + token)
from the rags store, so multiple accounts can be posted to independently.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import requests

from app import settings

# Instagram Graph API base — version is env-configurable (GRAPH_API_VERSION, default v26.0).
GRAPH = f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}"


class InstagramError(RuntimeError):
    pass


def _looks_like_placeholder(token: Optional[str]) -> bool:
    if not token:
        return True
    low = token.strip().lower()
    if len(low) < 80:
        return True
    return any(m in low for m in ("token_here", "your_", "replace", "<", ">", "xxx"))


def _validate(account: Dict) -> None:
    if _looks_like_placeholder(account.get("ig_access_token")):
        raise InstagramError(
            f"Account '{account.get('label')}' has a missing/placeholder access token. "
            "Set a valid Instagram Graph token in the Settings panel."
        )
    biz = str(account.get("ig_business_id") or "")
    if not biz.isdigit():
        raise InstagramError(
            f"Account '{account.get('label')}' has an invalid Business ID "
            f"({biz!r}); it must be the numeric IG Business Account ID."
        )


def _post(url: str, data: Dict, timeout: int = 30) -> Dict:
    resp = requests.post(url, data=data, timeout=timeout)
    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass
    if resp.status_code >= 400 or "error" in body:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        parts = [str(err.get("message") or resp.text)]
        if err.get("code") is not None:
            parts.append(f"code={err.get('code')}")
        if err.get("error_subcode") is not None:
            parts.append(f"subcode={err.get('error_subcode')}")
        if err.get("error_user_msg"):
            parts.append(str(err.get("error_user_msg")))
        raise InstagramError("Graph API error: " + " | ".join(parts))
    return body


def fetch_username(account: Dict) -> Optional[str]:
    """Look up the real IG @username for an account via the Graph API.

    `GET /{ig-business-id}?fields=username` returns the handle tied to the
    business account, so the slide overlay can use the genuine username
    instead of a manually-typed value. Returns None on any error so callers
    can fall back gracefully (never raises).
    """
    ig_id = str(account.get("ig_business_id") or "")
    token = account.get("ig_access_token") or ""
    if not ig_id.isdigit() or _looks_like_placeholder(token):
        return None
    try:
        r = requests.get(
            f"{GRAPH}/{ig_id}",
            params={"fields": "username", "access_token": token},
            timeout=15,
        )
        data = r.json()
        username = data.get("username") if isinstance(data, dict) else None
        return str(username).strip() if username else None
    except Exception:
        return None


def _permalink(media_id: str, token: str) -> Optional[str]:
    try:
        r = requests.get(
            f"{GRAPH}/{media_id}",
            params={"fields": "permalink", "access_token": token},
            timeout=15,
        )
        return r.json().get("permalink")
    except Exception:
        return None


def resolve_page_token(token: str, ig_business_id: str) -> str:
    """Return the PAGE access token for the Facebook Page connected to this IG business
    account. Instagram messaging (comment→DM) and publishing must use the PAGE token, not the
    User token — a User token gives '(#3) capability' / 'Object me does not exist' on
    /messages. If `token` is already a page token or resolution fails, the original is returned
    unchanged (so nothing breaks)."""
    ig = str(ig_business_id or "").strip()
    if _looks_like_placeholder(token) or not ig:
        return token
    try:
        r = requests.get(f"{GRAPH}/me/accounts",
                         params={"fields": "id,access_token,instagram_business_account",
                                 "access_token": token}, timeout=20)
        for pg in (r.json() or {}).get("data", []):
            iba = (pg.get("instagram_business_account") or {}).get("id")
            if iba and str(iba) == ig and pg.get("access_token"):
                return pg["access_token"]                 # the Page token — what messaging needs
    except Exception:
        pass
    return token


def account_info(account: Dict) -> Dict:
    """Public profile metrics for the account's right-side panel: username, name,
    followers/following/posts counts, avatar, bio. Returns {} on any error (never raises)."""
    ig_id = str(account.get("ig_business_id") or "")
    token = account.get("ig_access_token") or ""
    if not ig_id.isdigit() or _looks_like_placeholder(token):
        return {}
    try:
        r = requests.get(f"{GRAPH}/{ig_id}", params={
            "fields": "username,name,followers_count,follows_count,media_count,profile_picture_url,biography,website",
            "access_token": token}, timeout=15)
        d = r.json()
        if isinstance(d, dict) and "error" in d:
            return {"_error": str(d["error"].get("message", "unavailable"))[:300]}
        return d if isinstance(d, dict) else {}
    except Exception as e:
        return {"_error": str(e)[:200]}


def list_stories(account: Dict) -> List[Dict]:
    """Active Stories (last 24h) for the account — the /stories edge. [] on error.
    NOTE: Highlights are deliberately absent — the Graph API exposes NO Highlights edge."""
    ig_id = str(account.get("ig_business_id") or "")
    token = account.get("ig_access_token") or ""
    if not ig_id.isdigit() or _looks_like_placeholder(token):
        return []
    try:
        r = requests.get(f"{GRAPH}/{ig_id}/stories", params={
            "fields": "id,media_type,media_url,thumbnail_url,permalink,timestamp",
            "access_token": token}, timeout=15)
        d = r.json()
        return d.get("data", []) if isinstance(d, dict) else []
    except Exception:
        return []


def _wait_ready(creation_id: str, token: str, tries: int = 20, delay: float = 2.0) -> None:
    """Poll a media container's status_code until FINISHED before publishing. Instagram
    processes containers (esp. carousels / re-hosted images) asynchronously; publishing too
    early yields Graph error 2207027 'media not ready'. Best-effort: on ERROR raise, otherwise
    proceed after the last try (publish will surface a real error if still not ready)."""
    import time as _t
    for _ in range(tries):
        try:
            st = requests.get(f"{GRAPH}/{creation_id}",
                              params={"fields": "status_code", "access_token": token}, timeout=15).json()
        except Exception:
            st = {}
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise InstagramError("Instagram could not process the media (check the image URL is public + valid).")
        _t.sleep(delay)


def _publish_container(ig_id: str, creation_id: str, token: str) -> Dict:
    """Publish a ready container, retrying briefly on the transient 'media not ready' error."""
    import time as _t
    for attempt in range(6):
        try:
            return _post(f"{GRAPH}/{ig_id}/media_publish",
                         {"creation_id": creation_id, "access_token": token})
        except InstagramError as e:
            if "2207027" in str(e) or "not ready" in str(e).lower():
                _t.sleep(3)                       # still processing — wait and retry
                continue
            raise
    # final attempt (let the real error surface)
    return _post(f"{GRAPH}/{ig_id}/media_publish", {"creation_id": creation_id, "access_token": token})


def publish_story(account: Dict, media_url: str, is_video: bool = False) -> Dict:
    """Publish a single-media STORY (image or video) to the account. Graph API supports
    Stories via media_type=STORIES for Business/Creator accounts. Instagram-catalog music
    CANNOT be attached via API — only audio already baked into an uploaded video counts."""
    _validate(account)
    if not media_url:
        raise InstagramError("No media URL for the story.")
    ig_id = account["ig_business_id"]
    token = account["ig_access_token"]
    field = "video_url" if is_video else "image_url"
    created = _post(f"{GRAPH}/{ig_id}/media",
                    {"media_type": "STORIES", field: media_url, "access_token": token})
    creation_id = created["id"]
    _wait_ready(creation_id, token)                       # wait for processing (image or video)
    published = _publish_container(ig_id, creation_id, token)
    media_id = published["id"]
    return {"ig_media_id": media_id, "permalink": _permalink(media_id, token), "media_type": "story"}


def _create_child(ig_id: str, url: str, token: str, attempts: int = 5) -> Dict:
    """Create ONE carousel child, retrying the transient 'media could not be fetched' error
    (code 9004 / subcode 2207052). That error means Meta's fetcher hit a GitHub-raw edge that
    hadn't propagated the just-pushed slide yet — the image IS valid, so a short backoff lets it
    catch up. Any other error is raised immediately."""
    import time as _t
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return _post(
                f"{GRAPH}/{ig_id}/media",
                {"image_url": url, "is_carousel_item": "true", "access_token": token},
            )
        except InstagramError as e:
            last = e
            msg = str(e).lower()
            transient = ("2207052" in msg or "could not be fetched" in msg or "9004" in msg)
            if transient and i < attempts - 1:
                _t.sleep(5 * (i + 1))                 # 5s, 10s, 15s, 20s — let GitHub raw propagate
                continue
            raise
    raise last  # pragma: no cover


def publish(account: Dict, image_urls: List[str], caption: str) -> Dict:
    """Publish a post to `account`. Carousel if >1 image, else a single image.

    Returns {"ig_media_id", "permalink", "media_type"}.
    """
    _validate(account)
    if not image_urls:
        raise InstagramError("No image URLs to publish.")

    ig_id = account["ig_business_id"]
    token = account["ig_access_token"]

    if len(image_urls) == 1:
        created = _post(
            f"{GRAPH}/{ig_id}/media",
            {"image_url": image_urls[0], "caption": caption, "access_token": token},
        )
        _wait_ready(created["id"], token)                 # let Instagram fetch + process it
        published = _publish_container(ig_id, created["id"], token)
        media_id = published["id"]
        return {"ig_media_id": media_id, "permalink": _permalink(media_id, token), "media_type": "image"}

    # --- carousel ---
    # Create all children first (they process in parallel on Instagram's side — NO per-child
    # wait, that was the slow part). We only wait for the CAROUSEL CONTAINER to be FINISHED,
    # which already requires every child to be processed. This is ~5x faster.
    child_ids: List[str] = []
    for url in image_urls:
        child = _create_child(ig_id, url, token)
        child_ids.append(child["id"])

    container = _post(
        f"{GRAPH}/{ig_id}/media",
        {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": token,
        },
    )
    _wait_ready(container["id"], token)                   # one wait for the whole carousel
    published = _publish_container(ig_id, container["id"], token)
    media_id = published["id"]
    return {"ig_media_id": media_id, "permalink": _permalink(media_id, token), "media_type": "carousel"}
