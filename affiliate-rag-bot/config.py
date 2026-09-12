"""
config.py — Central config, reads .env and exposes a typed Config object.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Project root — also used to resolve local storage paths so the DB always lives
# in this folder regardless of the current working directory.
_BASE = os.path.dirname(os.path.abspath(__file__))

# Load THIS project's .env explicitly (the one next to config.py) so it is used
# no matter which directory the CLI or uvicorn server is launched from.
load_dotenv(os.path.join(_BASE, ".env"))


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(_BASE, path))


@dataclass
class AmazonConfig:
    email:         str = field(default_factory=lambda: os.getenv("AMAZON_EMAIL", ""))
    password:      str = field(default_factory=lambda: os.getenv("AMAZON_PASSWORD", ""))
    associate_tag: str = field(default_factory=lambda: os.getenv("AMAZON_ASSOCIATE_TAG", "yourtag-21"))
    marketplace:   str = field(default_factory=lambda: os.getenv("AMAZON_MARKETPLACE", "amazon.in"))
    category:      str = field(default_factory=lambda: os.getenv("AMAZON_CATEGORY", "home"))
    # How to build affiliate links:
    #   "deeplink"  → official ?tag= product URL (no login, no browser) — DEFAULT
    #   "sitestripe"→ legacy: scrape a SiteStripe short URL from a logged-in session
    link_method:   str = field(default_factory=lambda: os.getenv("AMAZON_LINK_METHOD", "deeplink"))


@dataclass
class PinterestConfig:
    email:      str = field(default_factory=lambda: os.getenv("PINTEREST_EMAIL", ""))
    password:   str = field(default_factory=lambda: os.getenv("PINTEREST_PASSWORD", ""))
    board_name: str = field(default_factory=lambda: os.getenv("PINTEREST_BOARD_NAME", "Best Amazon Deals"))


@dataclass
class StorageConfig:
    """PostgreSQL + pgvector storage — one database holds BOTH the RAG vector
    store (pin memory) and the seen_products dedup ledger."""
    # Full connection URL, including the project database name, e.g.
    #   postgresql://user:pass@localhost:5432/affiliate_rag_bot
    database_url:    str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    # pgvector collection name for stored pins.
    collection_name: str = field(default_factory=lambda: os.getenv("PGVECTOR_COLLECTION", "affiliate_pins"))

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy / LangChain-Postgres URL — forces the psycopg (v3) driver."""
        url = self.database_url
        if url.startswith("postgresql+psycopg://"):
            return url
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://"):]
        return url


@dataclass
class BotConfig:
    products_per_run:   int  = field(default_factory=lambda: int(os.getenv("PRODUCTS_PER_RUN", "3")))
    delay_between_pins: int  = field(default_factory=lambda: int(os.getenv("DELAY_BETWEEN_PINS", "1200")))
    headless:           bool = field(default_factory=lambda: os.getenv("HEADLESS", "false").lower() == "true")
    # ── Quality constraints — only surface products that ATTRACT customers ──
    # (grounded on real scraped values; products failing these are dropped
    # before ranking, and the survivors are ranked by attractiveness score.)
    min_rating:  float = field(default_factory=lambda: float(os.getenv("QUALITY_MIN_RATING", "3.8")))
    min_reviews: int   = field(default_factory=lambda: int(os.getenv("QUALITY_MIN_REVIEWS", "50")))
    price_min:   int   = field(default_factory=lambda: int(os.getenv("QUALITY_PRICE_MIN", "199")))
    price_max:   int   = field(default_factory=lambda: int(os.getenv("QUALITY_PRICE_MAX", "5000")))


@dataclass
class DiscoveryConfig:
    """Phase 1 (Autopilot blueprint) — multi-query discovery + adaptive stopping.

    Category mode mines SEVERAL subcategory search intents per run (rotated by past
    yield) and paginates each, instead of one fixed category term — so each run
    surfaces far more UNIQUE products. All bounded so runtime stays predictable.
    Set DISCOVERY_ENABLED=0 to fall back to the original single-query behaviour."""
    enabled:      bool = field(default_factory=lambda: os.getenv("DISCOVERY_ENABLED", "1") != "0")
    max_queries:  int  = field(default_factory=lambda: int(os.getenv("DISCOVERY_MAX_QUERIES", "5")))
    max_pages:    int  = field(default_factory=lambda: int(os.getenv("DISCOVERY_MAX_PAGES", "2")))
    target_pool:  int  = field(default_factory=lambda: int(os.getenv("DISCOVERY_TARGET_POOL", "60")))


@dataclass
class NoveltyConfig:
    """Phase 2 — semantic novelty (avoid conceptually-repetitive products) + the
    transparent winner score. Novelty compares a candidate against already-posted
    pins in pgvector; higher = less similar to what we've already covered. It is an
    internal ranking feature only — never a claim shown to a buyer (G13)."""
    enabled:   bool  = field(default_factory=lambda: os.getenv("NOVELTY_ENABLED", "1") != "0")
    weight:    float = field(default_factory=lambda: float(os.getenv("NOVELTY_WEIGHT", "0.10")))
    # winner_score = product_intelligence * confidence * freshness. This is the floor
    # confidence never drops below, so a strong product with a couple of missing
    # fields is only gently penalised, never zeroed.
    min_confidence: float = field(default_factory=lambda: float(os.getenv("WINNER_MIN_CONFIDENCE", "0.55")))


