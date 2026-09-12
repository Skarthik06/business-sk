"""
rag/store.py  —  PostgreSQL + pgvector RAG store via LangChain.

Persists to the project's PostgreSQL database (DATABASE_URL) using the pgvector
extension. Requires the `vector` extension to be enabled in that database
(the setup script does `CREATE EXTENSION IF NOT EXISTS vector`).

Two purposes:
  1. CONTENT RAG: stores every pin ever generated so future runs retrieve
     similar past pins as few-shot generation context.
  2. PRODUCT DISCOVERY: given a category/query, suggests product types that
     have performed well — enables RAG-driven product ideas.

Uses:
  - langchain-postgres (PGVector)  →  pgvector-backed vector store
  - HuggingFaceEmbeddings          →  all-MiniLM-L6-v2, 100% free, local, no API key
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Optional

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector
from langchain_core.documents import Document

from config import cfg
from utils.logger import log

COLLECTION = cfg.storage.collection_name

# rag_retrieve fires two queries concurrently (executor threads); this lock makes
# the one-time embeddings load + PGVector table creation atomic, so both threads
# don't race to CREATE TABLE langchain_pg_collection (a UniqueViolation).
# RLock (reentrant) because get_vector_store() holds it while calling get_embeddings().
_init_lock = threading.RLock()


# ─── Embeddings (free, runs locally) ─────────────────────────────────────────

_embeddings: Optional[HuggingFaceEmbeddings] = None

def get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        with _init_lock:
            if _embeddings is None:
                log.info(f"[RAG] Loading {cfg.embedding_model} (first run ~80 MB download)...")
                _embeddings = HuggingFaceEmbeddings(
                    model_name=cfg.embedding_model,
                    model_kwargs={"device": "cpu"},
                    encode_kwargs={"normalize_embeddings": True},
                )
                log.success("[RAG] Embeddings model loaded ✓")
    return _embeddings


# ─── PGVector store (PostgreSQL + pgvector) ──────────────────────────────────

_vector_store: Optional[PGVector] = None

def get_vector_store() -> PGVector:
    global _vector_store
    if _vector_store is None:
        with _init_lock:
            if _vector_store is None:
                _vector_store = PGVector(
                    embeddings=get_embeddings(),
                    collection_name=COLLECTION,
                    connection=cfg.storage.sqlalchemy_url,   # postgresql+psycopg://...
                    use_jsonb=True,                          # metadata stored as JSONB (filterable)
                    # cosine distance → relevance scores land in a clean 0-1 range
                    distance_strategy="cosine",
                    create_extension=True,                   # ensures `vector` extension exists
                )
                log.success("[RAG] pgvector store ready (PostgreSQL) ✓")
    return _vector_store


def _where(category: Optional[str] = None) -> dict:
    """PGVector JSONB metadata filter (operator form)."""
    conds = [{"doc_type": {"$eq": "pin"}}]
    if category:
        conds.append({"category": {"$eq": category}})
    return conds[0] if len(conds) == 1 else {"$and": conds}


# ─── Public API ───────────────────────────────────────────────────────────────

class RAGStore:
    """
    High-level interface to the pgvector pin memory.

    Every successfully-posted pin is stored here. Future runs query it for:
      A) Content context  — "what pin descriptions worked for similar products?"
      B) Product discovery — "what product categories/types have we had success with?"
    """

    # ── Store a new pin ──────────────────────────────────────────────────

    def store_pin(
        self,
        asin:            str,
        pin_title:       str,
        pin_description: str,
        hashtags:        list[str],
        category:        str,
        price:           str = "",
        product_title:   str = "",
    ) -> None:
        """Embed and persist a generated pin. Embedded text is a rich summary so
        future similarity searches match on content style, not just keywords."""
        store = get_vector_store()

        embed_text = (
            f"Category: {category}\n"
            f"Product: {product_title}\n"
            f"Pin Title: {pin_title}\n"
            f"Description: {pin_description}\n"
            f"Tags: {' '.join(hashtags)}"
        )

        doc = Document(
            page_content=embed_text,
            metadata={
                "asin":            asin,
                "product_title":   product_title,
                "pin_title":       pin_title,
                "pin_description": pin_description,
                "hashtags":        json.dumps(hashtags),
                "category":        category,
                "price":           price,
                "pinned_at":       datetime.utcnow().isoformat(),
                "doc_type":        "pin",
            },
        )

        store.add_documents([doc], ids=[f"pin_{asin}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"])
        log.info(f"[RAG] Stored pin for ASIN {asin} in pgvector")

    # ── Retrieve similar past pins (content context) ─────────────────────────

    def retrieve_similar_pins(
        self,
        query:    str,
        category: Optional[str] = None,
        n:        int = 5,
    ) -> list[dict]:
        """Find past pins with content similar to the query (few-shot examples)."""
        store = get_vector_store()
        try:
            results = store.similarity_search_with_relevance_scores(
                query, k=n, filter=_where(category),
            )
        except Exception as e:
            log.warning(f"[RAG] Similarity search failed: {e}")
            return []

        pins = []
        for doc, score in results:
            m = doc.metadata
            pins.append({
                "pin_title":       m.get("pin_title", ""),
                "pin_description": m.get("pin_description", ""),
                "category":        m.get("category", ""),
                "product_title":   m.get("product_title", ""),
                "score":           round(float(score), 4),
            })

        log.success(f"[RAG] Retrieved {len(pins)} similar pins (top score: {pins[0]['score'] if pins else 'n/a'})")
        return pins

    # ── Discover successful product types (RAG product discovery) ────────────

    def discover_product_ideas(self, category: str, n: int = 8) -> list[str]:
        """Return product title fragments from past successful pins in this
        category, to bias ranking toward proven product types."""
        store = get_vector_store()
        query = f"best selling {category} products amazon affiliate high commission"
        try:
            results = store.similarity_search(query, k=n, filter=_where(category))
        except Exception:
            return []

        titles: list[str] = []
        for doc in results:
            title = doc.metadata.get("product_title", "")
            if title and title not in titles:
                titles.append(title)

        log.info(f"[RAG] Discovered {len(titles)} successful product types in '{category}'")
        return titles

    # ── Semantic novelty (Phase 2 — Novelty Analyst) ─────────────────────────

    def novelty_scores(self, products: list[dict]) -> dict:
        """Return {asin: novelty 0-100} — how UNLIKE already-posted pins each product
        is. 100 = nothing similar has been posted (great); low = we've covered this
        idea already. Compares 'category + title' against the pin memory via cosine
        similarity. Cold-start / any failure ⇒ 100 for all (emptiness is normal, G7).
        Novelty is an internal ranking feature ONLY — never a claim shown to a buyer."""
        out: dict = {}
        if not products:
            return out
        try:
            store = get_vector_store()
        except Exception as e:
            log.warning(f"[RAG] novelty store unavailable: {e} — treating all as novel")
            return {(p.get("asin") or ""): 100 for p in products}

        for p in products:
            asin = p.get("asin") or ""
            text = f"{p.get('category','')} {p.get('title') or p.get('product_title','')}".strip()
            if not text:
                out[asin] = 100
                continue
            try:
                hits = store.similarity_search_with_relevance_scores(text, k=1, filter=_where())
                if not hits:
                    out[asin] = 100                    # nothing posted yet like this
                else:
                    # relevance ∈ [0,1] (cosine); novelty is its complement.
                    rel = max(0.0, min(1.0, float(hits[0][1])))
                    out[asin] = int(round((1.0 - rel) * 100))
            except Exception:
                out[asin] = 100                        # fail-open: assume novel
        return out

    # ── Stats ──────────────────────────────────────────────────────────────────

    def count(self) -> int:
        """Total pins stored in this collection (best-effort)."""
        try:
            from sqlalchemy import create_engine, text
            eng = create_engine(cfg.storage.sqlalchemy_url)
            with eng.connect() as conn:
                n = conn.execute(
                    text(
                        "SELECT count(*) FROM langchain_pg_embedding e "
                        "JOIN langchain_pg_collection c ON e.collection_id = c.uuid "
                        "WHERE c.name = :name"
                    ),
                    {"name": COLLECTION},
                ).scalar()
            eng.dispose()
            return int(n or 0)
        except Exception:
            return 0


# Singleton
rag_store = RAGStore()
