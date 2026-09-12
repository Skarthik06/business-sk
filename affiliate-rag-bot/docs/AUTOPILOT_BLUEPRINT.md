# Studio --- Affiliate Intelligence Autopilot

## Complete Architecture, Upgrade Specification & Implementation Blueprint

> **Document purpose**
>
> This document is the implementation blueprint for upgrading the
> existing Business-SK affiliate Instagram automation into a
> production-oriented **Affiliate Intelligence Autopilot**.
>
> It is intentionally designed as an evolution of the existing system,
> not a rewrite. Existing working components, safety rules, uniqueness
> guarantees, scoring, RAG memory, API contracts, and Instagram
> automation should be preserved unless a change is explicitly
> described.
>
> **Source baseline:** `ENGINE_GUIDE.md` and the current Studio
> dashboard.
>
> **Important:** the existing code remains the source of truth. This
> document describes the target architecture and implementation plan.
> Any feature that requires data not currently available must be
> implemented with a graceful fallback rather than fabricated data.

------------------------------------------------------------------------

# 1. Executive Summary

The current system already provides a strong affiliate-content
foundation:

``` text
Amazon search
    ↓
quality gate
    ↓
within-run dedup
    ↓
cross-run ASIN dedup
    ↓
trend enrichment
    ↓
RAG retrieval
    ↓
deterministic scoring
    ↓
one structured LLM call
    ↓
affiliate links
    ↓
store / Instagram backend
```

The target system upgrades this into:

``` text
                    ┌──────────────────────────────┐
                    │     AFFILIATE BRAIN           │
                    │ discovery + prediction +      │
                    │ learning + decision engine    │
                    └──────────────┬───────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ↓                          ↓                          ↓
 PRODUCT DISCOVERY          TREND INTELLIGENCE        PERFORMANCE DATA
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   ↓
                         PRODUCT INTELLIGENCE
                                   │
             ┌─────────────────────┼─────────────────────┐
             ↓                     ↓                     ↓
          VALUE                VIRALITY              COMMERCIAL
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   ↓
                           WINNER DISCOVERY
                                   │
                     ┌─────────────┼─────────────┐
                     ↓             ↓             ↓
                  CONTENT      SCHEDULER      STOREFRONT
                     │             │             │
                     └─────────────┼─────────────┘
                                   ↓
                              INSTAGRAM
                                   │
                                   ↓
                            PERFORMANCE
                                   │
                                   ↓
                          LEARNING / RAG
                                   │
                                   └────────→ DISCOVERY
```

The most important architectural change is the addition of a **closed
performance loop**:

``` text
DISCOVER → SCORE → PREDICT → CREATE → PUBLISH → MEASURE → LEARN → RE-RANK
```

The system should optimize for **expected affiliate opportunity**, not
simply the number of products scraped.

------------------------------------------------------------------------

# 2. Existing System --- Preserve These Foundations

The current engine is an 8-node LangGraph pipeline:

``` text
START
  ↓
scrape_amazon
  ↓
check_duplicates
  ↓
search_trends
  ↓
rag_retrieve
  ↓
compose_pins
  ↓
get_affiliate_links
  ↓
post_pinterest / skipped in content_only
  ↓
store_results
  ↓
END
```

Existing responsibilities:

  -----------------------------------------------------------------------
  Component                           Current role
  ----------------------------------- -----------------------------------
  `graph/workflow.py`                 LangGraph orchestration

  `graph/nodes.py`                    Node implementations

  `graph/state.py`                    Shared `BotState`

  `pipeline_runner.py`                Stream/JSON pipeline execution

  `tools/amazon.py`                   Amazon search scraping, quality
                                      gate, links

  `chains/discovery.py`               taxonomy, scoring, tiers, price
                                      bands, bundles

  `chains/compose.py`                 structured LLM selection +
                                      caption + hashtags

  `tools/search.py`                   optional trend keywords

  `rag/store.py`                      pgvector memory and proven-product
                                      retrieval

  `rag/dedup.py`                      exact ASIN cross-run uniqueness

  `rag/posts.py`                      posted-product catalog

  `server.py`                         HTTP API

  `config.py`                         environment configuration
  -----------------------------------------------------------------------

These components should remain reusable.

------------------------------------------------------------------------

# 3. Existing Global Rules --- Do Not Break

The upgrade must preserve the current guardrails.

## G1 --- Never raise for recoverable errors

Nodes should accumulate errors and continue when the failure is
optional.

## G2 --- Fail-open enrichment, fail-closed core pipeline

Enrichment failures such as trends or optional visual analysis should
not destroy a valid product run.

Scrape, critical dedup and content-generation failures must stop the
relevant operation safely.

## G3 --- Affiliate disclosure

Published affiliate content must contain the required disclosure
mechanism. The existing system force-inserts `#ad`.

## G4 --- Posting safety

Respect the existing posting delay and account-safety controls. Never
increase automation volume simply because the queue is large.

## G5 --- Never intentionally re-post an ASIN

The exact ASIN uniqueness ledger remains mandatory.

## G6 --- One PostgreSQL + pgvector data layer

Keep relational data and vector memory in the existing database
architecture unless there is a demonstrated scaling reason to separate
them.

## G7 --- Cold-start tolerance

Empty RAG/performance history is normal. The system must work with zero
historical data.

## G8 --- Secrets stay in environment configuration

Never expose affiliate tags, API keys, cookies, session secrets or
database credentials in the UI/API.

## G9 --- One active run at a time per protected account/session

Preserve the existing run lock and make the lock account-aware if
multiple Instagram accounts are eventually supported.

## G10 --- Control LLM cost

The current pipeline uses one structured LLM call. Keep that default for
the standard generation path.

Additional LLM calls should be optional and justified by a measurable
benefit.

## G11 --- Resilient selectors

Keep robust DOM selectors and fallback extraction paths for external
pages.

## G12 --- Dry-run means no publishing

Dry-run must never perform real Instagram publishing or other
irreversible actions.

## G13 --- No fabrication

Never invent:

-   price
-   discount
-   rating
-   reviews
-   sales
-   availability
-   commission
-   product specifications
-   retailer
-   affiliate URL
-   performance metrics

If data is unavailable, mark it unavailable.

## G14 --- Discovery first, commission second

Commission can influence ranking, but a product should not be selected
solely because its commission is high.

## G15 --- Arithmetic truth

Any budget claim must be calculated from actual prices.

## G16 --- Quality over quantity

Returning fewer strong products is better than padding a result set with
weak products.

## G17 --- Two-layer uniqueness

Use both:

1.  within-run product deduplication
2.  cross-run ASIN deduplication

Add semantic/content novelty on top without replacing exact ASIN
protection.

## G18 --- Public catalog reflects posted products

Generated, failed, dry-run and unposted products must not become
publicly visible storefront inventory.

------------------------------------------------------------------------

# 4. Target Product

## Studio --- Affiliate Intelligence Autopilot

The upgraded Studio should answer:

> **What should I post next, why should I post it, how should I present
> it, when should I post it, and what did we learn afterward?**

Instead of only:

> Find products.

------------------------------------------------------------------------

# 5. Target Dashboard

Recommended navigation:

``` text
Studio

├── Overview
├── Discover
├── Winners
├── Trends
├── Content Calendar
├── Content Studio
├── Instagram
├── Engagement
├── Storefront
├── Revenue
├── Intelligence
├── History
├── Agents
└── Settings
```

------------------------------------------------------------------------

# 6. Overview Dashboard

The Overview page should show the operational state of the business.

## Top cards

``` text
Today's Posts
Clicks
Orders
Commission
Revenue / 1K Views
Winner Products
```

Only show metrics that actually exist.

If affiliate-order data is not connected:

``` text
Orders
Not connected
```

Do not display a guessed number.

## Operational status

``` text
Product Discovery     🟢 Healthy
Instagram Session     🟢 Connected
Database              🟢 Healthy
RAG Memory             🟢 Healthy
Trend Provider         🟡 Optional
Affiliate Tracking     🟡 Partial
```

## Recent winners

Show:

-   product
-   category
-   winner score
-   post
-   performance
-   reason selected

------------------------------------------------------------------------

# 7. Product Discovery Engine

This is the highest-priority upgrade.

## Current limitation

The current category mode uses a small number of category-level search
terms.

The new system should use a rotating discovery matrix.

## Discovery hierarchy

``` text
CATEGORY
  ↓
FAMILY
  ↓
SUBCATEGORY
  ↓
SEARCH INTENTS
  ↓
SEARCH VARIATIONS
  ↓
TREND MODIFIERS
  ↓
RETAILER SEARCH
  ↓
PRODUCT POOL
```

Example:

``` text
Home
 ├── Desk
 │   ├── desk organizer
 │   ├── desk organization
 │   ├── desk accessories
 │   ├── study table accessories
 │   └── minimal desk setup
 │
 ├── Lighting
 │   ├── room lights
 │   ├── ambient lighting
 │   ├── desk lamps
 │   └── LED room decor
 │
 └── Storage
     ├── storage organizer
     ├── drawer organizer
     └── cable management
```

------------------------------------------------------------------------

# 8. Discovery Rotation

Do not repeatedly use the same query.

Maintain a discovery state:

``` text
category
subcategory
query
last_used_at
usage_count
fresh_product_yield
winner_yield
last_result_count
```

A query that repeatedly produces duplicates should receive a lower
priority.

A query that produces strong fresh products should receive a higher
priority.

Example:

``` text
desk organizer
fresh yield: 31
winner yield: 8
priority: HIGH

generic home decor
fresh yield: 4
winner yield: 0
priority: LOW
```

This creates an adaptive search strategy.

------------------------------------------------------------------------

# 9. Multi-Query Discovery

For every selected category, generate several search intents from the
existing taxonomy.

Example:

``` text
Category: electronics

Queries:
electronics gadgets
useful gadgets
desk gadgets
phone accessories
travel gadgets
student gadgets
tech under 1000
viral gadgets
```

Searches should be executed with controlled concurrency so the system
does not overload a retailer or browser session.

------------------------------------------------------------------------

# 10. Pagination / Deep Search

The current system should expand beyond the first result page.

Recommended process:

``` text
Query 1
 ├── page 1
 ├── page 2
 └── page 3

Query 2
 ├── page 1
 ├── page 2
 └── page 3
```

Stop early when enough high-quality fresh products are found.

Do not blindly scrape hundreds of pages.

## Adaptive stopping

Stop discovery when:

``` text
fresh_unique_pool >= target_pool
```

or:

``` text
query/page budget exhausted
```

This keeps runtime bounded.

------------------------------------------------------------------------

# 11. Deep Candidate Pool

The current pipeline intentionally creates a deeper pool before final
selection.

The upgraded target should use:

