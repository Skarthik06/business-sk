"""Unit tests for the parts that need no network: strategies, classifier, backoff, SSRF, extractors."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SCRAPER_DATA_DIR", str(Path(__file__).resolve().parent / "_data"))

import pytest  # noqa: E402

from app import security  # noqa: E402
from app.classifier import Outcome, backoff, classify, parse_retry_after  # noqa: E402
from app.extractors import extract, normalize_image  # noqa: E402
from app.strategies import Strategies, Strategy  # noqa: E402

S = Strategies()


def test_strategy_lookup_parent_domain():
    assert S.for_host("www.amazon.in").domain == "amazon.in"
    assert S.for_host("www.amazon.in").residential is True
    assert S.for_host("snitch.myshopify.com").domain == "myshopify.com"
    d = S.for_host("example.org")
    assert d.domain == "example.org" and d.mode == "auto" and d.residential is False


def test_classify_matrix():
    s = Strategy(min_bytes=100, challenge_markers=["Robot Check"])
    big = "<html>" + "x" * 500 + "</html>"
    assert classify(200, big, "text/html", s)[0] == Outcome.SUCCESS
    assert classify(200, "<html>Robot Check</html>" + "x" * 500, "text/html", s)[0] == Outcome.CHALLENGE
    assert classify(200, "<html>tiny</html>", "text/html", s)[0] == Outcome.CHALLENGE
    assert classify(200, '{"a": 1}', "application/json", s)[0] == Outcome.SUCCESS   # short JSON is fine
    assert classify(429, "", "", s)[0] == Outcome.RATE_LIMITED
    assert classify(403, "", "", s)[0] == Outcome.ACCESS_DENIED
    assert classify(503, "Robot Check", "text/html", s)[0] == Outcome.CHALLENGE
    assert classify(502, "", "", s)[0] == Outcome.SERVER_ERROR
    assert classify(404, "", "", s)[0] == Outcome.NOT_FOUND
    assert classify(418, "", "", s)[0] == Outcome.CLIENT_ERROR
    assert classify(None, "", "", s, error="timeout")[0] == Outcome.NETWORK_ERROR


def test_backoff_bounds_and_retry_after():
    for a in range(1, 8):
        assert 0 <= backoff(a) <= 20
    assert backoff(3, retry_after=7) == 7
    assert backoff(3, retry_after=500) == 20
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None


@pytest.mark.parametrize("url", [
    "ftp://example.com/x", "file:///etc/passwd", "http://127.0.0.1/", "http://localhost:8000/",
    "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "http://user:pw@example.com/",
    "http://metadata.google.internal/", "http://" + "a" * 3000 + ".com/"])
def test_ssrf_rejects(url):
    with pytest.raises(security.UrlRejected):
        security.validate_url(url)


def test_ip_blocked_mapped_v6():
    assert security.ip_blocked("::ffff:127.0.0.1")
    assert security.ip_blocked("fe80::1")
    assert not security.ip_blocked("8.8.8.8")


HTML = """<html lang="en"><head><title>Page T</title>
<meta property="og:title" content="Nice Jacket"><meta property="og:image" content="/img/a.jpg">
<meta name="description" content="Warm jacket"><link rel="canonical" href="https://shop.test/p/1">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Nice Jacket",
 "image":["https://cdn.test/b.webp"],"offers":{"@type":"Offer","price":"999","priceCurrency":"INR"}}</script>
</head><body><h1>Nice Jacket</h1>
<img src="/img/a.jpg"><img data-src="https://cdn.test/c.png" width="400" height="500">
<img srcset="https://cdn.test/d-300.jpg 300w, https://cdn.test/d-800.jpg 800w">
<img src="data:image/png;base64,AAAA"><img src="/icons/logo.svg">
<img data-a-dynamic-image='{"https://m.media.test/e.jpg":[1500,1500]}'>
<a href="/p/2">next</a></body></html>"""


def test_extract_product_page():
    d = extract(HTML, "https://shop.test/p/1", ["title", "images", "metadata", "jsonld", "price", "links"])
    assert d["title"] == "Nice Jacket"
    urls = [i["url"] for i in d["images"]]
    assert urls[0] == "https://shop.test/img/a.jpg" and urls.count("https://shop.test/img/a.jpg") == 1
    assert "https://cdn.test/b.webp" in urls and "https://cdn.test/c.png" in urls
    assert "https://cdn.test/d-800.jpg" in urls and "https://m.media.test/e.jpg" in urls
    assert not any(u.startswith("data:") or u.endswith(".svg") for u in urls)
    assert d["price"] == {"amount": "999", "currency": "INR", "source": "json-ld"}
    assert d["metadata"]["canonical"] == "https://shop.test/p/1" and d["metadata"]["lang"] == "en"
    assert d["jsonld"][0]["@type"] == "Product"
    assert d["links"] == ["https://shop.test/p/2"]
    assert "html" not in d


def test_extract_html_only_and_garbage():
    assert extract("<html>x</html>", "https://a.test/", ["html"]) == {"html": "<html>x</html>"}
    assert extract("", "https://a.test/", ["title", "images"])["images"] == []


def test_normalize_image():
    assert normalize_image("//cdn.test/x.jpg", "https://a.test/") == "https://cdn.test/x.jpg"
    assert normalize_image("javascript:alert(1)", "https://a.test/") is None
