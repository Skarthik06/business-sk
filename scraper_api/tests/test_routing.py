"""Strategy Engine: scoring, ranking, circuit breaker transitions, priors, worker states."""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SCRAPER_DATA_DIR", str(Path(__file__).resolve().parent / "_data"))

from app import routing, workers  # noqa: E402
from app.classifier import Outcome  # noqa: E402
from app.routing import Route, RouteBook  # noqa: E402
from app.strategies import Strategies  # noqa: E402

S = Strategies()


def run(coro):
    return asyncio.run(coro)


def test_amazon_priors_open_direct_and_favour_workers():
    s = S.for_host("www.amazon.in")
    pr = s.priors()
    assert pr["direct_http"]["circuit"] == "open" and pr["worker_http"]["success"] > 0.8
    book = RouteBook()
    direct = Route("direct_http", "direct", "http")
    laptop = Route("worker_http:laptop-01", "worker", "http", 1.0, {"worker_id": "laptop-01"})
    ranked = book.rank(s.domain, [direct, laptop], pr, set())
    assert [r.id for _, r in ranked] == ["worker_http:laptop-01"]        # direct circuit starts OPEN


def test_no_route_when_worker_offline_and_direct_open():
    s = S.for_host("www.amazon.in")
    book = RouteBook()
    direct = Route("direct_http", "direct", "http")
    offline = Route("worker_http:laptop-01", "worker", "http", 0.0, {"worker_id": "laptop-01"})
    assert book.rank(s.domain, [direct, offline], s.priors(), set()) == []


def test_easy_site_prefers_server_over_laptop_worker():
    s = S.for_host("www.snitch.co.in")
    book = RouteBook()
    laptop = Route("worker_http:laptop-01", "worker", "http", 1.0, {"worker_id": "laptop-01"})
    ranked = book.rank(s.domain, [laptop, Route("direct_http", "direct", "http")], s.priors(), set())
    assert ranked[0][1].id == "direct_http"


def test_easy_site_prefers_direct_http_over_browser():
    s = S.for_host("example.org")
    book = RouteBook()
    ranked = book.rank(s.domain, [Route("direct_browser", "direct", "browser"), Route("direct_http", "direct", "http")],
                       s.priors(), set())
    assert ranked[0][1].id == "direct_http"


def test_history_overrides_prior():
    s = S.for_host("shop.test")
    book = RouteBook()
    a, b = Route("direct_http", "direct", "http"), Route("worker_http:w1", "worker", "http", 1.0, {"worker_id": "w1"})
    for _ in range(4):                                       # direct keeps getting blocked, w1 keeps working
        run(book.record(s.domain, a, Outcome.CHALLENGE, 900, {}))
        run(book.record(s.domain, b, Outcome.SUCCESS, 3000, {}))
    ranked = book.rank(s.domain, [a, b], s.priors(), set())
    assert ranked[0][1].id == "worker_http:w1"


def test_circuit_opens_half_opens_and_closes():
    book = RouteBook()
    r = Route("direct_http", "direct", "http")
    for _ in range(routing.CB_THRESHOLD):
        run(book.record("x.test", r, Outcome.NETWORK_ERROR, 100, {}))
    c = book.circuits[("x.test", "direct_http")]
    assert c.state == "OPEN" and not book.allowed("x.test", r, {})
    c.open_until = time.time() - 1                            # cooldown over → one probe allowed
    assert book.allowed("x.test", r, {}) and c.state == "HALF_OPEN"
    book.claim_probe("x.test", r)
    assert not book.allowed("x.test", r, {})                  # only ONE probe at a time
    run(book.record("x.test", r, Outcome.SUCCESS, 100, {}))
    assert c.state == "CLOSED" and book.allowed("x.test", r, {})


def test_failed_probe_reopens_with_longer_cooldown():
    book = RouteBook()
    r = Route("direct_http", "direct", "http")
    for _ in range(routing.CB_THRESHOLD):
        run(book.record("y.test", r, Outcome.CHALLENGE, 100, {}))
    c = book.circuits[("y.test", "direct_http")]
    first = c.open_until - time.time()
    c.open_until = time.time() - 1
    assert book.allowed("y.test", r, {})
    run(book.record("y.test", r, Outcome.CHALLENGE, 100, {}))
    assert c.state == "OPEN" and (c.open_until - time.time()) > first * 1.5


def test_url_errors_do_not_hurt_the_route():
    book = RouteBook()
    r = Route("direct_http", "direct", "http")
    for _ in range(10):
        run(book.record("z.test", r, Outcome.NOT_FOUND, 50, {}))
    assert book.circuits[("z.test", "direct_http")].state == "CLOSED"
    assert book.health[("z.test", "direct_http")].attempts == 0


def test_stale_worker_ranks_below_online_worker():
    book = RouteBook()
    online = Route("worker_http:a", "worker", "http", 1.0, {"worker_id": "a"})
    stale = Route("worker_http:b", "worker", "http", 0.5, {"worker_id": "b"})
    ranked = book.rank("q.test", [stale, online], {}, set())
    assert ranked[0][1].id == "worker_http:a"


def test_worker_states_and_ids():
    assert workers.state_of(10) == "online"
    assert workers.state_of(90) == "stale"
    assert workers.state_of(500) == "offline"
    assert workers.valid_id("phone-01") and not workers.valid_id("../etc") and not workers.valid_id("")


def test_weights_configurable(monkeypatch):
    monkeypatch.setenv("SCRAPER_ROUTE_WEIGHTS", '{"success": 1.0, "availability": 0}')
    w = routing.weights()
    assert w["success"] == 1.0 and w["availability"] == 0 and w["latency"] == 0.10