``` text
Search cards
     ↓
quality gate
     ↓
within-search dedup
     ↓
global run dedup
     ↓
novelty filter
     ↓
scoring
     ↓
winner ranking
```

Recommended target:

``` text
10 requested posts
↓
100–300 raw candidates
↓
50–100 quality candidates
↓
20–40 strong candidates
↓
10 final products
```

Actual numbers should be configuration values rather than hard-coded
assumptions.

------------------------------------------------------------------------

# 12. Product Data Contract

Every candidate should normalize into a common structure.

``` python
ProductCandidate = {
    "product_id": str,
    "asin": str | None,
    "title": str,
    "brand": str | None,
    "retailer": str,
    "marketplace": str,
    "url": str,
    "image_url": str | None,
    "price": float | None,
    "original_price": float | None,
    "discount_pct": float | None,
    "rating": float | None,
    "reviews": int | None,
    "bought_past_month": int | None,
    "badge": str | None,
    "sponsored": bool | None,
    "category": str,
    "subcategory": str | None,
    "source_query": str,
    "source_page": int | None,
    "scraped_at": datetime,
    "data_confidence": float,
}
```

Never use a missing field as a fabricated zero unless the scoring
function explicitly defines that behavior.

------------------------------------------------------------------------

# 13. Quality Gate 2.0

Keep the existing quality gate and make it more configurable.

Recommended checks:

``` text
✓ real product URL
✓ real product identifier where available
✓ valid image
✓ parseable price
✓ allowed price band
✓ minimum rating
✓ minimum review count when available
✓ product title quality
✓ not obviously unavailable
✓ not duplicate
✓ not previously posted
```

Optional checks:

``` text
✓ recent demand signal
✓ brand quality
✓ image quality
✓ product-type relevance
```

The relaxed fallback must remain available for categories with sparse
data.

------------------------------------------------------------------------

# 14. Exact Product Deduplication

Retain the current three-signal within-run dedup:

1.  ASIN
2.  base image ID
3.  normalized title prefix

Improve it with additional optional signals:

``` text
canonical product URL
normalized brand + model
image perceptual hash
normalized title similarity
```

Do not remove the exact ASIN ledger.

------------------------------------------------------------------------

# 15. Semantic Product Novelty

This is different from ASIN uniqueness.

Example:

``` text
Product A:
Magnetic cable clips

Product B:
Desk cable organizer

Product C:
Cable management clips
```

They may be different ASINs but represent almost the same content idea.

The new novelty system should compare:

``` text
product embedding
+
title embedding
+
category/subcategory
+
historical post/product embeddings
```

Return:

``` text
novelty_score: 0–100
```

Higher means less similar to already-covered products/content.

Novelty is an internal ranking feature, never a consumer claim.

------------------------------------------------------------------------

# 16. Trend Intelligence

The trend subsystem should become persistent rather than being only a
per-run enrichment.

Store trend observations:

``` python
TrendObservation = {
    "keyword": str,
    "category": str,
    "source": str,
    "observed_at": datetime,
    "trend_score": float | None,
    "direction": str,
}
```

Possible direction:

``` text
EXPLODING
RISING
STABLE
DECLINING
UNKNOWN
```

If a trend provider is unavailable:

``` text
trend_score = None
direction = UNKNOWN
```

Do not invent a trend score.

------------------------------------------------------------------------

# 17. Trend Momentum

When multiple observations exist, calculate momentum.

Example conceptual model:

``` text
recent signal
+
short-term growth
+
medium-term consistency
+
historical winner relationship
```

Output:

``` text
trend_momentum = 0–100
```

Do not present this as an external market fact. It is an internal model
score.

------------------------------------------------------------------------

# 18. Product Intelligence Score

Replace the single opaque ranking concept with a transparent composite.

Recommended components:

``` text
quality_score
value_score
purchase_intent_score
instagram_score
content_potential_score
trend_score
novelty_score
commercial_score
performance_prior_score
```

Then:

``` text
product_intelligence_score =
    quality
  + value
  + purchase_intent
  + instagram
  + content_potential
  + trend
  + novelty
  + commercial
  + performance_prior
```

All weights must be configurable.

------------------------------------------------------------------------

# 19. Recommended Initial Weighting

Do not overfit before enough performance data exists.

Initial recommendation:

``` text
Instagram potential       20%
Purchase intent           18%
Quality                   15%
Value                     12%
Content potential         10%
Trend momentum            10%
Novelty                    7%
Commercial                 5%
Performance prior          3%
```

After sufficient historical performance data exists, the system may
learn/tune weights.

Commission should remain deliberately constrained.

------------------------------------------------------------------------

# 20. Winner Score

Create a second score specifically for selection:

``` text
winner_score =
    product_intelligence
    × confidence_adjustment
    × freshness_adjustment
```

The confidence adjustment should reduce the ranking of products where
critical information is missing.

Example:

``` text
Product A
score = 94
confidence = 0.98

Product B
score = 96
confidence = 0.72

Final:
A may outrank B
```

This avoids selecting products based on incomplete evidence.

------------------------------------------------------------------------

# 21. Tier System

Retain the existing:

``` text
S ≥ 90
A ≥ 80
B ≥ 70
C ≥ 60
D < 60
```

But make thresholds configurable.

Add:

``` text
confidence
novelty
trend
winner_probability
```

to the UI.

------------------------------------------------------------------------

# 22. Visual Intelligence

The existing Instagram score uses category-level visual priors.

The upgraded system may optionally analyze product images.

Potential features:

``` text
image_quality
subject_visibility
background_quality
visual_clarity
aesthetic_score
product_distinctiveness
lifestyle_content_potential
carousel_potential
```

Produce:

``` text
visual_score = 0–100
```

If image analysis fails:

``` text
visual_score = category_prior
```

This preserves cold-start and failure tolerance.

------------------------------------------------------------------------

# 23. Content Opportunity Score

A product should not only be good; it should have something to say.

Generate deterministic content opportunities such as:

``` text
problem → solution
before → after
budget upgrade
gift
student use
workspace improvement
room transformation
comparison
top picks
hidden useful product
seasonal use
```

Count valid angles and store them.

------------------------------------------------------------------------

# 24. Winner Engine

The Winner Engine should combine:

``` text
Product Intelligence
        +
Trend Momentum
        +
Novelty
        +
Historical Performance
        +
Content Opportunity
        +
Confidence
```

Output:

``` text
🥇 WINNER
🥈 RUNNER-UP
🥉 ALTERNATIVE
```

The dashboard should explain why.

------------------------------------------------------------------------

# 25. "Find Winners" Dashboard Mode

Add a button next to **Find products**:

``` text
🔥 Find Winners
```

Inputs:

``` text
Category
Subcategory
Budget
Goal
Content style
Novelty requirement
```

Goal options:

``` text
Balanced
Viral potential
High purchase intent
High value
Trending
High commission
Fresh/novel
```

The system should return a ranked shortlist.

------------------------------------------------------------------------

# 26. Why This Product?

Each result should have an expandable explanation.

Example:

``` text
WHY THIS PRODUCT?

🏆 Winner score             93
📸 Instagram potential      96
🛒 Purchase intent          91
💰 Value                    89
📈 Trend momentum           94
✨ Novelty                  95
🧠 Content potential        97

Evidence:
✓ real price available
✓ strong rating
✓ strong review volume
✓ valid image
✓ relevant category
✓ fresh relative to prior content

No claims are shown unless backed by source data.
```

------------------------------------------------------------------------

# 27. Performance Intelligence

This is the most important long-term data upgrade.

For every published post, store observable performance.

``` python
PostPerformance = {
    "post_id": str,
    "account_id": str,
    "captured_at": datetime,
    "reach": int | None,
    "impressions": int | None,
    "likes": int | None,
    "comments": int | None,
    "shares": int | None,
    "saves": int | None,
    "profile_visits": int | None,
    "link_clicks": int | None,
    "orders": int | None,
    "commission": float | None,
}
```

Only populate metrics from a connected source.

------------------------------------------------------------------------

# 28. Performance Funnel

Dashboard:

``` text
Reach
  ↓
Profile visits
  ↓
Storefront visits
  ↓
Product clicks
  ↓
Retailer visits
  ↓
Orders
  ↓
Commission
```

Derived metrics:

``` text
profile_visit_rate
storefront_ctr
product_ctr
conversion_rate
commission_per_click
commission_per_post
commission_per_1000_reach
```

If an upstream metric is unavailable, the downstream metric should also
remain unavailable.

------------------------------------------------------------------------

# 29. Affiliate Revenue Intelligence

The business dashboard should eventually provide:

``` text
Today
This week
This month
```

and:

``` text
Revenue by:
- post
- product
- category
- subcategory
- content style
- price band
- retailer
```

The most important metric is not simply commission.

Use:

``` text
commission / 1,000 reach
commission / post
commission / click
```

where sufficient data exists.

------------------------------------------------------------------------

# 30. Winner Learning

After publishing, connect the product to its result.

Example:

``` text
Product:
Desk Cable Organizer

Content:
"5 desk upgrades..."

Performance:
Reach: 25,000
Saves: 1,400
Clicks: 520
Orders: 31
Commission: ₹2,100
```

Store the relationship.

Then future discovery can learn:

``` text
desk organization
+
₹500–₹1,000
+
problem/solution hook
+
visual transformation
```

is a historically strong combination.

------------------------------------------------------------------------

# 31. RAG 2.0

Keep the existing pgvector RAG architecture.

Add separate memory concepts:

``` text
PRODUCT MEMORY
CONTENT MEMORY
PERFORMANCE MEMORY
TREND MEMORY
WINNER MEMORY
```

Do not put all information into one undifferentiated embedding.

------------------------------------------------------------------------

# 32. Product Memory

Store:

``` text
product
category
subcategory
price band
scores
content angles
posted status
similar products
performance
```

------------------------------------------------------------------------

# 33. Content Memory

Store:

``` text
hook
caption style
content format
category
products
hashtags
post date
performance
```

This enables queries such as:

> Which hooks performed best for Home?

------------------------------------------------------------------------

# 34. Winner Memory

Store high-performing patterns:

``` text
category
subcategory
price range
product type
content style
hook type
performance metrics
```

Use these as priors, not guarantees.

------------------------------------------------------------------------

# 35. A/B Content Testing

Add content styles:

``` text
DEAL_DROP
STORY
LISTICLE
PROBLEM_SOLUTION
QUESTION
TRANSFORMATION
GIFT_GUIDE
BUDGET
PREMIUM
VIRAL_FIND
```

Record the style with every post.

After enough observations, compare performance.

Example:

``` text
Problem/Solution
+27% click rate

Price Hook
+18% save rate

Story
+11% comments
```

Do not declare a winner from tiny samples.

------------------------------------------------------------------------

# 36. Content Experiment Engine

An experiment should define:

