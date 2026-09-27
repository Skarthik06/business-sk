"""Device pairing helpers + the per-worker site allowlist."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SCRAPER_DATA_DIR", str(Path(__file__).resolve().parent / "_data"))

from app import devices, workers  # noqa: E402


def test_code_normalisation():
    assert devices.normalize_code(" abcd-2345 ") == "ABCD2345"
    assert devices.normalize_code("ab cd 23 45") == "ABCD2345"
    assert len(devices.normalize_code("short")) != 8


def test_alphabet_has_no_confusable_characters():
    for c in "0O1IL":
        assert c not in devices.ALPHABET


def test_worker_id_slug():
    assert devices._slug("iQOO Neo 10R") == "phone-iqoo-neo-10r"
    assert devices._slug("Phone 2") == "phone-2"
    assert devices._slug("") == "phone"
    assert all(workers.valid_id(devices._slug(n) + "-ab12") for n in ("iQOO Neo 10R", "x" * 80, "../../etc"))


def test_worker_allowlist():
    phone = {"allowed_domains": ["amazon.in", "flipkart.com"]}
    assert workers.allows(phone, "www.amazon.in")
    assert workers.allows(phone, "amazon.in")
    assert workers.allows(phone, "dl.flipkart.com")
    assert not workers.allows(phone, "www.snitch.co.in")
    assert not workers.allows(phone, "evilamazon.in")            # suffix trick is not a subdomain
    assert workers.allows({"allowed_domains": []}, "anything.test")
    assert workers.allows({}, "anything.test")


def test_qr_svg_renders():
    svg = devices.qr_svg("skhelper://pair?server=https%3A%2F%2Fx.test%2Fscraper-worker&code=ABCD2345")
    assert svg.lstrip().startswith("<svg") and "</svg>" in svg