@dataclass
class TrendConfig:
    """Phase 3 (Trend Analyst) — persistent trend memory + momentum, trend-aware
    discovery and content. Momentum/direction are INTERNAL model scores (never a
    market fact, G13). Provider stays optional (Tavily); absence ⇒ graceful fallback."""
    enabled:       bool  = field(default_factory=lambda: os.getenv("TREND_ENABLED", "1") != "0")
    max_keywords:  int   = field(default_factory=lambda: int(os.getenv("TREND_MAX_KEYWORDS", "12")))
    lookback_days: int   = field(default_factory=lambda: int(os.getenv("TREND_LOOKBACK_DAYS", "14")))
    # How much a product's trend alignment influences product-intelligence (small — a
    # product should never be picked on trend alone; discovery-first, G14).
    weight:        float = field(default_factory=lambda: float(os.getenv("TREND_WEIGHT", "0.08")))
    # Trending terms blended into discovery query rotation per run.
    discovery_terms: int = field(default_factory=lambda: int(os.getenv("TREND_DISCOVERY_TERMS", "3")))


@dataclass
class Config:
    amazon:            AmazonConfig    = field(default_factory=AmazonConfig)
    pinterest:         PinterestConfig = field(default_factory=PinterestConfig)
    storage:           StorageConfig   = field(default_factory=StorageConfig)
    bot:               BotConfig       = field(default_factory=BotConfig)
    discovery:         DiscoveryConfig = field(default_factory=DiscoveryConfig)
    novelty:           NoveltyConfig   = field(default_factory=NoveltyConfig)
    trends:            TrendConfig     = field(default_factory=TrendConfig)
    # ── LLM (OpenAI / ChatGPT) ──────────────────────────────────────────
    openai_api_key:    str             = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model:      str             = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5-nano"))
    # Max output tokens for the compose call (reasoning tokens count toward this
    # for gpt-5 / o-series models, so keep some headroom).
    llm_max_output_tokens: int         = field(default_factory=lambda: int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "5000")))
    # Reasoning effort for gpt-5 / o-series models: minimal | low | medium | high.
    # "minimal" keeps (billed) reasoning tokens tiny for this copywriting workload.
    llm_reasoning_effort: str          = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", "minimal"))
    # Sampling temperature — LOW to curb hallucination. NOTE: reasoning models
    # (gpt-5 / o-series) reject any non-default temperature, so it is applied
    # ONLY to non-reasoning models; reasoning models omit it entirely.
    llm_temperature: float             = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.2")))

    @property
    def is_reasoning_model(self) -> bool:
        """gpt-5 family and o-series are reasoning models (different API params)."""
        m = (self.openai_model or "").lower()
        return m.startswith(("gpt-5", "o1", "o3", "o4"))
    # ── Embeddings (free, local — no API key) ───────────────────────────
    embedding_model:   str             = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    # ── Search ──────────────────────────────────────────────────────────
    tavily_api_key:    str             = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    # ── Affiliate network template (optional) ───────────────────────────
    # Network-agnostic override for link building. When set, it takes priority
    # over Amazon's deep link so you can use an aggregator (EarnKaro / Cuelinks /
    # INRDeals) without code changes. Placeholders substituted at runtime:
    #   {url}          — the Amazon product URL
    #   {url_encoded}  — URL-encoded product URL (for ?url= style redirects)
    #   {asin}         — the product ASIN
    #   {tag}          — your AMAZON_ASSOCIATE_TAG
    # Example (redirect style):  https://linkredirect.example/?url={url_encoded}
    affiliate_link_template: str       = field(default_factory=lambda: os.getenv("AFFILIATE_LINK_TEMPLATE", ""))

    def validate(self):
        # TAVILY_API_KEY is intentionally NOT required — tools/search.py falls
        # back to sensible static keywords when it's absent. Add a key only to
        # get fresher, real-time trending keywords.
        # Storage is PostgreSQL + pgvector — DATABASE_URL is required.
        required = {
            "AMAZON_EMAIL":       self.amazon.email,
            "AMAZON_PASSWORD":    self.amazon.password,
            "PINTEREST_EMAIL":    self.pinterest.email,
            "PINTEREST_PASSWORD": self.pinterest.password,
            "OPENAI_API_KEY":     self.openai_api_key,
            "DATABASE_URL":       self.storage.database_url,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"Missing required env vars: {', '.join(missing)}\n"
                "Copy .env.example → .env and fill in your credentials."
            )
        if not self.tavily_api_key:
            print("ℹ️  No TAVILY_API_KEY — using fallback trend keywords (optional feature).")
        if self.bot.delay_between_pins < 600:
            print(f"⚠️  DELAY_BETWEEN_PINS={self.bot.delay_between_pins}s < 10 min — shadowban risk!")


cfg = Config()