``` python
Experiment = {
    "experiment_id": str,
    "variable": "hook_style",
    "variants": [],
    "start_at": datetime,
    "end_at": datetime | None,
    "status": str,
    "minimum_sample": int,
}
```

The system should avoid changing multiple major variables simultaneously
when the goal is to learn causally.

------------------------------------------------------------------------

# 37. Content Calendar

Add an intelligent calendar.

Example:

``` text
MON — Home
TUE — Electronics
WED — Fashion
THU — Books
FRI — Home
SAT — Trending
SUN — Weekly Winners
```

But the actual schedule should be driven by:

``` text
historical category performance
audience response
fresh inventory
trend momentum
account posting constraints
```

No category should be forced if no quality products exist.

------------------------------------------------------------------------

# 38. Content Studio

Before publishing, show:

``` text
Product carousel
Caption
Hashtags
Affiliate links
Disclosure
Content style
Winner score
```

Actions:

``` text
Edit
Regenerate
Save draft
Schedule
Publish
Discard
```

------------------------------------------------------------------------

# 39. Instagram Automation Layer

The existing Instagram backend should remain a separate execution layer.

Recommended architecture:

``` text
Affiliate Intelligence API
        ↓
Publishing Queue
        ↓
Instagram Automation Service
        ↓
Playwright
        ↓
Instagram
```

The intelligence service decides **what** to publish.

The automation service decides **how** to execute the UI workflow.

This separation is important.

------------------------------------------------------------------------

# 40. Publishing Queue

Create a persistent queue:

``` python
PublishJob = {
    "job_id": str,
    "account_id": str,
    "post_id": str,
    "scheduled_for": datetime,
    "status": str,
    "attempt_count": int,
    "last_error": str | None,
}
```

Statuses:

``` text
DRAFT
QUEUED
SCHEDULED
RUNNING
PUBLISHED
FAILED
CANCELLED
```

------------------------------------------------------------------------

# 41. Safe Publishing State Machine

``` text
DRAFT
  ↓
APPROVED
  ↓
QUEUED
  ↓
SCHEDULED
  ↓
RUNNING
  ↓
PUBLISHED
```

Failure:

``` text
RUNNING
  ↓
FAILED
  ↓
RETRYABLE / NEEDS_REVIEW
```

Never blindly retry an uncertain Instagram action if the system cannot
determine whether the post already succeeded.

------------------------------------------------------------------------

# 42. Account Safety Layer

Create a dedicated account health service.

Track:

``` text
login status
session status
recent automation errors
recent failed actions
publishing interval
queue size
last successful post
last verification
```

Add:

``` text
🟢 HEALTHY
🟡 ATTENTION
🔴 STOPPED
```

Add a global:

``` text
🛑 EMERGENCY STOP
```

This should disable new publishing jobs.

------------------------------------------------------------------------

# 43. Engagement Intelligence

The engagement layer should classify inbound comments/messages.

Intent examples:

``` text
PRODUCT_LINK
PRICE
WHICH_ONE
AVAILABILITY
GENERAL_QUESTION
POSITIVE
NEGATIVE
SPAM
UNKNOWN
```

Responses should use only known product information.

------------------------------------------------------------------------

# 44. Comment → Product Link

Example:

``` text
User:
"link?"

System:
intent = PRODUCT_LINK

↓
identify referenced post/product
↓
retrieve approved affiliate/storefront URL
↓
send response
```

Do not send a guessed product URL.

------------------------------------------------------------------------

# 45. Storefront Upgrade

Keep the current rule:

> public catalog = posted products only.

Add intelligent sections:

``` text
🔥 Trending
🏆 Top Picks
💰 Under ₹500
💰 Under ₹1,000
⭐ Best Rated
🆕 Fresh Finds
🎁 Gifts
🏠 Home
🎧 Tech
👕 Fashion
```

Only display sections when enough real products exist.

------------------------------------------------------------------------

# 46. Storefront Ranking

Rank posted products using:

``` text
recent performance
winner score
freshness
trend
availability where verified
category relevance
```

Do not claim "most clicked" unless click data is actually available.

------------------------------------------------------------------------

# 47. Multi-Retailer Architecture

The current Amazon implementation should be abstracted behind a common
interface.

``` python
class RetailerAdapter:
    async def search(self, query, page=1): ...
    async def normalize_product(self, raw): ...
    async def build_affiliate_link(self, product): ...
    async def health_check(self): ...
```

Potential future adapters:

``` text
Amazon
Flipkart
Myntra
Meesho
Croma
AJIO
```

Do not implement a retailer until its data and affiliate-link method are
actually available.

------------------------------------------------------------------------

# 48. Retailer Comparison

For the same normalized product:

``` text
Product
 ├── Amazon
 ├── Flipkart
 └── Other supported retailer
```

Compare:

``` text
price
availability
rating
reviews
affiliate capability
commission
```

Only compare fields actually available from both sources.

------------------------------------------------------------------------

# 49. Affiliate Link Service

Move link generation behind a common service:

``` python
AffiliateLinkService.create_link(
    retailer,
    product,
    account
)
```

The service must:

1.  use configured affiliate credentials
2.  build from the real product identifier/URL
3.  validate the generated URL
4.  record the source retailer
5.  never invent tracking parameters

------------------------------------------------------------------------

# 50. API Architecture

Keep existing endpoints and add the following.

## Existing

``` text
GET  /api/generate
GET  /api/taxonomy
GET  /api/collections
POST /api/posts
GET  /api/posts
GET  /api/health
GET  /api/config
GET  /api/pipeline
GET  /api/categories
GET  /api/stats
GET  /api/history
```

## New discovery APIs

``` text
POST /api/discovery/run
GET  /api/discovery/jobs
GET  /api/discovery/queries
GET  /api/discovery/candidates
```

## Winner APIs

``` text
GET /api/winners
GET /api/winners/:id
POST /api/winners/search
```

## Trend APIs

``` text
GET /api/trends
GET /api/trends/:category
```

## Performance APIs

``` text
GET /api/performance/overview
GET /api/performance/posts
GET /api/performance/products
GET /api/performance/categories
```

## Content APIs

``` text
POST /api/content/generate
POST /api/content/preview
POST /api/content/save
POST /api/content/schedule
```

## Publishing APIs

``` text
GET  /api/publishing/queue
POST /api/publishing/queue
POST /api/publishing/cancel
POST /api/publishing/emergency-stop
```

## Intelligence APIs

``` text
GET /api/intelligence/insights
GET /api/intelligence/recommendations
GET /api/intelligence/winners
```

------------------------------------------------------------------------

# 51. Updated LangGraph Architecture

The current graph should evolve incrementally.

Target graph:

``` text
START
  ↓
load_strategy
  ↓
discover_queries
  ↓
scrape_retailers
  ↓
normalize_products
  ↓
quality_gate
  ↓
dedup_exact
  ↓
dedup_semantic
  ↓
search_trends
  ↓
retrieve_memory
  ↓
calculate_scores
  ↓
predict_winners
  ↓
select_products
  ↓
compose_content
  ↓
validate_content
  ↓
build_affiliate_links
  ↓
create_post_draft
  ↓
store_results
  ↓
QUEUE / PREVIEW / POST
  ↓
measure_performance
  ↓
learn
  ↓
END
```

Performance measurement can also run asynchronously after publishing
rather than blocking generation.

------------------------------------------------------------------------

# 52. Recommended Node Responsibilities

## `load_strategy`

Load:

-   category
-   budget
-   goal
-   style
-   account
-   marketplace
-   quality thresholds

## `discover_queries`

Select fresh search intents.

## `scrape_retailers`

Retrieve raw product candidates.

## `normalize_products`

Convert retailer-specific data into the common schema.

## `quality_gate`

Remove weak or invalid candidates.

## `dedup_exact`

ASIN/image/title/URL dedup.

## `dedup_semantic`

Remove content-level duplicates.

## `search_trends`

Retrieve current trend signals.

## `retrieve_memory`

Retrieve historical winners and content patterns.

## `calculate_scores`

Run deterministic scoring.

## `predict_winners`

Combine score, confidence, trend, novelty and performance priors.

## `select_products`

Choose final products.

## `compose_content`

Generate structured caption/hashtags/content style.

## `validate_content`

Check every factual claim.

## `build_affiliate_links`

Generate verified tracking links.

## `create_post_draft`

Create a reviewable post object.

## `store_results`

Persist products, content, memory and dedup information.

## `learn`

Update historical performance associations.

------------------------------------------------------------------------

# 53. State Contract 2.0

Extend `BotState` with:

``` python
{
    "strategy": {},
    "discovery_queries": [],
    "raw_products": [],
    "normalized_products": [],
    "quality_products": [],
    "fresh_products": [],
    "novel_products": [],
    "trend_signals": [],
    "rag_context": [],
    "rag_product_ideas": [],
    "ranked_products": [],
    "winner_candidates": [],
    "selected_products": [],
    "generated_content": {},
    "affiliate_links": [],
    "post_draft": {},
    "publish_job": {},
    "performance": {},
    "learning_events": [],
    "errors": [],
    "stream_log": []
}
```

Existing state fields should remain compatible where possible.

------------------------------------------------------------------------

# 54. Database Architecture

Continue using PostgreSQL + pgvector.

Recommended relational tables:

``` text
accounts
products
product_sources
product_observations
seen_products
posts
post_products
post_performance
affiliate_clicks
affiliate_orders
trend_observations
discovery_queries
discovery_runs
content_variants
experiments
publish_jobs
engagement_events
learning_events
```

Vector collections:

``` text
product_memory
content_memory
performance_memory
winner_memory
```

The exact pgvector implementation can use existing infrastructure.

------------------------------------------------------------------------

# 55. Product Tables

## `products`

``` text
id
canonical_key
asin
title
brand
category
subcategory
retailer
marketplace
canonical_url
image_url
created_at
updated_at
```

## `product_observations`

``` text
id
product_id
price
original_price
discount_pct
rating
reviews
bought_past_month
badge
observed_at
source
```

This separates the product identity from changing marketplace
observations.

------------------------------------------------------------------------

# 56. Posts

``` text
posts
-----
id
account_id
caption
hashtags
content_style
status
created_at
scheduled_for
published_at
```

## `post_products`

``` text
post_id
product_id
position
affiliate_url
selection_score
```

This creates a permanent relationship between content and products.

------------------------------------------------------------------------

# 57. Performance Storage

``` text
post_performance
----------------
post_id
captured_at
reach
impressions
likes
comments
shares
saves
profile_visits
link_clicks
orders
commission
source
```

Allow nullable values.

------------------------------------------------------------------------

# 58. Discovery Query Memory

``` text
discovery_queries
-----------------
query
category
subcategory
last_used_at
usage_count
fresh_yield
winner_yield
duplicate_rate
priority_score
```

