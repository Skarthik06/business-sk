"""API-key hashing, SSRF protection and proxy-credential encryption."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
from functools import lru_cache
from typing import Optional
from urllib.parse import urlsplit

from . import config


class UrlRejected(ValueError):
    """The URL is not allowed (scheme, length, credentials, or an internal address)."""


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


_BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal", "instance-data"}


def ip_blocked(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast
            or a.is_unspecified or str(a) == "169.254.169.254")


@lru_cache(maxsize=2048)
def _resolve(host: str, port: int) -> tuple:
    return tuple(sorted({i[4][0] for i in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)}))


def host_blocked(host: str, port: int = 443) -> Optional[str]:
    """Reason string if this host must not be fetched (internal / unresolvable), else None."""
    if config.ALLOW_PRIVATE:
        return None
    h = (host or "").lower().rstrip(".")
    if not h or h in _BLOCKED_HOSTNAMES or h.endswith((".internal", ".local", ".localhost")):
        return "internal hostname"
    try:
        ips = _resolve(h, port)
    except socket.gaierror:
        return "host does not resolve"
    if not ips or any(ip_blocked(ip) for ip in ips):
        return "private / internal address"
    return None


def validate_url(url: str) -> str:
    """Only public http(s) URLs. Raises UrlRejected. (Blocking DNS — call via asyncio.to_thread.)"""
    url = (url or "").strip()
    if not url or len(url) > config.MAX_URL_LEN:
        raise UrlRejected("URL missing or too long")
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise UrlRejected("only http/https URLs are allowed")
    if not p.hostname:
        raise UrlRejected("URL has no host")
    if p.username or p.password:
        raise UrlRejected("credentials inside the URL are not allowed")
    why = host_blocked(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    if why:
        raise UrlRejected(f"blocked target: {why}")
    return url


# ── proxy credential encryption (Fernet) ──────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    key = config.SECRET_KEY
    if not key:                                   # persist a generated key next to the data
        kp = config.DATA_DIR / "secret.key"
        if kp.exists():
            key = kp.read_text().strip()
        else:
            kp.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key().decode()
            kp.write_text(key)
            os.chmod(kp, 0o600)
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode() if token else ""
