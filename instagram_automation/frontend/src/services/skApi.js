import axios from 'axios';

// Client for the Business-SK affiliate API (separate service on :8100),
// proxied via Vite under /sk-api. No admin token — the affiliate has no auth.
const sk = axios.create({ baseURL: '/sk-api/api', timeout: 0 });
const data = (r) => r.data;

// Every user-facing affiliate endpoint is wired below. Endpoints intentionally NOT exposed
// (legacy/internal, superseded by the current Amazon→Instagram flow): /api/run (old
// Pinterest LangGraph posting — replaced by IG /api/sk/carousel), /api/pipeline (old node
// viz), /api/history (dedup ledger log — post history comes from /api/posts).
export default {
  health:     () => sk.get('/health').then(data),          // liveness + whether a run is active
  config:     () => sk.get('/config').then(data),          // model, tag, readiness
  categories: () => sk.get('/categories').then(data),      // base categories + commission rates
  stats:      () => sk.get('/stats').then(data),           // RAG dedup flywheel (products remembered)

  // Content service. opts may include: q, marketplace, min_rating, min_reviews, price_min,
  // price_max, content (caption style: auto|DEAL_DROP|LISTICLE|PROBLEM_SOLUTION|…).
  generate: (categories, productsPerRun, opts = {}) =>
    sk.get('/generate', {
      params: {
        ...(opts.q ? { q: opts.q } : { categories: categories.join(',') }),
        products_per_run: productsPerRun,
        ...(opts.marketplace ? { marketplace: opts.marketplace } : {}),
        ...(opts.min_rating != null ? { min_rating: opts.min_rating } : {}),
        ...(opts.min_reviews != null ? { min_reviews: opts.min_reviews } : {}),
        ...(opts.price_min != null ? { price_min: opts.price_min } : {}),
        ...(opts.price_max != null ? { price_max: opts.price_max } : {}),
        ...(opts.content && opts.content !== 'auto' ? { content: opts.content } : {}),
        ...(opts.goal && opts.goal !== 'balanced' ? { goal: opts.goal } : {}),
        ...(opts.combo_budget ? { combo_budget: opts.combo_budget, combo_size: opts.combo_size || 3 } : {}),
      },
    }).then(data),

  // Posting history (the IG backend does the actual publishing; the affiliate records it).
  recordPost:  (body) => sk.post('/posts', body).then(data),
  posts:       (limit = 50) => sk.get('/posts', { params: { limit } }).then(data),

  // Link hub — all posted products with your affiliate tag (one page).
  hub:         (category) => sk.get('/hub', { params: category ? { category } : {} }).then(data),

  // Discovery — taxonomy (families/subcategories/angles) + collections (price bands + bundles).
  taxonomy:    () => sk.get('/taxonomy').then(data),
  collections: (category) => sk.get('/collections', { params: category ? { category } : {} }).then(data),

  // ── Autopilot intelligence (Phases 1-10) ──────────────────────────────────
  trends:        (category) => sk.get('/trends', { params: category ? { category } : {} }).then(data),
  discoveryQueries: (category) => sk.get('/discovery/queries', { params: category ? { category } : {} }).then(data),
  winners:       (limit = 12) => sk.get('/intelligence/winners', { params: { limit } }).then(data),
  insights:      () => sk.get('/intelligence/insights').then(data),
  perfOverview:  () => sk.get('/performance/overview').then(data),
  perfPosts:     (limit = 50) => sk.get('/performance/posts', { params: { limit } }).then(data),
  perfIngest:    (body) => sk.post('/performance/ingest', body).then(data),
  retailers:     () => sk.get('/retailers').then(data),
  // agents control panel (editable constraints, runtime overlay)
  agents:        () => sk.get('/agents').then(data),
  setAgentSetting: (key, value) => sk.post('/agents/settings', { key, value: String(value) }).then(data),
  clearAgentSetting: (key) => sk.delete(`/agents/settings/${key}`).then(data),
  // publishing queue
  pubQueue:      (status) => sk.get('/publishing/queue', { params: status ? { status } : {} }).then(data),
  pubEnqueue:    (body) => sk.post('/publishing/queue', body).then(data),
  pubCancel:     (job_id) => sk.post('/publishing/cancel', { job_id }).then(data),
  pubEmergencyStop: (on) => sk.post('/publishing/emergency-stop', { on }).then(data),
  pubAccount:    () => sk.get('/publishing/account').then(data),
};