This makes discovery adaptive.

------------------------------------------------------------------------

# 59. Configuration

New environment variables should be grouped.

## Discovery

``` text
DISCOVERY_MAX_QUERIES
DISCOVERY_MAX_PAGES
DISCOVERY_TARGET_POOL
DISCOVERY_CONCURRENCY
```

## Novelty

``` text
NOVELTY_ENABLED
NOVELTY_THRESHOLD
NOVELTY_WEIGHT
```

## Trends

``` text
TREND_ENABLED
TREND_MAX_KEYWORDS
TREND_LOOKBACK_DAYS
```

## Visual

``` text
VISUAL_SCORING_ENABLED
VISUAL_SCORE_FALLBACK
```

## Performance

``` text
PERFORMANCE_ENABLED
PERFORMANCE_LOOKBACK_DAYS
```

## Winner engine

``` text
WINNER_MIN_CONFIDENCE
WINNER_SCORE_THRESHOLD
```

Every setting should have a safe default.

------------------------------------------------------------------------

# 60. Error Handling

Use three levels.

## Level 1 --- Recoverable enrichment

Examples:

``` text
trend API unavailable
visual analysis unavailable
RAG empty
optional performance unavailable
```

Continue.

## Level 2 --- Candidate-level failure

Example:

``` text
one product has malformed data
```

Drop that candidate and continue.

## Level 3 --- Pipeline-critical failure

Examples:

``` text
scraper returns nothing
database unavailable
critical dedup unavailable
content validation fails
```

Stop safely.

------------------------------------------------------------------------

# 61. Validation Layer

Before publishing, validate:

``` text
✓ product still exists in database
✓ affiliate URL exists
✓ affiliate URL belongs to approved retailer
✓ price claims match stored observation
✓ discount claims match stored observation
✓ rating claims match stored observation
✓ no fabricated numbers
✓ required disclosure present
✓ product has not already been published
✓ post has not already been successfully published
```

------------------------------------------------------------------------

# 62. Content Schema 2.0

Extend the existing structured output.

``` python
PinBatch = {
    "style": str,
    "picks": list[int],
    "caption": str,
    "hashtags": list[str],
    "hooks": list[str],
    "micro_captions": list[str],
    "content_angles": list[str],
}
```

The standard path can still be one LLM call.

------------------------------------------------------------------------

# 63. Caption Validation

After generation:

``` text
extract numeric claims
       ↓
compare with source product rows
       ↓
reject mismatches
       ↓
sanitize hashtags
       ↓
force disclosure
       ↓
publish-ready
```

This is stronger than relying only on the prompt.

------------------------------------------------------------------------

# 64. Hashtag Engine

Keep the current 15--25 tag range.

Build a category tag bank:

``` text
broad
category
subcategory
intent
audience
branded
```

The LLM may select from the bank and add appropriate tags.

Post-process:

``` text
lowercase
remove #
deduplicate
cap maximum
ensure required disclosure
```

------------------------------------------------------------------------

# 65. Smart Scheduling

Scheduling should consider:

``` text
configured posting windows
minimum interval
existing queue
account state
content category
historical performance
```

Do not claim a particular time is "best" unless enough historical data
supports the inference.

------------------------------------------------------------------------

# 66. Competitor Intelligence --- Optional Module

This should be isolated from the core product pipeline.

Potential data:

``` text
account
public post metadata
posting frequency
content category
observable engagement
content style
product/topic recurrence
```

Use it as a discovery signal.

Never copy another creator's content.

The goal is:

``` text
detect market patterns
```

not:

``` text
clone content
```

------------------------------------------------------------------------

# 67. AI Agents

Recommended logical agents:

``` text
Product Scout
Product Analyst
Trend Analyst
Novelty Analyst
Winner Engine
Content Strategist
Copywriter
Hashtag Selector
Publishing Agent
Engagement Agent
Performance Analyst
Learning Agent
```

These do not all need separate LLM calls.

Prefer deterministic code wherever possible.

------------------------------------------------------------------------

# 68. Agent Design Rule

Use:

``` text
CODE
```

for:

-   arithmetic
-   scoring
-   deduplication
-   validation
-   filtering
-   URLs
-   state transitions
-   database writes

Use:

``` text
LLM
```

for:

-   content strategy
-   wording
-   semantic categorization where useful
-   creative hooks
-   structured explanation

This keeps the system cheaper and more reliable.

------------------------------------------------------------------------

# 69. Cost Control

The target architecture must not turn every operation into an LLM call.

Recommended:

``` text
Scraping        deterministic
Normalization   deterministic
Quality         deterministic
Dedup           deterministic
Scoring         deterministic
Trend           external data
Visual          optional
Winner score    deterministic
Content         one LLM call
Validation      deterministic first
RAG             embeddings
```

This preserves the current cost discipline.

------------------------------------------------------------------------

# 70. Caching

Cache:

``` text
taxonomy
discovery queries
recent product observations
trend results
image analysis
affiliate URL results
```

Use TTLs appropriate to the data.

Never cache dynamic price information indefinitely.

------------------------------------------------------------------------

# 71. Observability

Every run should have:

``` text
run_id
account_id
started_at
completed_at
duration
queries_used
raw_count
quality_count
dedup_count
novel_count
winner_count
selected_count
LLM_time
scrape_time
errors
```

Dashboard:

``` text
RUN #1842
─────────────────
Queries             12
Raw products       312
Quality             87
Fresh               64
Novel               41
Winner candidates   18
Selected            10

Duration            43s
Status              SUCCESS
```

------------------------------------------------------------------------

# 72. Discovery Efficiency Metrics

Track:

``` text
fresh_product_yield
quality_yield
duplicate_rate
novelty_yield
winner_yield
query_success_rate
```

This allows the system to learn which search queries are valuable.

------------------------------------------------------------------------

# 73. Testing Strategy

Every upgrade must include tests.

## Unit tests

Test:

``` text
price parsing
rating parsing
review parsing
discount parsing
ASIN extraction
image canonicalization
deduplication
score calculations
tier calculations
budget bundles
affiliate URLs
caption validation
hashtag normalization
```

## Integration tests

Test:

``` text
scraper → normalizer
normalizer → scorer
scorer → winner engine
winner engine → compose
compose → validation
validation → storage
```

## End-to-end dry-run

Use:

``` text
mock retailer data
mock trend data
mock LLM response
test database
dry-run publishing
```

No real Instagram post should occur.

------------------------------------------------------------------------

# 74. Regression Tests

Before every release verify:

``` text
✓ old `/api/generate` still works
✓ old category mode works
✓ keyword mode works
✓ existing ASINs remain blocked
✓ storefront excludes unposted products
✓ #ad remains present
✓ dry-run does not post
✓ malformed candidates don't crash runs
✓ RAG empty state works
✓ trend API failure works
```

------------------------------------------------------------------------

# 75. Security

Protect:

``` text
database credentials
OpenAI keys
trend provider keys
affiliate credentials
Instagram session data
cookies
account identifiers
```

Never return them through:

``` text
/api/config
/api/health
/api/stats
logs
frontend
errors
```

Sanitize exception messages before displaying them publicly.

------------------------------------------------------------------------

# 76. Account Architecture

If multiple Instagram accounts are eventually supported:

``` text
Account
 ├── credentials/session
 ├── affiliate configuration
 ├── taxonomy preferences
 ├── posting rules
 ├── content style
 ├── performance history
 └── product exposure history
```

Deduplication should support either:

``` text
global uniqueness
```

or:

``` text
per-account uniqueness
```

as an explicit policy.

Do not silently change the current behavior.

------------------------------------------------------------------------

# 77. Product Exposure Graph

Eventually represent:

``` text
ACCOUNT
   ↓
POST
   ↓
PRODUCT
   ↓
CATEGORY
   ↓
CONTENT STYLE
   ↓
PERFORMANCE
```

This enables questions such as:

> Which product types perform best for this account?

and:

> Which content style performs best for electronics?

------------------------------------------------------------------------

# 78. Intelligent Recommendations

The dashboard should eventually display:

``` text
NEXT BEST ACTION

🔥 Strong opportunity detected

Category:
Home

Subcategory:
Desk Organization

Reason:
High fresh-product yield
+ rising trend signal
+ historically strong category performance

Recommended:
Find Winners
```

Recommendations must be grounded in actual stored data.

------------------------------------------------------------------------

# 79. "What Should I Post Today?"

Add a one-click assistant:

``` text
WHAT SHOULD I POST TODAY?
```

It evaluates:

``` text
fresh products
trend
historical performance
content calendar
recent categories
novelty
account constraints
```

Then returns:

``` text
Recommended category
Recommended product
Recommended content style
Recommended hook
Reason
Confidence
```

------------------------------------------------------------------------

# 80. "Find Me a Product Under ₹X"

Support:

``` text
Find me the best desk product under ₹999.
```

Pipeline:

``` text
query
 ↓
discovery
 ↓
quality
 ↓
novelty
 ↓
winner score
 ↓
return top candidates
```

Budget arithmetic must remain truthful.

------------------------------------------------------------------------

# 81. Collections

Keep existing real price bands:

``` text
Under ₹500
Under ₹1K
Under ₹1.5K
Under ₹2K
Under ₹3K
Under ₹5K
```

Add:

``` text
Trending
Best Rated
Fresh Finds
Best Value
Top Winners
```

Only when data supports the label.

------------------------------------------------------------------------

# 82. Budget Bundles

Retain the existing budget-truth rule.

For:

``` text
Setup under ₹3,000
```

calculate:

``` text
price1 + price2 + price3 <= 3000
```

Use real current/observed prices.

Never round down to make the claim fit.

------------------------------------------------------------------------

# 83. Product Lifecycle

Every product should move through:

``` text
DISCOVERED
   ↓
QUALIFIED
   ↓
RANKED
   ↓
SELECTED
   ↓
DRAFTED
   ↓
QUEUED
   ↓
PUBLISHED
   ↓
MEASURED
   ↓
LEARNED
```

Failure states:

``` text
REJECTED
DUPLICATE
STALE
INVALID
FAILED
```

------------------------------------------------------------------------

# 84. Product Freshness

A product can be unique by ASIN but still stale.

Track:

``` text
first_seen_at
last_seen_at
last_posted_at
times_seen
times_rejected
times_selected
```

This enables freshness ranking.

------------------------------------------------------------------------

# 85. Stale Product Handling

If a product was discovered long ago:

``` text
refresh marketplace observation
```

before making current price claims.

Never reuse an old price as a current price without an appropriate
freshness policy.

------------------------------------------------------------------------

# 86. Data Confidence

Each product should have:

``` text
identity_confidence
price_confidence
image_confidence
social_proof_confidence
affiliate_confidence
```

Then:

``` text
overall_confidence
```

