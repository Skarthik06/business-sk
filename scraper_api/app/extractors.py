"""Generic extractors: title, metadata, JSON-LD, price, image URLs (+ normaliser). Site-agnostic."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlsplit

_IMG_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".avif": "image/avif", ".gif": "image/gif"}
_NOT_IMG_EXT = (".svg", ".ico", ".js", ".css", ".html", ".php")


def _doc(html: str):
    import lxml.html
    try:
        return lxml.html.fromstring(html or "<html></html>")
    except Exception:
        return lxml.html.fromstring("<html></html>")


def _meta(doc, *names: str) -> str:
    for n in names:
        for attr in ("property", "name", "itemprop"):
            v = doc.xpath(f'//meta[@{attr}="{n}"]/@content')
            if v and v[0].strip():
                return v[0].strip()
    return ""


def _text(doc, xp: str) -> str:
    v = doc.xpath(xp)
    return re.sub(r"\s+", " ", (v[0] if isinstance(v[0], str) else v[0].text_content())).strip() if v else ""


def jsonld(doc) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(raw.strip(), strict=False)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("@graph"), list):
                out.extend(x for x in it["@graph"] if isinstance(x, dict))
            elif isinstance(it, dict):
                out.append(it)
    return out[:50]


def _ld_images(items: List[Dict[str, Any]]) -> Iterable[str]:
    for it in items:
        img = it.get("image")
        for v in (img if isinstance(img, list) else [img]):
            if isinstance(v, str):
                yield v
            elif isinstance(v, dict) and isinstance(v.get("url"), str):
                yield v["url"]


def _srcset(value: str) -> Iterable[tuple]:
    for part in (value or "").split(","):
        bits = part.strip().split()
        if bits:
            w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else None
            yield bits[0], w


def normalize_image(url: str, base: str) -> Optional[str]:
    u = (url or "").strip().strip("'\"")
    if not u or u.startswith(("data:", "blob:", "javascript:")):
        return None
    u = urljoin(base, u)
    p = urlsplit(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    path = p.path.lower()
    if path.endswith(_NOT_IMG_EXT):
        return None
    return u


def images(doc, base: str, items: Optional[List[Dict[str, Any]]] = None, limit: int = 200) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen = set()

    def add(u: str, source: str, w=None, h=None):
        n = normalize_image(u, base)
        if not n or n in seen or len(found) >= limit:
            return
        seen.add(n)
        ext = "." + urlsplit(n).path.rsplit(".", 1)[-1].lower() if "." in urlsplit(n).path else ""
        found.append({"url": n, "source": source, "width": w, "height": h, "content_type": _IMG_EXT.get(ext)})

    for src, prop in (("og:image", "og:image"), ("og:image:secure_url", "og:image"), ("twitter:image", "twitter:image")):
        for v in doc.xpath(f'//meta[@property="{prop}" or @name="{prop}" or @property="{src}"]/@content'):
            add(v, prop)
    for v in doc.xpath('//link[@rel="image_src"]/@href'):
        add(v, "link:image_src")
    for u in _ld_images(items if items is not None else jsonld(doc)):
        add(u, "json-ld")
    for img in doc.xpath("//img"):
        w = img.get("width") if (img.get("width") or "").isdigit() else None
        h = img.get("height") if (img.get("height") or "").isdigit() else None
        for attr in ("data-old-hires", "src", "data-src", "data-lazy-src"):
            if img.get(attr):
                add(img.get(attr), f"img@{attr}", int(w) if w else None, int(h) if h else None)
        dyn = img.get("data-a-dynamic-image")          # {"url": [w, h], ...} (Amazon)
        if dyn:
            try:
                for u, wh in json.loads(dyn).items():
                    add(u, "img@data-a-dynamic-image", *(wh[:2] if isinstance(wh, list) else (None, None)))
            except Exception:
                pass
        for attr in ("srcset", "data-srcset"):
            for u, wd in _srcset(img.get(attr) or ""):
                add(u, f"img@{attr}", wd)
    for s in doc.xpath("//source/@srcset"):
        for u, wd in _srcset(s):
            add(u, "source@srcset", wd)
    return found


def price(doc, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for it in items:
        offers = it.get("offers")
        for o in (offers if isinstance(offers, list) else [offers]):
            if isinstance(o, dict) and (o.get("price") or o.get("lowPrice")):
                return {"amount": str(o.get("price") or o.get("lowPrice")), "currency": o.get("priceCurrency"),
                        "source": "json-ld"}
    amt = _meta(doc, "product:price:amount", "og:price:amount", "price")
    if amt:
        return {"amount": amt, "currency": _meta(doc, "product:price:currency", "og:price:currency", "priceCurrency") or None,
                "source": "meta"}
    return None


def metadata(doc) -> Dict[str, Any]:
    canon = doc.xpath('//link[@rel="canonical"]/@href')
    lang = doc.xpath("//html/@lang")
    return {"description": _meta(doc, "og:description", "description", "twitter:description") or None,
            "canonical": canon[0].strip() if canon else None,
            "site_name": _meta(doc, "og:site_name") or None,
            "type": _meta(doc, "og:type") or None,
            "lang": lang[0] if lang else None}


def title(doc) -> Optional[str]:
    return (_meta(doc, "og:title", "twitter:title") or _text(doc, "//title") or _text(doc, "//h1")) or None


def links(doc, base: str, limit: int = 500) -> List[str]:
    out, seen = [], set()
    for h in doc.xpath("//a/@href"):
        u = urljoin(base, h.strip())
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
            if len(out) >= limit:
                break
    return out


def extract(html: str, base_url: str, fields: Iterable[str]) -> Dict[str, Any]:
    """Run the requested extractors. `html` in fields returns the raw page too."""
    want = {f.strip().lower() for f in fields if f and f.strip()}
    out: Dict[str, Any] = {}
    if want - {"html"}:
        doc = _doc(html)
        items = jsonld(doc) if want & {"jsonld", "images", "price"} else []
        if "title" in want:
            out["title"] = title(doc)
        if "metadata" in want:
            out["metadata"] = metadata(doc)
        if "jsonld" in want:
            out["jsonld"] = items
        if "images" in want:
            out["images"] = images(doc, base_url, items)
        if "price" in want:
            out["price"] = price(doc, items)
        if "links" in want:
            out["links"] = links(doc, base_url)
    if "html" in want:
        out["html"] = html
    return out
