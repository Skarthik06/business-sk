"""
rag/posts.py  —  Business-SK post history (PostgreSQL).

Every carousel we publish is recorded here as one row, labelled uniquely as
`post_<N>#<category>` (N = the global post id). Powers the History panel and the
engagement automation (which media_id → which products + affiliate links).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import cfg
from utils.logger import log


class Base(DeclarativeBase):
    pass


class SkPost(Base):
    __tablename__ = "sk_posts"

    id            = Column(Integer, primary_key=True, autoincrement=True)  # global post number
    label         = Column(String(120), nullable=False, default="")        # post_<id>#<category>
    category      = Column(String(80),  nullable=False, default="")
    media_id      = Column(String(64),  nullable=True)                      # IG media id
    permalink     = Column(Text,        nullable=True)
    product_count = Column(Integer,     nullable=False, default=0)
    product_asins = Column(Text,        nullable=False, default="[]")       # JSON
    affiliate_links = Column(Text,      nullable=False, default="[]")       # JSON
    products      = Column(Text,        nullable=False, default="[]")       # full [{asin,title,price,image,link}]
    caption       = Column(Text,        nullable=False, default="")
    content_style = Column(String(30),  nullable=False, default="")         # Phase 4 A/B style
    status        = Column(String(20),  nullable=False, default="posted")  # posted | failed | dry
    posted_at     = Column(DateTime,    nullable=False, default=datetime.utcnow)


_engine = None
_SessionLocal = None


def _session() -> Session:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(cfg.storage.sqlalchemy_url, pool_pre_ping=True, echo=False)
        Base.metadata.create_all(_engine)
        # Idempotent migration for the `products` column on pre-existing tables.
        with _engine.begin() as c:
            c.execute(text("ALTER TABLE sk_posts ADD COLUMN IF NOT EXISTS products TEXT NOT NULL DEFAULT '[]'"))
            c.execute(text("ALTER TABLE sk_posts ADD COLUMN IF NOT EXISTS content_style VARCHAR(30) NOT NULL DEFAULT ''"))
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        log.success("[Posts] sk_posts table ready ✓")
    return _SessionLocal()


class PostStore:
    def record(self, category: str, products: list[dict], media_id: Optional[str],
               permalink: Optional[str], caption: str, status: str = "posted",
               content_style: str = "") -> dict:
        """Insert a post row and return it (with the unique post_<N>#<category> label)."""
        asins = [p.get("asin", "") for p in products]
        links = [p.get("affiliate_link", "") for p in products]
        with _session() as s:
            row = SkPost(
                category=category, media_id=media_id, permalink=permalink,
                product_count=len(products), product_asins=json.dumps(asins),
                affiliate_links=json.dumps(links), products=json.dumps(products),
                caption=caption, status=status, content_style=content_style,
            )
            s.add(row)
            s.flush()                                   # assigns id
            row.label = f"post_{row.id}#{category}"
            s.commit()
            return self._to_dict(row)

    def list(self, limit: int = 50) -> list[dict]:
        with _session() as s:
            rows = s.query(SkPost).order_by(SkPost.id.desc()).limit(limit).all()
            return [self._to_dict(r) for r in rows]

    def get_by_media(self, media_id: str) -> Optional[dict]:
        with _session() as s:
            r = s.query(SkPost).filter(SkPost.media_id == media_id).first()
            return self._to_dict(r) if r else None

    def stats(self) -> dict:
        with _session() as s:
            total = s.query(SkPost).count()
            by_cat: dict = {}
            for (c,) in s.query(SkPost.category).all():
                by_cat[c] = by_cat.get(c, 0) + 1
            return {"total_posts": total, "by_category": by_cat}

    def all_products(self, category: Optional[str] = None) -> list[dict]:
        """Deduped products across all POSTED posts (newest first) — powers the hub page.
        Only status='posted' rows count: the public storefront/catalog reflects products
        ONLY after they are actually published to Instagram (dry-run/failed never leak)."""
        with _session() as s:
            q = s.query(SkPost).filter(SkPost.status == "posted").order_by(SkPost.id.desc())
            if category:
                q = q.filter(SkPost.category == category)
            seen: set = set()
            out: list[dict] = []
            for r in q.all():
                for p in json.loads(r.products or "[]"):
                    a = p.get("asin")
                    if a and a not in seen:
                        seen.add(a)
                        out.append({**p, "category": r.category})
            return out

    @staticmethod
    def _to_dict(r: SkPost) -> dict:
        return {
            "id": r.id, "label": r.label, "category": r.category,
            "media_id": r.media_id, "permalink": r.permalink,
            "product_count": r.product_count,
            "product_asins": json.loads(r.product_asins or "[]"),
            "affiliate_links": json.loads(r.affiliate_links or "[]"),
            "caption": r.caption, "status": r.status,
            "content_style": r.content_style or "",
            "posted_at": r.posted_at.isoformat() if r.posted_at else "",
        }


post_store = PostStore()