This allows the winner engine to avoid unreliable candidates.

------------------------------------------------------------------------

# 87. Learning Without Overfitting

The system should not immediately conclude:

> "This product style is viral."

after one post.

Use:

``` text
minimum sample sizes
confidence intervals where practical
time windows
category-specific comparisons
```

Keep a distinction between:

``` text
observation
```

and:

``` text
prediction
```

------------------------------------------------------------------------

# 88. Performance-Based Weight Learning

Once sufficient data exists, allow the system to learn weights.

Initial:

``` text
manual weights
```

Later:

``` text
historical data
 ↓
feature analysis
 ↓
model calibration
 ↓
weight recommendation
 ↓
human approval
 ↓
new weights
```

Do not automatically deploy a new model without validation.

------------------------------------------------------------------------

# 89. Recommended Machine Learning Roadmap

Do not start with a complicated model.

## Phase 1

Deterministic scoring.

## Phase 2

Historical averages.

## Phase 3

Feature-based probability model.

## Phase 4

Calibrated winner prediction.

## Phase 5

Contextual recommendation.

Possible features:

``` text
price
rating
reviews
discount
category
subcategory
visual score
trend
novelty
content style
hook style
posting day
historical category performance
```

Target:

``` text
P(high_performance | product, content, account)
```

Not a guaranteed outcome.

------------------------------------------------------------------------

# 90. Recommended Folder Architecture

A target code layout:

``` text
affiliate-rag-bot/
│
├── agents/
│   ├── product_scout.agents.md
│   ├── product_analyst.agents.md
│   ├── trend_analyst.agents.md
│   ├── novelty_analyst.agents.md
│   ├── content_strategist.agents.md
│   ├── performance_analyst.agents.md
│   └── learning_agent.agents.md
│
├── graph/
│   ├── workflow.py
│   ├── nodes.py
│   └── state.py
│
├── discovery/
│   ├── planner.py
│   ├── queries.py
│   ├── normalizer.py
│   ├── quality.py
│   ├── novelty.py
│   └── winner.py
│
├── tools/
│   ├── amazon.py
│   ├── search.py
│   ├── visual.py
│   └── retailers/
│       ├── base.py
│       ├── amazon.py
│       ├── flipkart.py
│       └── ...
│
├── scoring/
│   ├── discovery.py
│   ├── performance.py
│   └── calibration.py
│
├── chains/
│   ├── compose.py
│   └── prompts.py
│
├── rag/
│   ├── store.py
│   ├── dedup.py
│   ├── posts.py
│   ├── products.py
│   └── performance.py
│
├── publishing/
│   ├── queue.py
│   ├── scheduler.py
│   └── validator.py
│
├── performance/
│   ├── collector.py
│   ├── metrics.py
│   └── learner.py
│
├── engagement/
│   ├── classifier.py
│   └── responder.py
│
├── server.py
├── pipeline_runner.py
├── config.py
└── tests/
```

The actual migration should follow the current repository structure
rather than blindly creating all folders at once.

------------------------------------------------------------------------

# 91. Recommended Implementation Phases

## Phase 0 --- Baseline

Before changes:

``` text
✓ backup database
✓ tag current code version
✓ run current test suite
✓ capture current API responses
✓ verify current Instagram dry-run
```

------------------------------------------------------------------------

# 92. Phase 1 --- Discovery Upgrade

Implement first:

``` text
1. query rotation
2. subcategory mining
3. pagination
4. adaptive stopping
5. stronger normalization
6. discovery query statistics
```

Success criteria:

``` text
more fresh products
lower duplicate rate
no fabricated data
same existing API compatibility
```

------------------------------------------------------------------------

# 93. Phase 2 --- Novelty + Winner Engine

Implement:

``` text
1. semantic novelty
2. product intelligence score
3. confidence score
4. winner score
5. why-selected explanation
```

Success criteria:

``` text
fewer conceptually repetitive posts
stronger final candidates
transparent scoring
```

------------------------------------------------------------------------

# 94. Phase 3 --- Trend Intelligence

Implement:

``` text
1. persistent trend observations
2. trend momentum
3. category trends
4. trend-aware discovery
```

Success criteria:

``` text
fresh search queries
trend-aware product selection
graceful provider failure
```

------------------------------------------------------------------------

# 95. Phase 4 --- Content Intelligence

Implement:

``` text
1. content styles
2. content angle selection
3. A/B variants
4. hashtag bank
5. stronger validation
```

Success criteria:

``` text
more controlled experiments
no factual hallucinations
performance-linked content records
```

------------------------------------------------------------------------

# 96. Phase 5 --- Publishing Platform

Implement:

``` text
1. post drafts
2. publishing queue
3. scheduler
4. state machine
5. emergency stop
6. account health
```

Success criteria:

``` text
safe publishing
recoverable failures
no accidental duplicate posts
clear operational status
```

------------------------------------------------------------------------

# 97. Phase 6 --- Performance Loop

Implement:

``` text
1. performance storage
2. metrics dashboard
3. product-performance mapping
4. content-performance mapping
5. winner memory
```

Success criteria:

``` text
every published post can be associated with measurable results
```

------------------------------------------------------------------------

# 98. Phase 7 --- Learning

Implement:

``` text
1. performance priors
2. winner prediction
3. query optimization
4. category optimization
5. content-style optimization
```

Success criteria:

``` text
future selections improve using real historical evidence
```

------------------------------------------------------------------------

# 99. Phase 8 --- Multi-Retailer

Only after the core Amazon flow is stable:

``` text
1. adapter interface
2. second retailer
3. normalized product comparison
4. affiliate-link abstraction
5. retailer ranking
```

Do not attempt every retailer simultaneously.

------------------------------------------------------------------------

# 100. Phase 9 --- Competitor Intelligence

Only after product/performance intelligence works:

``` text
1. account watchlist
2. public metadata collection
3. topic detection
4. recurring product patterns
5. trend signals
```

Keep this modular.

------------------------------------------------------------------------

# 101. Phase 10 --- Advanced Prediction

Finally:

``` text
historical data
 ↓
feature engineering
 ↓
model training
 ↓
validation
 ↓
calibration
 ↓
winner prediction
```

The system should remain useful even if the prediction model is
disabled.

------------------------------------------------------------------------

# 102. Frontend --- New Discover Screen

Recommended layout:

``` text
┌─────────────────────────────────────────────┐
│ Discover                                     │
│                                             │
│ Category       [ Home ▼ ]                   │
│ Goal           [ Balanced ▼ ]               │
│ Budget         [ ₹300 ] — [ ₹2,000 ]       │
│                                             │
│ [ Find Products ]   [ 🔥 Find Winners ]     │
└─────────────────────────────────────────────┘
```

Below:

``` text
Fresh products found
Novel products
Winner candidates
Trending categories
```

------------------------------------------------------------------------

# 103. Product Card

``` text
┌──────────────────────────────────────────┐
│ IMAGE                                    │
│                                          │
│ Product Name                             │
│ ₹799       4.6★       4,821 reviews     │
│                                          │
│ 🏆 Winner Score       92                 │
│ 📈 Trend              88                 │
│ ✨ Novelty             95                 │
│ 📸 Instagram          94                 │
│                                          │
│ [ Why? ] [ Add ] [ Preview ]             │
└──────────────────────────────────────────┘
```

------------------------------------------------------------------------

# 104. Winner Detail Screen

``` text
WINNER ANALYSIS

Product
─────────────

Overall                 93
Quality                 91
Value                   89
Purchase intent         94
Instagram               96
Trend                   88
Novelty                 95
Content potential       97

Historical prior        Strong
Confidence              High

Recommended style:
Problem → Solution

Recommended angle:
Desk transformation
```

------------------------------------------------------------------------

# 105. Content Calendar UI

Use:

``` text
Calendar
Week
Day
Status
Category
Product
Style
Performance
```

Actions:

``` text
Drag
Reschedule
Preview
Cancel
Publish now
```

Respect minimum posting intervals.

------------------------------------------------------------------------

# 106. Revenue Dashboard

``` text
Revenue
────────────────────────

Today       ₹X
7 days      ₹X
30 days     ₹X

Clicks      X
Orders      X
Commission  ₹X

Top category
Top product
Top post
```

Unknown metrics remain unknown.

------------------------------------------------------------------------

# 107. Intelligence Dashboard

``` text
WHAT IS WORKING?

🏠 Home
Best performing category

💰 ₹500–₹1,000
Best price band

🔥 Problem/Solution
Best content style

📸 Transformation
Best hook family

🧲 Desk Organization
Strong product family

📈 Rising
Trend opportunity
```

Every insight should link back to supporting observations.

------------------------------------------------------------------------

# 108. Automation Modes

Support:

``` text
MANUAL
```

User selects everything.

``` text
ASSISTED
```

System recommends, user approves.

``` text
AUTOPILOT
```

System discovers, generates, schedules and publishes within configured
constraints.

Default should remain conservative.

------------------------------------------------------------------------

# 109. Autopilot Safety

Autopilot must have:

``` text
maximum posts/day
minimum delay
approved categories
approved price ranges
approved retailers
dry-run option
approval mode
emergency stop
failure threshold
```

If repeated failures occur:

``` text
AUTOPILOT → PAUSED
```

Require review before resuming.

------------------------------------------------------------------------

# 110. Human Approval Mode

Recommended workflow:

``` text
DISCOVER
 ↓
AI RECOMMENDATION
 ↓
USER APPROVAL
 ↓
SCHEDULE
 ↓
PUBLISH
```

This is the safest mode during development.

------------------------------------------------------------------------

# 111. No-Data Behavior

If there are:

``` text
no trends
```

use no trend bonus.

If there are:

``` text
no historical winners
```

use baseline scoring.

If there is:

``` text
no visual analysis
```

use category prior.

If there is:

``` text
no performance data
```

do not invent performance priors.

If there are:

``` text
not enough products
```

return fewer products.

------------------------------------------------------------------------

# 112. Critical Anti-Hallucination Rule

The LLM must never be the source of marketplace facts.

Input:

``` text
structured product evidence
```

Output:

``` text
creative interpretation
```

Then deterministic validation verifies factual claims.

------------------------------------------------------------------------

# 113. Product Selection Example

Input:

``` text
100 candidates
```

After processing:

``` text
100 raw
 ↓
82 quality
 ↓
64 fresh
 ↓
45 semantically novel
 ↓
25 high score
 ↓
12 winner candidates
 ↓
10 selected
```

If only 7 meet the required quality:

``` text
7 selected
```

Never manufacture 3 more.

------------------------------------------------------------------------

# 114. Complete Target Architecture

``` text
                         ┌─────────────────────┐
                         │      STUDIO UI      │
                         └──────────┬──────────┘
                                    │
                         REST / JSON API
                                    │
                         ┌──────────▼──────────┐
                         │    ORCHESTRATOR     │
                         │      LangGraph      │
                         └──────────┬──────────┘
                                    │
          ┌─────────────────────────┼──────────────────────────┐
          │                         │                          │
          ▼                         ▼                          ▼
 ┌────────────────┐       ┌──────────────────┐       ┌────────────────┐
 │ DISCOVERY      │       │ INTELLIGENCE     │       │ CONTENT        │
 │                │       │                  │       │                │
 │ query planner  │       │ scoring          │       │ strategy       │
 │ Amazon         │       │ trends           │       │ LLM            │
 │ retailers      │       │ novelty          │       │ validation     │
 │ normalization  │       │ visual           │       │ experiments    │
 └───────┬────────┘       │ performance      │       └───────┬────────┘
         │                │ winner engine     │               │
         │                └────────┬─────────┘               │
         │                         │                          │
         └─────────────────────────┼──────────────────────────┘
                                   │
                         ┌─────────▼─────────┐
                         │   POST / QUEUE    │
                         └─────────┬─────────┘
                                   │
                         ┌─────────▼─────────┐
                         │ INSTAGRAM SERVICE │
                         │     Playwright    │
                         └─────────┬─────────┘
                                   │
                               Instagram
                                   │
                         ┌─────────▼─────────┐
                         │   PERFORMANCE     │
                         │     COLLECTOR     │
                         └─────────┬─────────┘
                                   │
                         ┌─────────▼─────────┐
                         │ LEARNING / MEMORY │
                         │ Postgres+pgvector │
                         └─────────┬─────────┘
                                   │
                                   └──────→ DISCOVERY
```

------------------------------------------------------------------------

# 115. Data Flow

``` text
User strategy
      ↓
Query planner
      ↓
Retailer discovery
      ↓
Raw candidates
      ↓
Normalization
      ↓
Quality gate
      ↓
Exact dedup
      ↓
Semantic novelty
      ↓
Trend enrichment
      ↓
RAG retrieval
      ↓
Deterministic scoring
      ↓
Winner engine
      ↓
LLM content generation
      ↓
Content validation
      ↓
Affiliate link generation
      ↓
Post draft
      ↓
Approval / schedule / autopilot
      ↓
Instagram
      ↓
Performance
      ↓
Learning
      ↓
RAG + scoring priors
      ↓
Next discovery
```

------------------------------------------------------------------------

# 116. Definition of Done

The upgrade is not complete when the dashboard looks better.

It is complete when:

``` text
✓ discovery finds more fresh candidates
✓ query rotation works
✓ pagination works
✓ exact dedup still works
✓ semantic novelty works
✓ scores are transparent
✓ winner ranking works
✓ trends are persisted
✓ content styles are measurable
✓ posts have performance records
✓ storefront contains only posted products
✓ affiliate URLs are real
✓ content factual claims are validated
✓ dry-run is safe
✓ publishing queue is recoverable
✓ account safety controls work
✓ failures do not corrupt state
✓ historical performance influences future ranking
✓ cold-start still works
✓ no fabricated product facts exist
```

------------------------------------------------------------------------

# 117. Recommended Build Order

Do **not** build everything simultaneously.

Build in this exact order:

``` text
1. Discovery query rotation
2. Pagination
3. Candidate normalization
4. Discovery statistics
5. Semantic novelty
6. Product Intelligence Score
7. Winner Engine
8. Why-this-product UI
9. Persistent trend intelligence
10. Content styles
11. Content validation
12. Publishing queue
13. Performance database
14. Performance dashboard
15. Winner learning
16. Intelligent calendar
17. Storefront ranking
18. Engagement intelligence
19. Multi-retailer abstraction
20. Competitor intelligence
21. ML prediction
```

------------------------------------------------------------------------

# 118. First Milestone --- Highest ROI

The first implementation milestone should be:

## "Winner Discovery Engine"

It should provide:

``` text
Category
 ↓
10–20 rotating search intents
 ↓
multiple result pages
 ↓
hundreds of candidates
 ↓
quality gate
 ↓
exact dedup
 ↓
semantic novelty
 ↓
trend enrichment
 ↓
product intelligence score
 ↓
winner score
 ↓
Top 10
```

The UI should show:

``` text
🔥 Find Winners
```

and return:

``` text
🥇 Winner
🥈 Runner-up
🥉 Alternative
```

with transparent score explanations.

------------------------------------------------------------------------

# 119. Second Milestone --- Closed-Loop Learning

After publishing:

``` text
Post
 ↓
Performance
 ↓
Product
 ↓
Category
 ↓
Content style
 ↓
Hook
 ↓
Winner memory
```

Then future discovery uses those observations.

This is the point where the system becomes genuinely self-improving.

------------------------------------------------------------------------

# 120. Final Product Vision

The final Studio should feel like this:

``` text
                    STUDIO
                      │
              "What should I post?"
                      │
                      ▼
             AFFILIATE BRAIN
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
     Products       Trends       History
        │             │             │
        └─────────────┼─────────────┘
                      ▼
                WINNER ENGINE
                      │
                      ▼
                CONTENT ENGINE
                      │
                      ▼
                USER APPROVAL
                      │
                      ▼
                  SCHEDULER
                      │
                      ▼
                 INSTAGRAM
                      │
                      ▼
                 PERFORMANCE
                      │
                      ▼
                LEARNING LOOP
                      │
                      └──────────→ AFFILIATE BRAIN
```

The long-term goal is therefore not:

> **"an Instagram bot that finds Amazon products."**

It is:

> **"an affiliate intelligence platform that continuously discovers
> opportunities, evaluates them, creates content, publishes safely,
> measures the business result, and uses the result to improve its next
> decision."**

------------------------------------------------------------------------

# 121. Non-Negotiable Engineering Principles

1.  **Never fabricate marketplace facts.**
2.  **Never intentionally repeat a product that violates the configured
    uniqueness policy.**
3.  **Never replace deterministic validation with an LLM.**
4.  **Never let commission alone determine selection.**
5.  **Never expose secrets.**
6.  **Never let dry-run publish.**
7.  **Never assume a post succeeded after an uncertain automation
    action.**
8.  **Never display unavailable metrics as zero unless zero is actually
    known.**
9.  **Never make budget claims without arithmetic verification.**
10. **Never allow optional enrichment failure to destroy an otherwise
    valid run.**
11. **Never let the learning system immediately overfit to one
    successful post.**
12. **Never replace the current working pipeline wholesale when an
    incremental migration is safer.**

------------------------------------------------------------------------

# 122. Final Upgrade Strategy

The correct strategy is:

``` text
KEEP
├── LangGraph
├── Postgres
├── pgvector
├── Playwright
├── Amazon scraper foundation
├── existing taxonomy
├── deterministic scoring
├── ASIN ledger
├── RAG
├── structured LLM generation
└── current safety rules

UPGRADE
├── Discovery
├── Novelty
├── Trend intelligence
├── Winner prediction
├── Visual intelligence
├── Content experiments
├── Scheduling
├── Performance
├── Revenue analytics
├── Learning
└── Storefront intelligence

ADD
├── Publishing queue
├── Account health
├── Emergency stop
├── Performance schema
├── Experiment system
├── Discovery query memory
├── Product lifecycle
└── Intelligence dashboard
```

The most important architectural principle is:

``` text
DISCOVERY
    ↓
EVIDENCE
    ↓
SCORING
    ↓
PREDICTION
    ↓
CONTENT
    ↓
PUBLISHING
    ↓
MEASUREMENT
    ↓
LEARNING
    ↓
DISCOVERY
```

That is the target **Affiliate Intelligence Autopilot** architecture.
# 123. Affiliate Ecosystem & Marketplace Expansion Module

This module integrates the product-focused affiliate ecosystem scope into the
Affiliate Intelligence Autopilot architecture.

The objective is to expand the current Amazon-centric discovery engine into a
**multi-source affiliate product intelligence layer**, while keeping the
existing safety, validation, uniqueness and no-fabrication principles.

The ecosystem research scope covers:

1. Electronics & Technology
2. Fashion & Clothing
3. Beauty & Personal Care
4. Home & Living
5. Appliances
6. Sports & Fitness Products
7. Travel Products
8. Automotive Products
9. Books & Educational Products
10. Pet Products
11. Baby & Kids Products
12. Jewelry & Watches
13. Gaming
14. Digital Products & Software
15. Travel Services

## 123.1 Explicitly excluded categories

The system must not onboard or recommend:

- Restaurants
- Food
- Grocery
- Quick commerce
- Food delivery
- Meal delivery
- Alcohol
- Tobacco
- Nicotine products
- Recreational drugs
- Supplements with regulatory concerns
- Pharmaceuticals
- Prescription medical products
- Gambling
- Adult products
- Pornography
- Weapons
- Firearms
- Ammunition
- Explosives
- Illegal products

Travel Services are allowed only as a separate service category and must not be
mixed into physical-product collections.

---

# 124. Expanded Product Taxonomy

The current eight-category taxonomy should evolve into the following target taxonomy.

## Electronics & Technology

Subcategories:

```text
smartphones
tablets
laptops
computers
monitors
PC components
gaming
gaming accessories
headphones
earbuds
speakers
smartwatches
wearables
cameras
camera accessories
TVs
projectors
printers
storage
networking
chargers
power banks
cables
computer accessories
mobile accessories
smart-home technology
drones
consumer electronics
```

Potential merchant/source examples from the research scope:

```text
Amazon
Flipkart
Croma
Reliance Digital
Tata CLiQ
Samsung
Apple
OnePlus
Xiaomi
Realme
Motorola
Nothing
Sony
JBL
Bose
Sennheiser
Logitech
Anker
boAt
Noise
Portronics
Dell
HP
Lenovo
Asus
Acer
MSI
```

**Important:** examples are candidate sources, not proof that every merchant
has a currently available affiliate program. Each must be verified before
activation.

---

## Fashion & Clothing

```text
men's clothing
women's clothing
children's clothing
shoes
sneakers
sportswear
streetwear
accessories
bags
backpacks
wallets
sunglasses
belts
hats
watches
jewelry
fashion accessories
```

Potential source examples:

```text
Myntra
AJIO
Amazon Fashion
Flipkart Fashion
Nike
Adidas
Puma
H&M
Zara
Uniqlo
Levi's
Tommy Hilfiger
Calvin Klein
GAP
Marks & Spencer
Superdry
Jack & Jones
ONLY
Vero Moda
Roadster
HRX
WROGN
Bewakoof
The Souled Store
Rare Rabbit
Louis Philippe
Van Heusen
Allen Solly
Peter England
Max Fashion
Lifestyle
Westside
Pantaloons
```

---

## Beauty & Personal Care

```text
skincare
haircare
makeup
fragrances
grooming
shaving
men's grooming
women's beauty
beauty devices
personal-care devices
hair dryers
hair stylers
trimmers
electric shavers
beauty accessories
```

Potential sources:

```text
Nykaa
Nykaa Fashion
Purplle
Sephora
Myntra Beauty
Mamaearth
Minimalist
Plum
Dot & Key
The Derma Co
Sugar Cosmetics
Lakme
Maybelline
MAC
Nivea
Neutrogena
L'Oréal
```

---

## Home & Living

```text
furniture
home décor
lighting
kitchen appliances
home appliances
storage
bedding
mattresses
office furniture
home-office products
cleaning appliances
smart-home products
interior accessories
```

Potential sources:

```text
IKEA
Pepperfry
Urban Ladder
Home Centre
Wakefit
SleepyCat
WoodenStreet
Nilkamal
Amazon
Flipkart
Croma
Tata CLiQ
```

---

## Appliances

```text
refrigerators
washing machines
air conditioners
air coolers
microwaves
ovens
air fryers
vacuum cleaners
water purifiers
coffee machines
kitchen appliances
personal appliances
smart appliances
```

Research both marketplace and direct-manufacturer affiliate opportunities.

---

## Sports & Fitness Products

```text
running shoes
sports shoes
gym equipment
home fitness equipment
fitness accessories
sportswear
cycling products
outdoor equipment
yoga equipment
training accessories
sports accessories
```

Potential sources:

```text
Nike
Adidas
Puma
Decathlon
Under Armour
ASICS
Reebok
Cult
Amazon
Flipkart
Myntra
```

---

## Travel Products

Physical products only:

```text
luggage
suitcases
backpacks
travel bags
travel accessories
travel organizers
neck pillows
travel electronics
portable chargers
travel clothing
outdoor equipment
camping equipment
hiking equipment
travel gadgets
```

Potential sources include:

```text
American Tourister
Samsonite
Safari
VIP
Wildcraft
Decathlon
Amazon
Flipkart
Myntra
AJIO
```

---

## Automotive Products

```text
car accessories
bike accessories
car electronics
dashcams
car cleaning products
car organizers
bike gear
helmets
riding accessories
tyre-related products
tools
automotive gadgets
```

Vehicle sales should not be treated as a normal product category unless a
legitimate, supported affiliate opportunity exists.

---

## Books & Educational Products

```text
books
e-books
study materials
educational products
learning devices
educational software
```

Only include opportunities realistically promotable through Instagram.

---

## Pet Products

```text
pet accessories
pet toys
pet beds
pet grooming products
pet electronics
pet supplies
```

Exclude food and medicines.

---

## Baby & Kids Products

```text
baby clothing
toys
strollers
car seats
baby accessories
kids electronics
kids furniture
educational toys
school accessories
```

Potential sources:

```text
FirstCry
Amazon
Flipkart
Myntra
AJIO
```

Exclude food and medicines.

---

## Jewelry & Watches

```text
watches
smartwatches
jewelry
fashion jewelry
accessories
sunglasses
```

Potential sources:

```text
Titan
Fastrack
Casio
Fossil
Michael Kors
Daniel Wellington
Tanishq
CaratLane
Bluestone
Mia
Myntra
AJIO
Amazon
Flipkart
```

---

## Gaming

```text
gaming laptops
gaming PCs
consoles
controllers
headsets
keyboards
mice
gaming chairs
gaming desks
streaming equipment
gaming accessories
```

Focus primarily on physical products.

---

## Digital Products & Software

Keep separate from physical product affiliate programs:

```text
SaaS
software
design tools
productivity software
developer tools
hosting
domains
VPNs
creative software
AI tools
online services
```

Only onboard legitimate affiliate programs that permit the intended
promotional method.

---

## Travel Services

This is the only non-physical-product category in the ecosystem scope:

```text
hotels
flights
travel booking
car rentals
travel platforms
```

Keep it as a separate business/category type.

---

# 125. Merchant Discovery Architecture

Do not hard-code merchant availability.

Create a merchant registry.

```python
Merchant = {
    "merchant_id": str,
    "name": str,
    "country": str | None,
    "marketplace": str | None,
    "categories": list[str],
    "affiliate_network": str | None,
    "affiliate_program_url": str | None,
    "program_status": str,
    "india_available": bool | None,
    "api_available": bool | None,
    "feed_available": bool | None,
    "deep_link_available": bool | None,
    "link_api_available": bool | None,
    "product_data_level": str,
    "instagram_allowed": bool | None,
    "automation_allowed": bool | None,
    "tracking_capabilities": list[str],
    "commission_info": object | None,
    "payment_info": object | None,
    "restrictions": list[str],
    "verification_status": str,
    "verified_at": datetime | None,
    "source_url": str | None,
}
```

Possible program states:

```text
VERIFIED
PARTIALLY_VERIFIED
UNVERIFIED
INACTIVE
REJECTED
```

Never treat a merchant example from a research list as an active affiliate
source until verified.

---

# 126. Affiliate Network Registry

Research and model networks separately from merchants.

Candidate networks from the ecosystem scope include:

```text
Cuelinks
EarnKaro
vCommission
Admitad
Optimise
Impact
Awin
CJ Affiliate
Rakuten Advertising
Partnerize
Sovrn Commerce
LTK
Skimlinks
PartnerStack
FlexOffers
```

Again, these are candidate research targets, not guaranteed integrations.

For each network store:

```text
India availability
merchant coverage
product categories
API
product feeds
deep linking
link generation
tracking
reporting
SubIDs
commission information
social-media eligibility
Instagram eligibility
automation restrictions
approval requirements
payment methods
minimum payout
```

---

# 127. Product Source Capability Matrix

Every potential source should be evaluated for:

```text
official API
product API
search API
product feed
CSV
XML
JSON
deal feed
coupon feed
price feed
inventory feed
affiliate-link API
deep-link generator
```

Product data fields:

```text
product name
brand
category
product ID
SKU
images
product URL
affiliate URL
current price
MRP
discount
coupon
rating
review count
stock
seller
variant
size
color
commission
```

Each capability must be represented as:

```text
AVAILABLE
PARTIALLY_AVAILABLE
NOT_AVAILABLE
UNKNOWN
```

Never infer capability from the merchant's website alone.

---

# 128. Instagram Compatibility Matrix

For each affiliate source, track:

```text
Instagram posts
Instagram Reels
Instagram Stories
Instagram bio links
social-media promotion
influencer promotion
paid advertising
```

Also track restrictions:

```text
affiliate links
link shorteners
coupons
paid ads
brand bidding
automated content
bots
scraping
API usage
geographic restrictions
disclosure requirements
```

A source may be technically accessible but still unsuitable for the intended
Instagram workflow. Such a source should not automatically be activated.

---

# 129. Source Selection Architecture

Do not make "more merchants" automatically mean "more sources per run."

Introduce a source selection layer:

```text
User strategy
      ↓
Category
      ↓
Eligible merchants
      ↓
Verified capabilities
      ↓
Current source health
      ↓
Catalog relevance
      ↓
Product yield history
      ↓
Affiliate-link availability
      ↓
Discovery plan
```

The discovery planner then decides which sources to query.

---

# 130. Retailer Adapter Contract

Every marketplace/retailer integration must implement the same interface.

```python
class RetailerAdapter:

    async def health_check(self):
        ...

    async def search(self, query, page=1, filters=None):
        ...

    async def normalize_product(self, raw_product):
        ...

    async def get_product(self, product_id):
        ...

    async def refresh_observation(self, product):
        ...

    async def build_affiliate_link(self, product, account):
        ...

    async def get_capabilities(self):
        ...
```

The core engine must not contain retailer-specific assumptions.

---

# 131. Affiliate Network Adapter Contract

Networks should have a separate abstraction:

```python
class AffiliateNetworkAdapter:

    async def health_check(self):
        ...

    async def find_merchants(self, category=None):
        ...

    async def create_deep_link(self, merchant, product_url, account):
        ...

    async def get_tracking_metadata(self, link):
        ...

    async def get_reporting_data(self, account, period):
        ...
```

If an affiliate network does not expose an API, the system must not pretend it does.

---

# 132. Merchant Onboarding Workflow

New merchant:

```text
DISCOVERED
   ↓
CAPABILITY RESEARCH
   ↓
PROGRAM VERIFICATION
   ↓
LINK METHOD VERIFICATION
   ↓
INSTAGRAM POLICY REVIEW
   ↓
TEST LINK
   ↓
TEST PRODUCT DATA
   ↓
HEALTH CHECK
   ↓
ENABLED
```

Failure:

```text
NEEDS_REVIEW
```

No unverified source should silently enter production.

---

# 133. Product Identity Across Retailers

ASIN is Amazon-specific.

Therefore introduce:

```text
canonical_product_id
```

with retailer-specific identifiers:

```text
Amazon ASIN
Flipkart product ID
Myntra SKU
merchant SKU
brand model
UPC/EAN where legitimately available
```

Do not assume two products are identical simply because their titles are similar.

Use:

```text
identifier
brand
model
normalized title
image similarity
attributes
```

for cross-retailer matching.

---

# 134. Cross-Retailer Product Matching

When a likely match exists:

```text
canonical product
 ├── Amazon offer
 ├── Flipkart offer
 └── other supported offer
```

Store offers separately.

This enables:

```text
same product
different retailer
different price
different affiliate program
different commission
```

---

# 135. Offer Intelligence

A product and an offer should be separate entities.

```python
Offer = {
    "offer_id": str,
    "product_id": str,
    "merchant_id": str,
    "retailer": str,
    "product_url": str,
    "affiliate_url": str | None,
    "price": float | None,
    "original_price": float | None,
    "discount_pct": float | None,
    "stock": str | None,
    "seller": str | None,
    "commission": object | None,
    "observed_at": datetime,
}
```

This is essential for future multi-retailer comparison.

---

# 136. Expanded Ranking Model

The existing product score remains the product-quality layer.

Add offer-level intelligence:

```text
product quality
+
offer quality
+
affiliate availability
+
tracking confidence
```

A high-quality product with no valid affiliate path should not be treated as
a publishable affiliate opportunity.

---

# 137. Affiliate Opportunity Score

Introduce a separate internal score:

```text
affiliate_opportunity_score
```

Possible components:

```text
product_intelligence
affiliate_link_confidence
source_reliability
tracking_quality
catalog freshness
commission potential
```

This is distinct from:

```text
product_quality
```

and:

```text
winner_score
```

This prevents affiliate mechanics from corrupting product quality scoring.

---

# 138. Source Reliability

Track operational reliability:

```text
search success rate
product extraction success rate
affiliate link success rate
API uptime
data freshness
rate of malformed products
```

A source that repeatedly fails should automatically receive lower discovery
priority.

---

# 139. Source Health Dashboard

Add:

```text
AFFILIATE SOURCES

Amazon       🟢
Flipkart     🟢
Myntra       🟡
Croma        🟢
Source X     🔴
```

Detailed view:

```text
Product search      98%
Data completeness   93%
Affiliate links     99%
Last success        12m ago
Status              HEALTHY
```

Only show actual measured values.

---

# 140. Expanded Final Opportunity Database

The ecosystem research database should contain:

| Merchant | Category | Affiliate Network | India | API | Feed | Deep Link | Link API | Product Data | Price | Stock | Ratings | Reviews | Commission | Tracking | Instagram | Automation | Restrictions | Difficulty | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

Recommended additional fields:

```text
merchant_id
verification_status
verified_at
program_url
data_freshness
source_reliability
link_test_status
capabilities_json
policy_notes
```

---

# 141. Affiliate Opportunity Ranking

For ecosystem onboarding, rank sources separately from product ranking.

Suggested factors:

1. Product catalog quality
2. Product catalog size
3. India availability
4. API/feed quality
5. Affiliate-link automation
6. Commission potential
7. Deal frequency
8. Brand popularity
9. Instagram suitability
10. Tracking quality
11. Reliability
12. Scalability

Score each potential source from:

```text
1–100
```

But this score is an **integration/opportunity score**, not a product-selection rule.

---

# 142. Source Integration Score vs Winner Score

Keep these concepts separate:

```text
SOURCE INTEGRATION SCORE
        ↓
Should we integrate this merchant/source?

PRODUCT INTELLIGENCE SCORE
        ↓
Is this product good?

AFFILIATE OPPORTUNITY SCORE
        ↓
Is this product/offer commercially usable?

WINNER SCORE
        ↓
Is this likely to be a strong next post?
```

This separation is critical.

---

# 143. Expanded Discovery Flow

The final discovery system becomes:

```text
USER GOAL
   ↓
CATEGORY / SUBCATEGORY
   ↓
SOURCE REGISTRY
   ↓
ELIGIBLE MERCHANTS
   ↓
QUERY PLANNER
   ↓
MULTI-SOURCE SEARCH
   ↓
NORMALIZATION
   ↓
PRODUCT IDENTITY MATCHING
   ↓
QUALITY GATE
   ↓
EXACT DEDUP
   ↓
SEMANTIC NOVELTY
   ↓
TREND ENRICHMENT
   ↓
OFFER INTELLIGENCE
   ↓
PRODUCT INTELLIGENCE
   ↓
AFFILIATE OPPORTUNITY
   ↓
WINNER ENGINE
   ↓
CONTENT
```

---

# 144. Expanded Storefront Architecture

The storefront can eventually expose products from multiple verified sources.

Each product card should have:

```text
product
price
retailer
affiliate availability
rating
reviews
winner score
```

If multiple offers exist:

```text
BEST AVAILABLE OFFER

Amazon       ₹1,299
Flipkart     ₹1,249
Retailer X   ₹1,199
```

Only use current/verified observations.

---

# 145. Multi-Source Search UI

Update Discover:

```text
Sources

☑ Amazon
☑ Flipkart
☑ Myntra
☐ Croma
☐ Reliance Digital
```

or:

```text
Source strategy:
○ Best available
○ Amazon only
○ Selected sources
```

Only verified and enabled sources appear.

---

# 146. Source-Aware Winner Example

```text
🔥 WINNER

Product:
Wireless Earbuds

Product Intelligence: 94
Novelty: 91
Trend: 88
Instagram: 96

Offers:

Amazon       ₹1,499   ✓ affiliate
Flipkart     ₹1,449   ✓ affiliate
Retailer X   ₹1,399   ✗ affiliate unavailable

Recommended affiliate offer:
Amazon / Flipkart based on configured strategy
```

The system must never claim that the cheapest offer is the best affiliate offer
unless the business rules actually say so.

---

# 147. International Affiliate Sources

International programs may be researched when:

```text
Indian customers are supported
or
the merchant legitimately serves the target audience
or
the business explicitly enables international traffic
```

Every international source must carry:

```text
supported countries
currency
shipping scope
affiliate eligibility
Instagram eligibility
tracking
```

Do not mix international offers into India-focused results without a
marketplace/availability check.

---

# 148. India-First Architecture

Default:

```text
market = India
currency = INR
locale = en-IN
```

Source selection should prioritize:

```text
India availability
INR pricing
Indian shipping
Indian affiliate tracking
Indian social-media eligibility
```

International sources remain optional.

---

# 149. Travel Services Isolation

Travel services should have their own product type:

```text
PHYSICAL_PRODUCT
DIGITAL_PRODUCT
TRAVEL_SERVICE
```

Do not allow hotel/flight records to enter:

```text
physical-product collections
```

Do not apply product-specific price-band logic to services unless separately
designed.

---

# 150. Research Verification Policy

For every merchant, network, API or capability:

```text
CLAIM
 ↓
OFFICIAL SOURCE
 ↓
VERIFICATION
 ↓
DATABASE RECORD
```

If official evidence is unavailable:

```text
UNKNOWN
```

Do not convert assumptions into implementation capabilities.

---

# 151. Ecosystem Research Does Not Automatically Become Production Configuration

Research output:

```text
merchant candidate
```

does not mean:

```text
enabled merchant
```

Production state requires:

```text
verified
tested
approved
enabled
```

This protects the system from broken links, unsupported programs and policy
violations.

---

# 152. Complete Optimized System

With this marketplace module integrated, the final architecture is:

```text
                              STUDIO UI
                                  │
                                  ▼
                           STRATEGY BUILDER
                                  │
                                  ▼
                         AFFILIATE BRAIN
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
 SOURCE INTELLIGENCE       PRODUCT DISCOVERY         TREND INTELLIGENCE
        │                         │                         │
 merchant registry          query planner              trend store
 network registry           subcategories              momentum
 capabilities               pagination                 seasonality
 policy verification        multi-source               signals
        │                         │                         │
        └─────────────────────────┼─────────────────────────┘
                                  ▼
                          PRODUCT NORMALIZATION
                                  │
                                  ▼
                           QUALITY GATE
                                  │
                                  ▼
                           EXACT DEDUP
                                  │
                                  ▼
                        SEMANTIC NOVELTY
                                  │
                                  ▼
                         OFFER INTELLIGENCE
                                  │
                                  ▼
                     PRODUCT INTELLIGENCE SCORE
                                  │
                                  ▼
                         AFFILIATE OPPORTUNITY
                                  │
                                  ▼
                           WINNER ENGINE
                                  │
                                  ▼
                          CONTENT ENGINE
                                  │
                                  ▼
                          VALIDATION LAYER
                                  │
                                  ▼
                        AFFILIATE LINK SERVICE
                                  │
                                  ▼
                           POST DRAFT
                                  │
                         ┌────────┴────────┐
                         ▼                 ▼
                      APPROVAL         AUTOPILOT
                         │                 │
                         └────────┬────────┘
                                  ▼
                            PUBLISH QUEUE
                                  │
                                  ▼
                         INSTAGRAM SERVICE
                                  │
                                  ▼
                             INSTAGRAM
                                  │
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
               COMMENTS        CLICKS         PERFORMANCE
                   │              │              │
                   └──────────────┼──────────────┘
                                  ▼
                           LEARNING ENGINE
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
                 RAG MEMORY   QUERY MEMORY   WINNER MEMORY
                    │             │             │
                    └─────────────┼─────────────┘
                                  ▼
                            NEXT DISCOVERY
```

---

# 153. Final Optimized Objective

The complete project should optimize for:

```text
HIGH-QUALITY PRODUCTS
+
HIGH NOVELTY
+
REAL DEMAND SIGNALS
+
STRONG INSTAGRAM CONTENT POTENTIAL
+
VALID AFFILIATE PATH
+
TRACKABLE OFFER
+
SAFE PUBLISHING
+
MEASURABLE PERFORMANCE
+
CONTINUOUS LEARNING
```

The system should **not** optimize merely for:

```text
more products
more posts
higher commission
more scraping
more LLM calls
```

---

# 154. Final Product Vision — Studio Affiliate Intelligence Autopilot

```text
             "WHAT SHOULD I POST NEXT?"
                         │
                         ▼
                 AFFILIATE BRAIN
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
   MARKETPLACE         PRODUCT           HISTORY
   ECOSYSTEM          DISCOVERY          & RAG
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                  OPPORTUNITY ENGINE
                         │
                         ▼
                   WINNER ENGINE
                         │
                         ▼
                  CONTENT ENGINE
                         │
                         ▼
               APPROVAL / AUTOPILOT
                         │
                         ▼
                     INSTAGRAM
                         │
                         ▼
                    PERFORMANCE
                         │
                         ▼
                     LEARNING
                         │
                         └──────────────→ AFFILIATE BRAIN
```

The final platform is therefore:

> **A product-focused affiliate intelligence platform that discovers legitimate
> affiliate sources, finds high-quality and novel products across supported
> marketplaces, evaluates offers, predicts strong content opportunities,
> generates and validates Instagram content, publishes safely, tracks business
> performance, and learns from every completed cycle.**

---

# 155. Master Implementation Priority

After merging the ecosystem and intelligence plans, the recommended priority
is:

```text
P0
├── Preserve current engine and G1–G18
├── Multi-query discovery
├── Subcategory rotation
├── Pagination
├── Product normalization
├── Discovery query memory
├── Semantic novelty
├── Product Intelligence Score
└── Winner Engine

P1
├── Trend persistence
├── Visual intelligence
├── Content styles
├── Content validation
├── Why-this-product UI
├── Publishing queue
├── Account health
└── Emergency stop

P2
├── Performance database
├── Revenue analytics
├── A/B experiments
├── Winner memory
├── Intelligent calendar
└── Storefront ranking

P3
├── Affiliate merchant registry
├── Network adapters
├── Multi-retailer abstraction
├── Offer comparison
└── Source health

P4
├── Engagement intelligence
├── Competitor intelligence
└── advanced personalization

P5
├── Performance prediction model
├── learned ranking
└── automated weight calibration
```

---

# 156. The Three Most Important Engineering Loops

## Loop 1 — Product Discovery

```text
QUERY
 ↓
SOURCE
 ↓
PRODUCT
 ↓
QUALITY
 ↓
NOVELTY
 ↓
WINNER
```

## Loop 2 — Content

```text
WINNER
 ↓
ANGLE
 ↓
STYLE
 ↓
HOOK
 ↓
CAPTION
 ↓
VALIDATION
 ↓
POST
```

## Loop 3 — Learning

```text
POST
 ↓
REACH
 ↓
CLICK
 ↓
ORDER
 ↓
COMMISSION
 ↓
PATTERN
 ↓
NEXT WINNER
```

The combination of all three is the core of the optimized system.
