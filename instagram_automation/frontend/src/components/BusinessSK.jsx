import { useCallback, useEffect, useRef, useState } from 'react';
import skApi from '../services/skApi';
import api from '../services/api';
import { Icon, Spinner, cx } from './ui';

const DEFAULT_CATS = [
  { name: 'fashion', rate: 9 }, { name: 'home', rate: 8 }, { name: 'kitchen', rate: 7 },
  { name: 'beauty', rate: 6 }, { name: 'fitness', rate: 5 }, { name: 'toys', rate: 5 },
  { name: 'books', rate: 4 }, { name: 'electronics', rate: 4 },
];
// Each Business-SK sidebar item maps to a page here. sk-engagement routes to the
// shared JK Engagement panel in App.jsx, not here.
const VIEW_TAB = {
  'sk-overview': 'overview', 'sk-affiliate': 'generate', 'sk-winners': 'winners',
  'sk-trends': 'trends', 'sk-intelligence': 'intel', 'sk-calendar': 'calendar',
  'sk-post': 'post', 'sk-storefront': 'hub', 'sk-revenue': 'revenue',
  'sk-agents': 'agents', 'sk-accounts': 'accounts', 'sk-history': 'history',
};
const TAB_TITLE = {
  overview: 'Overview', generate: 'Affiliate', winners: 'Winners', trends: 'Trends',
  intel: 'Intelligence', calendar: 'Content Calendar', post: 'Content Studio',
  hub: 'Storefront', revenue: 'Revenue', agents: 'Agents', accounts: 'Accounts', history: 'History',
};
const TAB_KICKER = {
  overview: 'Your affiliate operation at a glance — status, winners, and what to do next.',
  generate: 'Pick categories, choose how many products each, and find the best-selling picks.',
  winners: 'The strongest, freshest, best-evidenced picks across everything you have posted.',
  trends: 'Trending keywords with momentum and direction, learned from every run.',
  intel: 'Performance, learned recommendations, discovery yields and retailer health.',
  calendar: 'Your publishing queue and the suggested weekly plan.',
  post: 'Review the batch from Discover, choose an account, and publish.',
  hub: 'Your public Amazon page — the link for your Instagram bio.',
  revenue: 'Measured results and the funnel — log a post to power the learning loop.',
  agents: 'Every capability is an agent — tune its constraints live, no restart.',
  accounts: 'Your affiliate program accounts, stored encrypted with your .ragskey.',
  history: 'Every carousel you have published.',
};
const LS_FAV = 'sk_favorites';
const LS_QUEUE = 'sk_queue';
const CAPTION_STYLES = ['auto', 'DEAL_DROP', 'STORY', 'LISTICLE', 'PROBLEM_SOLUTION', 'QUESTION',
  'TRANSFORMATION', 'GIFT_GUIDE', 'BUDGET', 'PREMIUM', 'VIRAL_FIND'];
const GOALS = [
  { k: 'balanced', label: 'Balanced' }, { k: 'viral', label: 'Viral potential' },
  { k: 'intent', label: 'High purchase intent' }, { k: 'value', label: 'Best value' },
  { k: 'trending', label: 'Trending' }, { k: 'fresh', label: 'Fresh / novel' },
  { k: 'commission', label: 'High commission' },
];

export default function BusinessSK({ notify, accounts = [], view = 'sk-affiliate', onNavigate }) {
  const tab = VIEW_TAB[view] || 'generate';
  const [reachable, setReachable] = useState(null);
  const [config, setConfig] = useState(null);
  const [cats, setCats] = useState(DEFAULT_CATS);
  const [queue, setQueueState] = useState(() => load(LS_QUEUE, []));   // [{category, products:[...]}]
  const say = useCallback((t, k = 'ok') => (notify ? notify(t, k) : null), [notify]);
  const setQueue = useCallback((q) => setQueueState((prev) => {
    const next = typeof q === 'function' ? q(prev) : q;
    save(LS_QUEUE, next); return next;
  }), []);

  const [health, setHealth] = useState(null);
  const [stats, setStats] = useState(null);
  useEffect(() => {
    Promise.all([skApi.config(), skApi.categories()])
      .then(([c, ct]) => { setConfig(c); setCats(ct.categories || DEFAULT_CATS); setReachable(true); })
      .catch(() => setReachable(false));
    skApi.health().then(setHealth).catch(() => setHealth(null));
    skApi.stats().then(setStats).catch(() => setStats(null));   // RAG dedup flywheel memory
  }, []);

  if (reachable === false) return <NotReachable onRetry={() => window.location.reload()} />;

  const shared = { cats, say, config, accounts };
  const queued = queue.reduce((n, g) => n + (g.products?.length || 0), 0);
  const show = (t) => ({ display: tab === t ? 'block' : 'none' });
  return (
    <div className="fade-up">
      <div className="flex items-end justify-between flex-wrap gap-4 mb-6">
        <div>
          <p className="eyebrow mb-2">Business-SK</p>
          <h1 className="font-display text-4xl md:text-5xl" style={{ fontWeight: 600, lineHeight: 1 }}>{TAB_TITLE[tab]}</h1>
          <p className="text-sm mt-2" style={{ color: 'var(--muted)', maxWidth: 560 }}>{TAB_KICKER[tab]}</p>
        </div>
        <div className="flex gap-2 flex-wrap text-xs font-mono">
          <Chip ok={health?.ok ?? reachable}>{(health?.ok ?? reachable) ? (health?.running ? 'API · running' : 'API live') : '…'}</Chip>
          {config && <Chip ok={config.ready}>{config.model}</Chip>}
          {config && <Chip>tag {config.associate_tag}</Chip>}
          <Chip ok={accounts.length > 0}>{accounts.length} IG account{accounts.length !== 1 ? 's' : ''}</Chip>
          {stats && <Chip ok={stats.db_ok}>{(stats.total_seen ?? 0)} remembered</Chip>}
          {queued > 0 && <Chip ok>{queued} queued</Chip>}
        </div>
      </div>

      <div style={show('overview')}><OverviewPanel active={tab === 'overview'} health={health} stats={stats} accounts={accounts} go={onNavigate} /></div>
      <div style={show('generate')}><GenerateTab {...shared} setQueue={setQueue} goPost={() => onNavigate?.('sk-post')} /></div>
      <div style={show('winners')}><WinnersPanel active={tab === 'winners'} say={say} /></div>
      <div style={show('trends')}><TrendsPanel active={tab === 'trends'} cats={cats} /></div>
      <div style={show('intel')}><IntelligencePanel active={tab === 'intel'} /></div>
      <div style={show('revenue')}><RevenuePanel active={tab === 'revenue'} say={say} /></div>
      <div style={show('calendar')}><CalendarPanel active={tab === 'calendar'} say={say} /></div>
      <div style={show('agents')}><AgentsPanel active={tab === 'agents'} say={say} /></div>
      <div style={show('accounts')}><AccountsPanel active={tab === 'accounts'} say={say} /></div>
      <div style={show('post')}><PostTab {...shared} queue={queue} setQueue={setQueue} goAffiliate={() => onNavigate?.('sk-affiliate')} /></div>
      <div style={show('hub')}><HubTab {...shared} /></div>
      <div style={show('history')}><HistoryTab active={tab === 'history'} /></div>
      <CardStyles />
    </div>
  );
}

// ══════════════════════════════════ AFFILIATE (find products) ═════════════════
function GenerateTab({ cats, say, setQueue, goPost }) {
  const [counts, setCounts] = useState({ home: 3 });     // {category: n} — products PER POST (IG carousel, 1-10)
  const [subs, setSubs] = useState({});                  // {category: [subcategory,...]} — multi-select; each = 1 post
  const [tax, setTax] = useState(null);
  useEffect(() => { skApi.taxonomy().then(setTax).catch(() => setTax(null)); }, []);
  const [showOpts, setShowOpts] = useState(false);
  const [minRating, setMinRating] = useState(3.8);
  const [minReviews, setMinReviews] = useState(50);
  const [priceMax, setPriceMax] = useState(5000);
  const [style, setStyle] = useState('auto');            // Phase 4 caption style (A/B)
  const [goal, setGoal] = useState('balanced');          // ranking goal (Find Winners)
  const [comboOn, setComboOn] = useState(false);         // combo/bundle mode
  const [comboBudget, setComboBudget] = useState(3000);
  const [combo, setCombo] = useState(null);              // returned combo bundle
  const [running, setRunning] = useState(false);
  const [prog, setProg] = useState([]);                  // per-post progress rows
  const [groups, setGroups] = useState(null);            // [{id,label,category,products}]
  const [favs, setFavs] = useState(() => load(LS_FAV, []));

  const selected = Object.keys(counts);
  const toggle = (n) => setCounts((c) => { const next = { ...c }; if (n in next) { delete next[n]; setSubs((s) => { const q = { ...s }; delete q[n]; return q; }); } else next[n] = 3; return next; });
  const setCount = (n, d) => setCounts((c) => ({ ...c, [n]: Math.max(1, Math.min(10, (c[n] || 3) + d)) }));
  const selectAll = () => setCounts(Object.fromEntries(cats.map((c) => [c.name, counts[c.name] || 3])));
  const clearAll = () => { setCounts({}); setSubs({}); };
  const toggleSub = (cat, sub) => setSubs((s) => {
    const cur = s[cat] || [];
    return { ...s, [cat]: cur.includes(sub) ? cur.filter((x) => x !== sub) : [...cur, sub] };
  });

  // Each selected subcategory = one post; a category with no subs selected = one post for the category.
  const jobs = selected.flatMap((c) => {
    const chosen = subs[c] || [];
    return chosen.length ? chosen.map((s) => ({ cat: c, label: s, q: s })) : [{ cat: c, label: c, q: null }];
  });
  const postCount = jobs.length;

  const isFav = (asin) => favs.some((f) => f.asin === asin);
  const toggleFav = (it) => setFavs((cur) => {
    const next = isFav(it.asin) ? cur.filter((f) => f.asin !== it.asin) : [it, ...cur].slice(0, 100);
    save(LS_FAV, next); return next;
  });
  const copy = (t, l) => navigator.clipboard?.writeText(t).then(() => say(`${l} copied`)).catch(() => say('Copy failed', 'error'));

  const run = async () => {
    if (!selected.length) return say('Select at least one category', 'error');
    if (postCount > 10) return say('Instagram allows up to 10 posts — deselect a few subcategories', 'error');
    setRunning(true); setGroups(null); setCombo(null);
    const opts = { min_rating: minRating, min_reviews: minReviews, price_max: priceMax, content: style, goal,
                   ...(comboOn ? { combo_budget: comboBudget } : {}) };
    const out = [];
    const errs = [];
    setProg(jobs.map((j) => ({ id: j.label, phase: 'queued', n: 0 })));
    for (const j of jobs) {
      setProg((p) => p.map((r) => (r.id === j.label ? { ...r, phase: 'generating' } : r)));
      try {
        const r = j.q
          ? await skApi.generate([], counts[j.cat] || 3, { ...opts, q: j.q })
          : await skApi.generate([j.cat], counts[j.cat] || 3, opts);
        const products = (r.items || []).map((it) => ({ ...it, category: j.cat }));  // keep base category
        out.push({ id: j.label, label: j.label, category: j.cat, products, caption: r.caption || '', hashtags: r.hashtags || [], content_style: r.content_style || '', warnings: r.content_warnings || [] });
        if (r.combo) setCombo(r.combo);
        (r.errors || []).forEach((e) => errs.push(e));
        setProg((p) => p.map((r2) => (r2.id === j.label ? { ...r2, phase: 'done', n: products.length } : r2)));
      } catch (e) {
        setProg((p) => p.map((r2) => (r2.id === j.label ? { ...r2, phase: 'error', n: 0 } : r2)));
      }
    }
    setGroups(out);
    const nonEmpty = out.filter((g) => g.products.length);
    setQueue(nonEmpty);                                  // AUTO-reflect into Post to IG (no manual send)
    const total = out.reduce((n, g) => n + g.products.length, 0);
    const blocked = errs.some((e) => /blocked|Download is starting|scrape_amazon|bot-wall/i.test(e));
    say(total
      ? `${total} products → ${nonEmpty.length} post${nonEmpty.length === 1 ? '' : 's'} ready in Content Studio`
      : blocked ? 'Amazon blocked the cloud IP — add a scraping proxy (see Agents/Accounts) to fetch products'
      : 'No new products (deduped or filtered out)', total ? 'ok' : 'error');
    setRunning(false);
  };

  const allItems = (groups || []).flatMap((g) => g.products.map((p) => ({ ...p, category: g.category })));
  const total = allItems.length;
  const readyPosts = (groups || []).filter((g) => g.products.length).length;

  return (
    <>
      {/* Step 1 — categories, per-post product counts, multi-select subcategories */}
      <div className="panel p-5 mb-5">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div className="step-head"><span className="step-n">1</span> Choose categories &amp; products per post</div>
          <div className="flex gap-2">
            <button className="mini" onClick={selectAll}>Select all</button>
            <button className="mini" onClick={clearAll}>Clear</button>
          </div>
        </div>
        <CategoryGrid cats={cats} counts={counts} onToggle={toggle} onCount={setCount} />

        {/* Subcategory refine — MULTI-SELECT. Each picked subcategory becomes its own post. */}
        {tax?.by_category && selected.some((c) => tax.by_category[c]?.subcategories?.length) && (
          <div className="subcat-wrap">
            {selected.map((c) => {
              const list = tax.by_category[c]?.subcategories || [];
              if (!list.length) return null;
              const picked = subs[c] || [];
              return (
                <div key={c} className="subcat-block">
                  <div className="subcat-head">{c} <span>refine — pick any (each = 1 post)</span></div>
                  <div className="flex flex-wrap gap-1.5">
                    {list.map((s) => (
                      <span key={s} className={cx('subchip', picked.includes(s) && 'on')} onClick={() => toggleSub(c, s)}>{s}</span>
                    ))}
                  </div>
                </div>
              );
            })}
            <p className="text-xs mt-1" style={{ color: 'var(--faint)' }}>Select multiple subcategories — each makes a separate post. Every result is scored (Instagram · Buy · Value · Content) and tiered S→D.</p>
          </div>
        )}

        <div className="divider" />
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className={cx('posts-meter', postCount > 10 && 'over')}>
            <b>{postCount}</b> / 10 post{postCount === 1 ? '' : 's'} <span>· Instagram allows up to 10</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="text-xs" style={{ color: 'var(--faint)' }}>Goal</label>
            <select className="sk-select" value={goal} onChange={(e) => setGoal(e.target.value)} title="What to optimise this run for (Find Winners)">
              {GOALS.map((gopt) => <option key={gopt.k} value={gopt.k}>{gopt.label}</option>)}
            </select>
            <label className="text-xs" style={{ color: 'var(--faint)' }}>Style</label>
            <select className="sk-select" value={style} onChange={(e) => setStyle(e.target.value)} title="How the AI writes the caption (A/B)">
              {CAPTION_STYLES.map((s) => <option key={s} value={s}>{s === 'auto' ? 'Auto' : s.replace(/_/g, ' ').toLowerCase()}</option>)}
            </select>
            <button className={cx('mini', comboOn && 'on')} onClick={() => setComboOn((v) => !v)} title="Build a combo: complementary products summing under a budget">🎁 Combo {comboOn ? 'on' : 'off'}</button>
            {comboOn && <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--faint)' }}>under ₹<input className="sk-input" style={{ width: 74 }} value={comboBudget} onChange={(e) => setComboBudget(Number(e.target.value) || 0)} inputMode="numeric" /></span>}
            <button className="btn btn-sm btn-ghost" onClick={() => setShowOpts((v) => !v)}><Icon name="settings" size={13} /> Filters {showOpts ? '▾' : '▸'}</button>
          </div>
        </div>
        {showOpts && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-3 p-3" style={{ background: 'var(--panel-2)', borderRadius: 10 }}>
            <Slider label={`Min rating: ${minRating}★`} min={0} max={5} step={0.1} value={minRating} onChange={setMinRating} />
            <Slider label={`Min reviews: ${minReviews}`} min={0} max={2000} step={10} value={minReviews} onChange={setMinReviews} />
            <Slider label={`Max price: ₹${priceMax}`} min={200} max={20000} step={100} value={priceMax} onChange={setPriceMax} />
          </div>
        )}

        <div className="flex items-center gap-4 flex-wrap mt-5">
          <button className="btn btn-lg" onClick={run} disabled={running || !postCount || postCount > 10} style={{ minWidth: 180, justifyContent: 'center' }}>
            {running ? <><Spinner size={16} /> Finding…</> : <><Icon name="spark" size={17} /> Find products</>}
          </button>
          {postCount > 0 && <span className="text-xs" style={{ color: postCount > 10 ? 'var(--danger)' : 'var(--faint)' }}>{postCount} post{postCount === 1 ? '' : 's'} · up to {Math.max(...selected.map((c) => counts[c] || 3), 0)} products each</span>}
        </div>

        {/* PROCESSING panel — per-post status while finding */}
        {prog.length > 0 && (running || groups) && (
          <div className="proc-panel mt-4">
            <div className="proc-head"><Icon name="bolt" size={13} /> Processing · {prog.filter((r) => r.phase === 'done').length}/{prog.length}</div>
            <div className="flex flex-wrap gap-2">
              {prog.map((r) => (
                <span key={r.id} className={cx('prog-pill', r.phase === 'error' && 'err')}>
                  {r.phase === 'generating' ? <Spinner size={11} /> : r.phase === 'done' ? <Icon name="check" size={11} style={{ color: '#3fb950' }} /> : r.phase === 'error' ? <Icon name="x" size={11} style={{ color: 'var(--danger)' }} /> : <span className="dot" />}
                  {r.id}{r.phase === 'done' ? ` · ${r.n}` : ''}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Step 2 — results (auto-sent to Post to IG) */}
      {groups && (
        <div className="mb-24">
          <div className="flex items-center gap-3 mb-4">
            <div className="step-head"><span className="step-n">2</span> Review — {readyPosts} post{readyPosts === 1 ? '' : 's'} ready in Content Studio</div>
            <span className="flex-1" />
            {total > 0 && <>
              <button className="btn btn-sm btn-ghost" onClick={() => copy(JSON.stringify(allItems, null, 2), 'JSON')}><Icon name="doc" size={12} /> JSON</button>
              <button className="btn btn-sm btn-ghost" onClick={() => downloadCSV(allItems)}><Icon name="ext" size={12} /> CSV</button>
            </>}
          </div>
          {combo && (
            <div className="panel p-4 mb-5" style={{ borderColor: 'var(--accent)' }}>
              <div className="flex items-center gap-2 mb-2"><span className="prog-badge">🎁 {combo.title}</span><span className="text-xs" style={{ color: 'var(--muted)' }}>{combo.count} products · combined ₹{Number(combo.combined_price).toLocaleString()}</span></div>
              <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-5 gap-2">
                {(combo.products || []).map((p, i) => (
                  <div key={(p.asin || '') + i} className="acct-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 6 }}>
                    {(p.image_url || p.image) ? <img src={hiRes(p.image_url || p.image)} alt="" style={{ width: '100%', height: 90, objectFit: 'contain', background: '#fff', borderRadius: 8 }} /> : null}
                    <div style={{ fontSize: 11, fontWeight: 600, lineHeight: 1.2, maxHeight: 28, overflow: 'hidden' }}>{p.product_title || p.title}</div>
                    <div className="text-xs font-mono" style={{ color: 'var(--accent)' }}>{p.price}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {total === 0 ? <Empty text="No new products (deduped). Try other subcategories or lower the quality filters." /> : (
            (groups.filter((g) => g.products.length)).map((g) => (
              <div key={g.id} className="mb-6">
                <div className="group-head"><span className="chip-sk on" style={{ textTransform: 'capitalize' }}>{g.label}</span><span className="text-xs" style={{ color: 'var(--faint)' }}>{g.products.length} products · 1 post · 1 caption</span>{g.content_style && g.content_style !== 'UNKNOWN' && <span className="style-tag">{g.content_style.replace(/_/g, ' ').toLowerCase()}</span>}{(g.warnings || []).length > 0 && <span className="warn-tag" title={g.warnings.join('\n')}>⚠ {g.warnings.length}</span>}</div>
                {g.caption && (
                  <div className="panel p-3 mb-3" style={{ background: 'var(--panel-2)' }}>
                    <div className="flex items-center gap-2 mb-1"><span className="eyebrow">Carousel caption</span><span className="flex-1" /><button className="btn btn-sm btn-ghost" onClick={() => copy(g.caption + '\n\n' + (g.hashtags || []).map((h) => '#' + h).join(' '), 'Caption')}><Icon name="quote" size={12} /> Copy</button></div>
                    <div className="text-xs" style={{ color: 'var(--muted)', whiteSpace: 'pre-wrap' }}>{g.caption}</div>
                    {(g.hashtags || []).length > 0 && <div className="text-xs font-mono mt-1" style={{ color: '#79c0ff' }}>{g.hashtags.map((h) => '#' + h).join(' ')}</div>}
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {g.products.map((it, i) => <ProductCard key={it.asin + i} it={it} copy={copy} fav={isFav(it.asin)} onFav={() => toggleFav(it)} say={say} />)}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {favs.length > 0 && !groups && (
        <div className="panel p-4">
          <div className="eyebrow mb-2">Favorites ({favs.length})</div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {favs.slice(0, 6).map((it, i) => <ProductCard key={'f' + it.asin + i} it={it} copy={copy} fav onFav={() => toggleFav(it)} say={say} />)}
          </div>
        </div>
      )}

      {/* sticky bar — already queued; this just jumps to Post to IG */}
      {total > 0 && (
        <div className="action-bar">
          <div className="text-sm" style={{ color: 'var(--muted)' }}>
            <b style={{ color: 'var(--text)' }}>{readyPosts} post{readyPosts === 1 ? '' : 's'}</b> ({total} products) auto-sent to Post to IG
          </div>
          <button className="btn btn-lg" onClick={() => goPost?.()} style={{ justifyContent: 'center' }}>
            Go to Post to IG <Icon name="chevR" size={16} />
          </button>
        </div>
      )}
    </>
  );
}

// ══════════════════════════════════ POST TO IG ════════════════════════════════
function PostTab({ accounts, say, queue = [], setQueue, goAffiliate }) {
  const [account, setAccount] = useState(accounts[0]?.id || '');
  const [statuses, setStatuses] = useState({});   // {id: {phase, status, label, permalink, error}}
  const [busyId, setBusyId] = useState(null);      // group currently posting
  const [busyAll, setBusyAll] = useState(false);
  const [posted, setPosted] = useState(() => load('sk_posted_cards', []));  // small "posted" cards
  const addPosted = (card) => setPosted((p) => { const next = [card, ...p.filter((x) => x.id !== card.id)].slice(0, 30); save('sk_posted_cards', next); return next; });
  const refresh = () => {
    if (queue.length && !window.confirm(`Clear the ${queue.length} staged post${queue.length === 1 ? '' : 's'} here? (Run "Find products" in Affiliate to re-stage.)`)) return;
    setStatuses({});
    setQueue([]);                                   // clear the held/staged queue
    say('Cleared staged posts');
  };

  useEffect(() => { if (!account && accounts[0]) setAccount(accounts[0].id); }, [accounts, account]);
  const setSt = (id, patch) => setStatuses((s) => ({ ...s, [id]: { ...(s[id] || {}), ...patch } }));
  const acctObj = accounts.find((a) => a.id === account);
  const acctHandle = acctObj?.handle ? '@' + acctObj.handle : (acctObj?.label || 'your account');

  // Publish ONE post (carousel) — up to 10 products (IG's carousel limit). dryRun=true only records.
  const publishOne = async (g, dryRun, refresh = true) => {
    if (!dryRun && !account) { say('Pick an Instagram account', 'error'); return false; }
    const pins = (g.products || []).slice(0, 10);
    if (!pins.length) { setSt(g.id, { phase: 'done', status: 'skipped', error: 'no products' }); return false; }
    setBusyId(g.id); setSt(g.id, { phase: 'posting', error: '' });
    try {
      const body = (g.caption || '').trim() || buildCaption(g.label, pins);      // one universal caption
      const tags = (g.hashtags || []).map((h) => (h.startsWith('#') ? h : '#' + h)).join(' ');
      const caption = tags && !body.includes(tags) ? `${body}\n\n${tags}` : body;  // append hashtags to the post
      const images = pins.map((p) => hiRes(p.image_url)).filter(Boolean);        // full-resolution images
      let media_id = null, permalink = null, status = 'dry';
      if (!dryRun) {
        const res = await api.skCarousel(account, images, caption, { category: g.category, products: pins });
        media_id = res.ig_media_id; permalink = res.permalink; status = 'posted';
      }
      const rec = await skApi.recordPost({ category: g.category, products: pins, media_id, permalink, caption, status, content_style: g.content_style || '' });
      setSt(g.id, { phase: 'done', status, label: rec.post?.label, permalink });
      if (!dryRun && refresh) { try { await api.skPublishStorefront(); } catch { /* non-fatal */ } }
      if (!dryRun && status === 'posted') {
        // Move the posted carousel OUT of the queue into a compact "posted" card.
        addPosted({ id: g.id, label: g.label, category: g.category, count: pins.length,
                    permalink, postLabel: rec.post?.label, at: Date.now() });
        setQueue((prev) => (prev || []).filter((x) => x.id !== g.id));
      }
      return true;
    } catch (e) {
      const msg = e?.response?.data?.error?.message || e?.response?.data?.detail || e?.message || 'error';
      setSt(g.id, { phase: 'failed', error: String(msg) });
      return false;
    } finally { setBusyId(null); }
  };

  // One REAL post (with a confirm — it publishes public content to the live account).
  const postOneReal = async (g) => {
    if (!account) return say('Pick an Instagram account first', 'error');
    if (!window.confirm(`Post "${g.label}" (${Math.min(g.products?.length || 0, 10)} products) to Instagram ${acctHandle} — for real?`)) return;
    await publishOne(g, false);
  };

  // Publish ALL — one after the other (sequential, no clashes).
  const publishAll = async (dryRun) => {
    if (!queue.length) return say('Nothing queued — find products in Affiliate first', 'error');
    if (!dryRun && !account) return say('Pick an Instagram account', 'error');
    if (!dryRun && !window.confirm(`Post ALL ${queue.length} carousels to Instagram ${acctHandle} — for real, one after another?`)) return;
    setBusyAll(true);
    let ok = 0;
    for (const g of queue) {
      if (statuses[g.id]?.phase === 'done') continue;      // skip already-posted
      if (await publishOne(g, dryRun, false)) ok++;
    }
    if (!dryRun && ok) { try { await api.skPublishStorefront(); } catch { /* non-fatal */ } }
    setBusyAll(false);
    say(dryRun ? 'Dry run finished' : `Posted ${ok}/${queue.length} to Instagram ✓ storefront refreshed`);
  };

  const doneCount = queue.filter((g) => statuses[g.id]?.phase === 'done').length;

  return (
    <div className="post-layout">
      <div className="post-main">
      {/* publish controls */}
      <div className="panel p-5 mb-5">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div className="step-head"><span className="step-n">1</span> Publish · {queue.length} post{queue.length === 1 ? '' : 's'}{doneCount ? ` · ${doneCount} done` : ''}</div>
          <div className="flex gap-2">
            <button className="btn btn-sm btn-ghost" onClick={refresh} title="Clear the staged posts here"><Icon name="bolt" size={13} /> Clear staged</button>
            <button className="btn btn-sm btn-ghost" onClick={goAffiliate}><Icon name="spark" size={13} /> {queue.length ? 'Edit in Affiliate' : 'Find products'}</button>
          </div>
        </div>
        {queue.length === 0 ? (
          <div className="empty-cta">
            <Icon name="pin" size={22} style={{ color: 'var(--faint)' }} />
            <p className="text-sm mt-2" style={{ color: 'var(--muted)' }}>No posts yet. Go to <b>Affiliate</b>, pick categories &amp; subcategories — each becomes a post here automatically.</p>
            <button className="btn btn-sm mt-3" onClick={goAffiliate}><Icon name="spark" size={13} /> Go to Affiliate</button>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-4 mb-2">
              <div>
                <label className="text-xs" style={{ color: 'var(--muted)' }}>Instagram account</label>
                <select className="sk-input" style={{ width: 240, marginTop: 6 }} value={account} onChange={(e) => setAccount(Number(e.target.value))}>
                  {accounts.length === 0 && <option value="">— none linked —</option>}
                  {accounts.map((a) => <option key={a.id} value={a.id}>{a.label} {a.handle ? `(@${a.handle})` : ''}</option>)}
                </select>
              </div>
              <button className="btn btn-lg btn-post" onClick={() => publishAll(false)} disabled={busyAll || !!busyId || accounts.length === 0} style={{ minWidth: 210, justifyContent: 'center' }}>
                {busyAll ? <><Spinner size={16} /> Posting…</> : <><Icon name="pin" size={17} /> Post all {queue.length} to Instagram</>}
              </button>
              <button className="btn btn-ghost" onClick={() => publishAll(true)} disabled={busyAll || !!busyId} title="Record without posting (test)">
                <Icon name="settings" size={13} /> Dry run (test)
              </button>
            </div>
            <p className="text-xs" style={{ color: 'var(--faint)' }}>
              <b style={{ color: '#3fb950' }}>Post all to Instagram</b> publishes for real to {acctHandle} — each post is one carousel (≤10 products), labelled <code>post_N#category</code>, with a comment→DM automation attached, sent one after another. Or post cards individually below.
              {accounts.length === 0 && ' Add an Instagram account in the Accounts panel first.'}
            </p>
          </>
        )}
      </div>

      {/* Posted — compact confirmation cards (moved out of the queue once live) */}
      {posted.length > 0 && (
        <div className="panel p-4 mb-5" style={{ borderColor: '#3fb95055' }}>
          <div className="flex items-center justify-between mb-2">
            <div className="eyebrow" style={{ color: '#3fb950' }}>✓ Posted ({posted.length})</div>
            <button className="mini" onClick={() => { setPosted([]); save('sk_posted_cards', []); }}>Clear</button>
          </div>
          <div className="flex flex-col gap-2">
            {posted.map((p) => (
              <div key={p.id + p.at} className="posted-row">
                <span className="mini on" style={{ minWidth: 60, textAlign: 'center' }}>posted</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 13, textTransform: 'capitalize' }}>{p.postLabel || p.label}</div>
                  <div className="text-xs font-mono" style={{ color: 'var(--muted)' }}>{p.count} products · {new Date(p.at).toLocaleTimeString()}</div>
                </div>
                {p.permalink && <a className="btn btn-sm btn-ghost" href={p.permalink} target="_blank" rel="noreferrer"><Icon name="ext" size={12} /> View</a>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* per-post cards, each with its OWN real Post button + processing state */}
      {queue.length > 0 && (
        <div className="flex flex-col gap-4">
          {queue.map((g) => (
            <IgPostCard key={g.id} g={g} st={statuses[g.id] || {}} posting={busyId === g.id}
              busyAll={busyAll} accountLabel={acctHandle}
              onPost={() => postOneReal(g)} onDry={() => publishOne(g, true)} />
          ))}
        </div>
      )}
      {queue.length === 0 && posted.length > 0 && (
        <div className="empty-cta"><p className="text-sm" style={{ color: 'var(--muted)' }}>All queued posts published 🎉 — find more products in <b>Affiliate</b>.</p><button className="btn btn-sm mt-3" onClick={goAffiliate}><Icon name="spark" size={13} /> Go to Affiliate</button></div>
      )}
      </div>

      {/* right-side account panel — profile, stories, tools */}
      <AccountPanel accountId={account} queue={queue} say={say} />
    </div>
  );
}

// ── Instagram-style post preview card — swipe the carousel, read the caption, publish ──
function IgPostCard({ g, st, posting, busyAll, accountLabel, onPost, onDry }) {
  const pins = (g.products || []).slice(0, 10);
  const [idx, setIdx] = useState(0);
  const [showCap, setShowCap] = useState(false);
  const [showList, setShowList] = useState(false);
  const [design, setDesign] = useState(null);        // rendered Still Set slide URLs (once previewed)
  const [designing, setDesigning] = useState(false);
  const [designErr, setDesignErr] = useState('');
  const slides = design && design.length ? design : null;   // "design mode" once slides are rendered
  const total = slides ? slides.length : pins.length;
  const cur = pins[idx] || {};
  const caption = (g.caption || '').trim() || buildCaption(g.label, pins);   // one caption for the carousel
  const hashtags = g.hashtags || [];
  const go = (d) => setIdx((i) => (i + d + total) % total);
  const done = st.phase === 'done';

  // Render the actual Still Set designed slides for THIS post's products (layout adapts to the
  // number of products) — the same renderer the real post uses, so preview == final post.
  const renderDesign = async () => {
    setDesigning(true); setDesignErr('');
    try {
      const res = await api.skRenderPreview(pins, { category: g.category });
      setDesign(res.images || []);
      setIdx(0);
    } catch (e) {
      setDesignErr(e?.response?.data?.detail || e?.response?.data?.error?.message || e?.message || 'render failed');
    } finally { setDesigning(false); }
  };

  return (
    <div className={cx('ig-card', done && 'is-done', st.phase === 'failed' && 'is-fail')}>
      {/* header */}
      <div className="ig-top">
        <span className="ig-dot" />
        <div style={{ minWidth: 0 }}>
          <div className="ig-user">{accountLabel ? '@' + accountLabel : 'your account'}</div>
          <div className="ig-sub">{g.label} · carousel</div>
        </div>
        <span className="flex-1" />
        {done && <StatusPill s={st.status || 'posted'} />}
        {st.phase === 'failed' && <StatusPill s="failed" />}
        {posting && <span className="prog-pill"><Spinner size={11} /> posting…</span>}
      </div>

      {/* media (swipeable) — shows the DESIGNED Still Set slides once previewed, else raw products */}
      <div className="ig-media">
        {slides
          ? <img src={slides[idx]} alt="" loading="lazy" />
          : (cur.image_url ? <img src={hiRes(cur.image_url)} alt="" loading="lazy" /> : <div className="ig-ph" />)}
        {total > 1 && <>
          <button className="ig-nav l" onClick={() => go(-1)} aria-label="prev">‹</button>
          <button className="ig-nav r" onClick={() => go(1)} aria-label="next">›</button>
          <span className="ig-idx">{idx + 1}/{total}</span>
          <div className="ig-dots">{Array.from({ length: total }).map((_, i) => <span key={i} className={cx('d', i === idx && 'on')} />)}</div>
        </>}
        {slides && <span className="ig-idx" style={{ left: 8, right: 'auto', background: 'var(--accent)', color: '#111', fontWeight: 700 }}>DESIGNED</span>}
        {!slides && cur.tier && <span className={cx('tier-badge', 'tier-' + cur.tier)}>{cur.tier} · {cur.content_score}</span>}
      </div>

      {/* preview the actual template design (adapts to product count) — preview == what posts */}
      <div className="ig-designbar" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', flexWrap: 'wrap' }}>
        {!slides
          ? <button className="btn btn-sm" onClick={renderDesign} disabled={designing || !pins.length}>
              {designing ? <><Spinner size={12} /> Rendering template…</> : <><Icon name="pin" size={13} /> Preview post design</>}
            </button>
          : <button className="btn btn-sm btn-ghost" onClick={() => { setDesign(null); setIdx(0); }}>Show products</button>}
        {slides && <span className="text-xs" style={{ color: 'var(--faint)' }}>Preview = exactly what posts · {total} slides</span>}
        {designErr && <span className="text-xs" style={{ color: '#f85149' }}>{designErr}</span>}
      </div>

      {/* current product line (raw view only — designed slides already show the details) */}
      {!slides && <div className="ig-info">
        <b>{cur.price}</b>
        {cur.orig_price && <span style={{ textDecoration: 'line-through', color: 'var(--faint)', fontSize: 12 }}>{cur.orig_price}</span>}
        {cur.discount_pct != null && <span style={{ color: '#3fb950', fontSize: 12 }}>-{cur.discount_pct}%</span>}
        <span className="ig-title">{(cur.product_title || cur.title || '').slice(0, 60)}</span>
      </div>}

      {/* ONE universal caption for the whole carousel (exactly what gets posted) */}
      <div className="ig-cap">
        <div style={{ whiteSpace: 'pre-wrap' }}>{showCap ? caption : caption.slice(0, 220) + (caption.length > 220 ? '…' : '')}
          {caption.length > 220 && <button className="ig-more" onClick={() => setShowCap((v) => !v)}>{showCap ? ' less' : ' more'}</button>}
        </div>
        {hashtags.length > 0 && <div className="ig-tags">{hashtags.map((h) => '#' + h).join(' ')}</div>}
      </div>

      {/* complete details — all products in this post */}
      <div className="ig-details">
        <button className="ig-more" onClick={() => setShowList((v) => !v)}>{showList ? '▾' : '▸'} {pins.length} products in this carousel</button>
        {showList && (
          <div className="ig-list">
            {pins.map((p, i) => (
              <a key={p.asin + i} className="ig-list-row" href={p.affiliate_link} target="_blank" rel="noopener">
                {p.image_url ? <img src={p.image_url} alt="" /> : <span className="ph" />}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="ig-list-title">{(p.product_title || p.title || '').slice(0, 54)}</div>
                  <div className="ig-list-meta"><b>{p.price}</b>{p.discount_pct != null && <span style={{ color: '#3fb950' }}>-{p.discount_pct}%</span>}{p.rating != null && <span>★{p.rating}</span>}{p.tier && <span className={cx('mini-tier', 'tier-' + p.tier)}>{p.tier}</span>}</div>
                </div>
                <Icon name="ext" size={12} style={{ color: 'var(--accent)' }} />
              </a>
            ))}
          </div>
        )}
      </div>

      {/* publish — REAL post + dry test */}
      <div className="ig-foot">
        <button className="btn btn-post" onClick={onPost} disabled={posting || busyAll || done} style={{ minWidth: 190, justifyContent: 'center' }}>
          {posting ? <><Spinner size={14} /> Posting…</> : done ? <><Icon name="check" size={14} /> Posted ✓</> : <><Icon name="pin" size={15} /> Post to Instagram</>}
        </button>
        {!done && <button className="btn btn-sm btn-ghost" onClick={onDry} disabled={posting || busyAll} title="Record without posting">dry test</button>}
        {st.permalink && <a className="text-xs" href={st.permalink} target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>view on Instagram ↗</a>}
        {st.label && <span className="text-xs font-mono" style={{ color: 'var(--faint)' }}>{st.label}</span>}
        {st.error && <span className="text-xs" style={{ color: 'var(--warn)' }}>{st.error}</span>}
      </div>
    </div>
  );
}

// ── Post to IG · right-side account panel (profile · stories · tools) ──────────
function AccountPanel({ accountId, queue = [], say }) {
  const [acc, setAcc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [storyBusy, setStoryBusy] = useState('');
  const images = queue.flatMap((g) => (g.products || []).map((p) => p.image_url)).filter(Boolean);

  const load = useCallback(() => {
    if (!accountId) { setAcc(null); return; }
    setLoading(true);
    api.skAccount(accountId).then(setAcc).catch(() => setAcc(null)).finally(() => setLoading(false));
  }, [accountId]);
  useEffect(() => { load(); }, [load]);

  const postStory = async (url) => {
    setStoryBusy(url);
    try {
      const r = await api.skStory(accountId, url, false);
      say(r?.permalink ? 'Story posted ✓' : 'Story posted');
      setTimeout(load, 1500);            // refresh active-stories after posting
    } catch (e) {
      say(e?.response?.data?.error?.message || e?.response?.data?.detail || 'Story failed (token needs instagram_content_publish + Business account)', 'error');
    } finally { setStoryBusy(''); }
  };

  const info = acc?.info || {};
  const num = (n) => (n == null ? '—' : Number(n).toLocaleString('en-IN'));
  return (
    <aside className="post-aside">
      {/* profile */}
      <div className="panel p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <div className="eyebrow">Account</div>
          <button className="btn btn-sm btn-ghost" onClick={load} disabled={loading}>{loading ? <Spinner size={12} /> : <Icon name="history" size={12} />}</button>
        </div>
        {!accountId ? <p className="text-xs" style={{ color: 'var(--muted)' }}>Pick an account to see its profile.</p> : (
          <>
            <div className="flex items-center gap-3 mb-3">
              {info.profile_picture_url
                ? <img src={info.profile_picture_url} alt="" className="avatar" />
                : <div className="avatar" style={{ background: 'var(--panel-2)' }} />}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{info.username ? '@' + info.username : (acc?.handle ? '@' + acc.handle : acc?.label || '—')}</div>
                <div className="text-xs" style={{ color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{info.name || ''}</div>
              </div>
            </div>
            <div className="stat-row">
              <div className="stat"><b>{num(info.followers_count)}</b><span>followers</span></div>
              <div className="stat"><b>{num(info.follows_count)}</b><span>following</span></div>
              <div className="stat"><b>{num(info.media_count)}</b><span>posts</span></div>
              <div className="stat"><b>{acc?.story_count ?? 0}</b><span>stories</span></div>
            </div>
            {!info.username && (
              <p className="text-xs mt-2" style={{ color: 'var(--warn)' }}>
                {info._error
                  ? <>Can't read this account: <span style={{ color: 'var(--muted)' }}>{info._error}</span></>
                  : <>Profile metrics need a token with <code>pages_show_list</code> + <code>instagram_manage_insights</code>.</>}
              </p>
            )}
          </>
        )}
      </div>

      {/* active stories */}
      {accountId && (
        <div className="panel p-4 mb-4">
          <div className="eyebrow mb-2">Active stories · {acc?.story_count ?? 0}</div>
          {acc?.stories?.length ? (
            <div className="story-strip">
              {acc.stories.map((s) => (
                <a key={s.id} href={s.permalink || '#'} target="_blank" rel="noopener" className="story-thumb" title={s.timestamp}>
                  {(s.thumbnail_url || s.media_url) ? <img src={s.thumbnail_url || s.media_url} alt="" /> : <span className="ph" />}
                </a>
              ))}
            </div>
          ) : <p className="text-xs" style={{ color: 'var(--muted)' }}>No active stories (last 24h).</p>}
        </div>
      )}

      {/* post a story from queued product images */}
      {accountId && (
        <div className="panel p-4 mb-4">
          <div className="eyebrow mb-2">Post a story</div>
          {images.length ? (
            <>
              <p className="text-xs mb-2" style={{ color: 'var(--muted)' }}>Tap a product image to post it as a Story now.</p>
              <div className="story-strip">
                {images.slice(0, 8).map((url, i) => (
                  <button key={i} className="story-thumb btn-reset" onClick={() => postStory(url)} disabled={!!storyBusy} title="Post as story">
                    <img src={url} alt="" />
                    {storyBusy === url && <span className="story-loading"><Spinner size={13} /></span>}
                  </button>
                ))}
              </div>
            </>
          ) : <p className="text-xs" style={{ color: 'var(--muted)' }}>Queue products in Affiliate to post them as stories.</p>}
        </div>
      )}

      {/* tools + honest capability notes */}
      <div className="panel p-4">
        <div className="eyebrow mb-2">Tools</div>
        <div className="tool-note"><Icon name="check" size={13} style={{ color: '#3fb950' }} /> Post to Story — live (above)</div>
        <div className="tool-note"><Icon name="x" size={13} style={{ color: 'var(--faint)' }} /> Highlights — <span>not offered by the Instagram API; manage in the app</span></div>
        <div className="tool-note"><Icon name="x" size={13} style={{ color: 'var(--faint)' }} /> Add IG music — <span>API can't attach catalog audio; only baked-in video audio</span></div>
      </div>
    </aside>
  );
}

// ══════════════════════════════════ HISTORY ═══════════════════════════════════
function HistoryTab({ active }) {
  const [posts, setPosts] = useState(null);
  const [stats, setStats] = useState(null);
  // Always-mounted now, so refetch whenever this tab becomes visible (fresh after a post).
  useEffect(() => { if (active === false) return; skApi.posts(100).then((d) => { setPosts(d.posts || []); setStats(d.stats); }).catch(() => setPosts([])); }, [active]);
  if (posts === null) return <div className="panel p-6 text-center"><Spinner /></div>;
  return (
    <div className="panel p-4">
      <div className="eyebrow mb-3">Posting history · {stats?.total_posts ?? posts.length} posts</div>
      {posts.length === 0 ? <Empty text="No posts yet — post carousels from the Post tab." /> : (
        <div className="text-sm">
          {posts.map((p) => (
            <div key={p.id} className="flex gap-3 py-2 items-center" style={{ borderTop: '1px solid var(--border)' }}>
              <span className="font-mono" style={{ color: 'var(--accent)', width: 140 }}>{p.label}</span>
              <StatusPill s={p.status} />
              <span style={{ color: 'var(--muted)', width: 90 }}>{p.product_count} products</span>
              <span className="font-mono text-xs" style={{ color: 'var(--faint)', flex: 1 }} title={p.product_asins.join(', ')}>{p.product_asins.slice(0, 3).join(', ')}</span>
              <span className="text-xs" style={{ color: 'var(--faint)' }}>{(p.posted_at || '').slice(0, 16).replace('T', ' ')}</span>
              {p.permalink && <a href={p.permalink} target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>↗</a>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════ STOREFRONT (HUB) ══════════════════════════
function HubTab({ cats, say }) {
  const [products, setProducts] = useState(null);
  const [cat, setCat] = useState('');
  const [pub, setPub] = useState(null);       // { url, repo } — the public storefront
  const [publishing, setPublishing] = useState(false);
  useEffect(() => { skApi.hub(cat || undefined).then((d) => setProducts(d.products || [])).catch(() => setProducts([])); }, [cat]);
  useEffect(() => { api.skStorefrontUrl().then(setPub).catch(() => setPub(null)); }, []);
  const [coll, setColl] = useState(null);   // {price_bands, bundles}
  useEffect(() => { skApi.collections(cat || undefined).then(setColl).catch(() => setColl(null)); }, [cat]);

  const localUrl = (c) => `/sk-api/hub${c ? `?category=${c}` : ''}`;
  const publicUrl = pub?.url || null;
  const connected = pub?.repo?.connected;

  const publish = async () => {
    setPublishing(true);
    try {
      const r = await api.skPublishStorefront();
      setPub((p) => ({ ...(p || {}), url: r.url }));
      say('Storefront published to your public link 🎉');
    } catch (e) {
      say(e?.response?.data?.error?.message || e?.response?.data?.detail || 'Publish failed', 'error');
    } finally { setPublishing(false); }
  };
  const copyPublic = () => publicUrl && navigator.clipboard?.writeText(publicUrl).then(() => say('Public link copied — paste it in your IG bio'));

  return (
    <>
      {/* PUBLIC link-in-bio card */}
      <div className="panel p-5 mb-5" style={{ borderColor: 'var(--accent)' }}>
        <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
          <div className="eyebrow">🔗 Public storefront · your Instagram bio link</div>
          <div className="flex gap-2">
            <button className="btn btn-sm" onClick={publish} disabled={publishing}>
              {publishing ? <><Spinner size={13} /> Publishing…</> : <><Icon name="ext" size={13} /> Publish / refresh</>}
            </button>
          </div>
        </div>
        <p className="text-sm mb-3" style={{ color: 'var(--muted)' }}>
          One public Amazon page with <b>{products?.length ?? '…'}</b> of your products — every card links to Amazon with your tag.
          Hit <b>Publish / refresh</b> after posting to update it, then paste this link into your Instagram bio.
        </p>
        {publicUrl ? (
          <div className="flex items-center gap-2 flex-wrap">
            <a href={publicUrl} target="_blank" rel="noopener" className="font-mono text-sm" style={{ color: 'var(--accent)', wordBreak: 'break-all' }}>{publicUrl}</a>
            <button className="btn btn-sm btn-ghost" onClick={copyPublic}><Icon name="doc" size={13} /> Copy</button>
            <a className="btn btn-sm btn-ghost" href={publicUrl} target="_blank" rel="noopener"><Icon name="ext" size={13} /> Open ↗</a>
          </div>
        ) : (
          <p className="text-xs" style={{ color: 'var(--warn)' }}>
            {connected === false ? 'Connect a GitHub token in the Settings panel to publish a public link.' : 'Click Publish to create your public link.'}
          </p>
        )}
        <p className="text-xs mt-2" style={{ color: 'var(--faint)' }}>Hosted free on GitHub Pages · updates ~1 min after each publish.</p>
      </div>

      {/* LOCAL preview + filter */}
      <div className="panel p-5 mb-5">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
          <div className="eyebrow">Preview (local)</div>
          <a className="btn btn-sm btn-ghost" href={localUrl(cat)} target="_blank" rel="noopener"><Icon name="ext" size={13} /> Open local ↗</a>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className={cx('chip-sk', !cat && 'on')} onClick={() => setCat('')}>all</span>
          {cats.map((c) => <span key={c.name} className={cx('chip-sk', cat === c.name && 'on')} onClick={() => setCat(c.name)}>{c.name}</span>)}
        </div>
      </div>

      {/* SMART COLLECTIONS — auto price-bands + budget-true bundles (discovery engine) */}
      {coll && (coll.price_bands?.length > 0 || coll.bundles?.length > 0) && (
        <div className="panel p-5 mb-5">
          <div className="eyebrow mb-3">Smart collections · auto-built from your products</div>
          {coll.price_bands?.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {coll.price_bands.map((b) => (
                <span key={b.band} className="chip-sk" title={`${b.count} products`}>{b.band}<span className="rate">{b.count}</span></span>
              ))}
            </div>
          )}
          {coll.bundles?.length > 0 && (
            <div className="flex flex-col gap-2">
              {coll.bundles.map((b, i) => (
                <div key={i} className="queue-row">
                  <span className="chip-sk on">{b.title}</span>
                  <div className="queue-thumbs">
                    {(b.products || []).map((p, j) => p.image ? <img key={j} src={p.image} alt="" /> : <span key={j} className="ph" />)}
                  </div>
                  <span className="text-xs font-mono" style={{ color: '#3fb950', marginLeft: 'auto' }}>₹{Number(b.combined_price).toLocaleString('en-IN')} total · {b.count} items</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {products && products.length === 0 ? <Empty text="No products yet — post carousels first (Post to IG)." /> : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {(products || []).map((p, i) => (
            <a key={p.asin + i} className="panel p-0 overflow-hidden" href={p.affiliate_link} target="_blank" rel="noopener"
              style={{ textDecoration: 'none', color: 'inherit', display: 'flex', flexDirection: 'column' }}>
              {p.image ? <img src={p.image} alt="" style={{ width: '100%', height: 150, objectFit: 'contain', background: '#fff' }} /> : <div style={{ height: 150, background: 'var(--panel-2)' }} />}
              <div className="p-3" style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <div className="text-xs" style={{ color: 'var(--muted)', textTransform: 'uppercase' }}>{p.category}</div>
                <div style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.25 }}>{(p.product_title || '').slice(0, 70)}</div>
                <div className="flex items-center justify-between"><b>{p.price}</b><span className="text-xs" style={{ color: 'var(--accent)' }}>Shop ↗</span></div>
              </div>
            </a>
          ))}
        </div>
      )}
    </>
  );
}

// ── shared bits ──────────────────────────────────────────────────────────────
// Professional category selector: checkbox cards, each with a per-category product
// stepper that appears once selected. Clicking the card toggles; the stepper controls count.
function CategoryGrid({ cats, counts, onToggle, onCount, disabled }) {
  return (
    <div className={cx('cat-grid', disabled && 'is-disabled')}>
      {cats.map((c) => {
        const on = c.name in counts;
        return (
          <div key={c.name} className={cx('cat-card', on && 'on')} onClick={() => onToggle(c.name)}>
            <span className={cx('cat-check', on && 'on')}>{on && <Icon name="check" size={12} />}</span>
            <div className="cat-main">
              <div className="cat-name">{c.name}</div>
              <div className="cat-rate">~{c.rate}% commission</div>
            </div>
            {on && (
              <div className="cat-step" onClick={(e) => e.stopPropagation()}>
                <button onClick={() => onCount(c.name, -1)} aria-label="less">−</button>
                <span>{counts[c.name] || 3}</span>
                <button onClick={() => onCount(c.name, 1)} aria-label="more">+</button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ProductCard({ it, copy, fav, onFav, say }) {
  const [why, setWhy] = useState(false);
  const caption = `${it.summary || ''}\n\n${(it.hashtags || []).map((h) => '#' + h).join(' ')}`.trim();
  const sendToIG = () => { copy(`${caption}\n\nImage: ${it.image_url}`, 'Caption+image'); say('Copied — paste into Custom Poster', 'ok'); };
  // Winner score is the master (intelligence × confidence, novelty/trend/performance-aware);
  // fall back to the content score/tier for older responses.
  const wTier = it.winner_tier || it.tier;
  const wScore = it.winner_score != null ? it.winner_score : it.content_score;
  const warns = it.content_warnings || [];
  return (
    <div className="panel p-0 overflow-hidden" style={{ display: 'flex', flexDirection: 'column' }}>
      <div style={{ position: 'relative' }}>
        {it.image_url ? <img src={hiRes(it.image_url)} alt="" loading="lazy" style={{ width: '100%', height: 160, objectFit: 'contain', background: '#fff' }} /> : <div style={{ height: 160, background: 'var(--panel-2)' }} />}
        {wTier && <span className={cx('tier-badge', 'tier-' + wTier)} title={`Winner score ${wScore}/100 · intelligence×confidence`}>🏆 {wTier} · {wScore}</span>}
        <button className="fav-btn" onClick={onFav} title="Favorite" style={{ color: fav ? 'var(--amber)' : '#fff' }}><Icon name="spark" size={16} /></button>
      </div>
      <div className="p-3.5" style={{ display: 'flex', flexDirection: 'column', gap: 7, flex: 1 }}>
        <div className="flex items-center gap-2 flex-wrap text-xs font-mono" style={{ color: 'var(--muted)' }}>
          <b style={{ color: 'var(--text)', fontSize: 15 }}>{it.price}</b>
          {it.orig_price && <span style={{ textDecoration: 'line-through', color: 'var(--faint)' }}>{it.orig_price}</span>}
          {it.discount_pct != null && <span style={{ color: '#3fb950' }}>-{it.discount_pct}%</span>}
          {it.price_band && <span className="band-tag">{it.price_band}</span>}
        </div>
        {it.content_score != null && (
          <div className="score-row">
            <ScoreBar label="IG" v={it.instagram_score} title="Instagram / visual appeal" />
            <ScoreBar label="Buy" v={it.purchase_intent_score} title="Purchase intent" />
            <ScoreBar label="Val" v={it.value_score} title="Value for money" />
            <ScoreBar label="Cnt" v={it.content_potential_score} title="Content potential" />
          </div>
        )}
        {/* Autopilot intelligence chips — novelty (freshness) + trend alignment */}
        {(it.novelty_score != null || it.trend_score != null || it.performance_prior != null) && (
          <div className="flex items-center gap-1.5 flex-wrap">
            {it.novelty_score != null && <span className="intel-chip" title="Novelty — how unlike already-posted products">✨ {it.novelty_score}</span>}
            {it.trend_score != null && <span className="intel-chip" title="Trend alignment">📈 {it.trend_score}</span>}
            {it.performance_prior != null && <span className="intel-chip" title="Measured category performance prior">🎯 {it.performance_prior}</span>}
            {(it.evidence || []).length > 0 && <button className="intel-chip why" onClick={() => setWhy((v) => !v)}>Why {why ? '▾' : '▸'}</button>}
          </div>
        )}
        {why && (it.evidence || []).length > 0 && (
          <ul className="why-list">{it.evidence.map((e, i) => <li key={i}>✓ {e}</li>)}</ul>
        )}
        <div className="flex items-center gap-3 text-xs font-mono" style={{ color: 'var(--muted)' }}>
          {it.rating != null && <span>★ {it.rating}</span>}
          {it.reviews != null && <span>{Number(it.reviews).toLocaleString()} rev</span>}
          {it.bought_past_month && <span>{it.bought_past_month} bought</span>}
          {it.badge && <span style={{ color: 'var(--accent)' }}>{it.badge}</span>}
        </div>
        {warns.length > 0 && <div className="warn-tag" title={warns.join('\n')}>⚠ {warns.length} unverified claim{warns.length === 1 ? '' : 's'}</div>}
        <div style={{ fontWeight: 600, fontSize: 13.5, lineHeight: 1.25 }}>{it.product_title || it.title}</div>
        <div style={{ flex: 1 }} />
        <div className="flex gap-2 mt-1">
          <button className="btn btn-sm" style={{ flex: 1, justifyContent: 'center' }} onClick={() => copy(it.affiliate_link, 'Link')}><Icon name="ext" size={12} /> Link</button>
          <button className="btn btn-sm" style={{ flex: 1, justifyContent: 'center' }} onClick={() => copy(caption, 'Caption')}><Icon name="quote" size={12} /> Caption</button>
          <button className="btn btn-sm" style={{ flex: 1, justifyContent: 'center' }} onClick={sendToIG} title="Copy for Custom Poster"><Icon name="pin" size={12} /> IG</button>
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════ OVERVIEW (blueprint §6) ═══════════════════
function OverviewPanel({ active, health, stats, accounts = [], go }) {
  const [perf, setPerf] = useState(null);
  const [wins, setWins] = useState(null);
  useEffect(() => {
    if (!active) return;
    skApi.perfOverview().then(setPerf).catch(() => setPerf(null));
    skApi.winners(6).then((d) => setWins(d.winners || [])).catch(() => setWins([]));
  }, [active]);
  const totals = perf?.totals || {};
  const connected = perf?.connected;
  const card = (label, value) => (
    <div className="stat-tile"><div className="stat-v">{value == null ? 'Not connected' : value}</div><div className="stat-k">{label}</div></div>
  );
  const StatusDot = ({ ok, warn, label }) => (
    <div className="flex items-center gap-2 text-sm"><span style={{ width: 9, height: 9, borderRadius: '50%', background: ok ? '#3fb950' : warn ? 'var(--amber)' : 'var(--faint)' }} />{label}<span className="flex-1" /><span className="text-xs font-mono" style={{ color: 'var(--faint)' }}>{ok ? 'Healthy' : warn ? 'Optional' : '—'}</span></div>
  );
  return (
    <div className="mb-24 flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {card('Posted products', stats?.total_seen ?? 0)}
        {card('IG accounts', accounts.length)}
        {card('Link clicks', connected ? (totals.link_clicks ?? '—') : null)}
        {card('Orders', connected ? (totals.orders ?? '—') : null)}
        {card('Commission', connected ? (totals.commission != null ? '₹' + totals.commission : '—') : null)}
        {card('Winner products', wins == null ? '…' : wins.length)}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="panel p-4">
          <div className="eyebrow mb-3">Operational status</div>
          <div className="flex flex-col gap-2.5">
            <StatusDot ok={health?.ok} label="Product discovery" />
            <StatusDot ok={accounts.length > 0} label="Instagram session" />
            <StatusDot ok={stats?.db_ok} label="Database" />
            <StatusDot ok={(stats?.total_seen ?? 0) > 0} warn={stats?.db_ok} label="RAG memory" />
            <StatusDot ok={connected} warn label="Affiliate tracking" />
          </div>
        </div>
        <div className="panel p-4">
          <div className="eyebrow mb-3">Next best action</div>
          {accounts.length === 0
            ? <ActionCard title="Connect an Instagram account" body="Add your IG account so carousels can be published." btn="Go to Accounts" onClick={() => go?.('accounts')} />
            : <ActionCard title="Find winning products" body="Run Discover to surface fresh, high-winner-score products, then publish." btn="Open Discover" onClick={() => go?.('sk-affiliate')} />}
        </div>
      </div>
      <div className="panel p-4">
        <div className="eyebrow mb-3">Recent winners</div>
        {wins == null ? <div className="text-center p-4"><Spinner size={16} /></div>
          : wins.length === 0 ? <p className="text-sm" style={{ color: 'var(--muted)' }}>No posted products yet — publish a few carousels and your top winners show here.</p>
          : <div className="flex flex-col gap-2">{wins.map((w, i) => (
              <div key={(w.asin || '') + i} className="acct-row">
                {w.winner_tier && <span className={cx('mini', 'on')} style={{ minWidth: 44, textAlign: 'center' }}>{w.winner_tier} · {w.winner_score}</span>}
                <span style={{ flex: 1, minWidth: 0, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{w.product_title || w.title}</span>
                <span className="text-xs font-mono" style={{ color: 'var(--muted)' }}>{w.price}</span>
              </div>))}</div>}
      </div>
    </div>
  );
}
const ActionCard = ({ title, body, btn, onClick }) => (
  <div><div style={{ fontWeight: 600, fontSize: 14 }}>{title}</div>
    <p className="text-sm mt-1 mb-3" style={{ color: 'var(--muted)' }}>{body}</p>
    <button className="btn btn-sm" onClick={onClick}>{btn} <Icon name="chevR" size={14} /></button></div>
);

// ══════════════════════════════════ WINNERS (Phase 10) ════════════════════════
function WinnersPanel({ active, say }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const copy = (t, l) => navigator.clipboard?.writeText(t).then(() => say?.(`${l} copied`));
  const load2 = () => { setLoading(true); skApi.winners(24).then(setData).catch(() => setData({ winners: [] })).finally(() => setLoading(false)); };
  useEffect(() => { if (active && !data) load2(); }, [active]);
  const winners = data?.winners || [];
  return (
    <div className="mb-24">
      <div className="panel p-4 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div><div className="eyebrow mb-1">Predicted winners</div>
          <p className="text-xs" style={{ color: 'var(--muted)' }}>Ranked by winner score across your posted products. Method: <b>{data?.method || '—'}</b>{data?.has_model ? ' · model' : ''}.</p></div>
        <button className="btn btn-sm" onClick={load2} disabled={loading}>{loading ? <Spinner size={13} /> : <Icon name="bolt" size={13} />} Refresh</button>
      </div>
      {winners.length === 0
        ? <Empty text="No posted products yet — winners are ranked from what you've published. Post a few carousels, then check back." />
        : <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {winners.map((it, i) => <ProductCard key={(it.asin || '') + i} it={it} copy={copy} fav={false} onFav={() => {}} say={say} />)}
          </div>}
    </div>
  );
}

// ══════════════════════════════════ TRENDS (Phase 3) ══════════════════════════
const DIR_COLOR = { EXPLODING: '#ff6b6b', RISING: '#3fb950', STABLE: 'var(--muted)', DECLINING: 'var(--faint)', UNKNOWN: 'var(--faint)' };
function TrendsPanel({ active }) {
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const load2 = () => { setLoading(true); skApi.trends().then((d) => setRows(d.trends || [])).catch(() => setRows([])).finally(() => setLoading(false)); };
  useEffect(() => { if (active && rows === null) load2(); }, [active]);
  return (
    <div className="mb-24">
      <div className="panel p-4 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div><div className="eyebrow mb-1">Trend intelligence</div>
          <p className="text-xs" style={{ color: 'var(--muted)' }}>Keyword momentum + direction, learned from past runs. Momentum is an internal model score, not a market fact.</p></div>
        <button className="btn btn-sm" onClick={load2} disabled={loading}>{loading ? <Spinner size={13} /> : <Icon name="bolt" size={13} />} Refresh</button>
      </div>
      {(rows || []).length === 0
        ? <Empty text="No trend data yet — trends build as you run Find products (each run records keyword observations)." />
        : <div className="panel p-4"><div className="flex flex-col gap-2.5">
            {rows.map((t) => (
              <div key={t.category + t.keyword} className="trend-row">
                <span className="trend-kw">{t.keyword}</span>
                <span className="trend-cat">{t.category}</span>
                <div className="score-track" style={{ flex: 1, maxWidth: 220 }}><div className="score-fill" style={{ width: `${t.momentum}%`, background: DIR_COLOR[t.direction] || 'var(--accent)' }} /></div>
                <span className="font-mono text-xs" style={{ width: 34, textAlign: 'right' }}>{t.momentum}</span>
                <span className="dir-tag" style={{ color: DIR_COLOR[t.direction], borderColor: DIR_COLOR[t.direction] }}>{t.direction}</span>
              </div>
            ))}
          </div></div>}
    </div>
  );
}

// ══════════════════════════════════ INTELLIGENCE (Phases 6-8) ═════════════════
function IntelligencePanel({ active }) {
  const [ins, setIns] = useState(null);
  const [perf, setPerf] = useState(null);
  const [ret, setRet] = useState(null);
  const [q, setQ] = useState(null);
  const load2 = () => {
    skApi.insights().then(setIns).catch(() => setIns(null));
    skApi.perfOverview().then(setPerf).catch(() => setPerf(null));
    skApi.retailers().then(setRet).catch(() => setRet(null));
    skApi.discoveryQueries().then((d) => setQ(d.queries || [])).catch(() => setQ([]));
  };
  useEffect(() => { if (active && !ins) load2(); }, [active]);
  const connected = perf?.connected;
  return (
    <div className="mb-24 flex flex-col gap-4">
      {/* Performance */}
      <div className="panel p-4">
        <div className="eyebrow mb-2">Performance</div>
        {!connected
          ? <p className="text-sm" style={{ color: 'var(--muted)' }}>Not connected — measured metrics (reach, clicks, orders, commission) appear here once a source feeds <span className="font-mono">/api/performance/ingest</span>. No numbers are ever guessed.</p>
          : <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {Object.entries(perf.derived || {}).map(([k, v]) => (
                <div key={k} className="stat-tile"><div className="stat-v">{v == null ? '—' : v}</div><div className="stat-k">{k.replace(/_/g, ' ')}</div></div>
              ))}
            </div>}
      </div>
      {/* Learned recommendations */}
      <div className="panel p-4">
        <div className="eyebrow mb-2">Learned recommendations</div>
        {ins?.has_data
          ? <div className="flex flex-col gap-2">
              {(ins.categories || []).map((c) => <div key={c.category} className="reco-row"><b>{c.category}</b><span className="score-track" style={{ flex: 1, maxWidth: 200 }}><span className="score-fill" style={{ width: `${c.prior}%`, background: 'var(--accent)' }} /></span><span className="font-mono text-xs">{c.prior} · {c.samples} posts</span></div>)}
            </div>
          : <p className="text-sm" style={{ color: 'var(--muted)' }}>{ins?.note || 'No performance data yet — recommendations appear once posts have measured results.'}</p>}
      </div>
      {/* Discovery query yields */}
      <div className="panel p-4">
        <div className="eyebrow mb-2">Discovery query yields</div>
        {(q || []).length === 0
          ? <p className="text-sm" style={{ color: 'var(--muted)' }}>No data yet — the discovery planner learns which search intents produce the most fresh products as you run.</p>
          : <div className="flex flex-wrap gap-2">{q.slice(0, 24).map((r) => <span key={r.category + r.query} className="q-chip" title={`${r.fresh_total} fresh / ${r.usage_count} runs`}>{r.query} · {r.priority}</span>)}</div>}
      </div>
      {/* Retailers */}
      <div className="panel p-4">
        <div className="eyebrow mb-2">Retailers</div>
        <div className="flex flex-wrap gap-2">
          {(ret?.adapters || []).map((a) => <span key={a.retailer} className="q-chip" style={{ opacity: a.implemented ? 1 : 0.5 }}>{a.healthy ? '🟢' : a.implemented ? '🟡' : '⚪'} {a.retailer}{a.implemented ? '' : ' (soon)'}</span>)}
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════ REVENUE + PERFORMANCE (Phase 6/7) ═════════
function RevenuePanel({ active, say }) {
  const [ov, setOv] = useState(null);
  const [posts, setPosts] = useState(null);
  const [form, setForm] = useState({ post_id: '', reach: '', saves: '', link_clicks: '', orders: '', commission: '' });
  const [busy, setBusy] = useState(false);
  const reload = () => { skApi.perfOverview().then(setOv).catch(() => setOv(null)); skApi.perfPosts().then((d) => setPosts(d.posts || [])).catch(() => setPosts([])); };
  useEffect(() => { if (active && !ov) reload(); }, [active]);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const ingest = async () => {
    if (!form.post_id) return say?.('Enter the post id (from History)', 'error');
    const metrics = {}; ['reach', 'saves', 'link_clicks', 'orders', 'commission'].forEach((k) => { if (form[k] !== '') metrics[k] = Number(form[k]); });
    setBusy(true);
    try { await skApi.perfIngest({ post_id: String(form.post_id), source: 'manual', metrics }); setForm({ post_id: '', reach: '', saves: '', link_clicks: '', orders: '', commission: '' }); reload(); say?.('Performance saved — learning updated'); }
    catch { say?.('Save failed', 'error'); } finally { setBusy(false); }
  };
  const connected = ov?.connected;
  return (
    <div className="mb-24 flex flex-col gap-4">
      <div className="panel p-4">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
          <div className="eyebrow">Revenue &amp; performance</div>
          <button className="btn btn-sm" onClick={async () => { try { const r = await api.affSyncPerformance(); reload(); say?.(`Synced ${r.synced} post(s) from Instagram`); } catch { say?.('Sync needs a connected IG account + posted carousels', 'error'); } }}><Icon name="bolt" size={13} /> Sync from Instagram</button>
        </div>
        {!connected
          ? <p className="text-sm" style={{ color: 'var(--muted)' }}>No measured results yet. Enter metrics below (from your Instagram insights + affiliate dashboard) to start the learning loop — nothing is ever guessed.</p>
          : <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {Object.entries(ov.derived || {}).map(([k, v]) => <div key={k} className="stat-tile"><div className="stat-v">{v == null ? '—' : (k.includes('commission') ? '₹' + v : v)}</div><div className="stat-k">{k.replace(/_/g, ' ')}</div></div>)}
            </div>}
      </div>
      <div className="panel p-4">
        <div className="eyebrow mb-2">Log a post's results</div>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <label className="fld"><span>Post id</span><input className="sk-input" value={form.post_id} onChange={set('post_id')} placeholder="e.g. 12" /></label>
          <label className="fld"><span>Reach</span><input className="sk-input" value={form.reach} onChange={set('reach')} inputMode="numeric" /></label>
          <label className="fld"><span>Saves</span><input className="sk-input" value={form.saves} onChange={set('saves')} inputMode="numeric" /></label>
          <label className="fld"><span>Clicks</span><input className="sk-input" value={form.link_clicks} onChange={set('link_clicks')} inputMode="numeric" /></label>
          <label className="fld"><span>Orders</span><input className="sk-input" value={form.orders} onChange={set('orders')} inputMode="numeric" /></label>
          <label className="fld"><span>Commission ₹</span><input className="sk-input" value={form.commission} onChange={set('commission')} inputMode="numeric" /></label>
        </div>
        <div className="flex justify-end mt-3"><button className="btn btn-sm" onClick={ingest} disabled={busy}>{busy ? <Spinner size={13} /> : <Icon name="check" size={13} />} Save results</button></div>
      </div>
      {(posts || []).length > 0 && (
        <div className="panel p-4"><div className="eyebrow mb-2">Logged posts (by outcome)</div>
          <div className="flex flex-col gap-2">{posts.map((p) => <div key={p.post_id} className="acct-row"><span className="prog-badge">post {p.post_id}</span><span className="text-xs font-mono" style={{ color: 'var(--muted)', flex: 1 }}>{Object.entries(p.metrics).filter(([, v]) => v != null).map(([k, v]) => `${k}:${v}`).join(' · ') || 'no metrics'}</span><b>{p.outcome_score ?? '—'}</b></div>)}</div>
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════ CONTENT CALENDAR (Phase 5) ════════════════
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const CAL_PLAN = { Mon: 'Home', Tue: 'Electronics', Wed: 'Fashion', Thu: 'Books', Fri: 'Home', Sat: 'Trending', Sun: 'Weekly Winners' };
function CalendarPanel({ active, say }) {
  const [jobs, setJobs] = useState(null);
  const [acct, setAcct] = useState(null);
  const reload = () => { skApi.pubQueue().then((d) => setJobs(d.jobs || [])).catch(() => setJobs([])); skApi.pubAccount().then((d) => setAcct(d.health)).catch(() => setAcct(null)); };
  useEffect(() => { if (active && jobs === null) reload(); }, [active]);
  const estop = async (on) => { try { await skApi.pubEmergencyStop(on); reload(); say?.(on ? 'Emergency stop ON' : 'Emergency stop cleared'); } catch { say?.('Failed', 'error'); } };
  const cancel = async (id) => { try { await skApi.pubCancel(id); reload(); } catch { say?.('Failed', 'error'); } };
  const byDay = {};
  (jobs || []).forEach((j) => { const d = j.scheduled_for ? new Date(j.scheduled_for) : null; const key = d ? DOW[d.getDay()] : 'Unscheduled'; (byDay[key] = byDay[key] || []).push(j); });
  return (
    <div className="mb-24 flex flex-col gap-4">
      <div className="panel p-4 flex items-center justify-between flex-wrap gap-3">
        <div><div className="eyebrow mb-1">Content calendar &amp; queue</div>
          <p className="text-xs" style={{ color: 'var(--muted)' }}>Scheduled publishing jobs (spaced automatically). Account: <b style={{ color: acct?.status === 'STOPPED' ? 'var(--danger)' : acct?.status === 'ATTENTION' ? 'var(--amber)' : '#3fb950' }}>{acct?.status || '—'}</b></p></div>
        <div className="flex gap-2">
          <button className="btn btn-sm" onClick={reload}><Icon name="bolt" size={13} /> Refresh</button>
          {acct?.emergency_stop ? <button className="btn btn-sm" style={{ borderColor: '#3fb950', color: '#3fb950' }} onClick={() => estop(false)}>Resume</button>
            : <button className="btn btn-sm" style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }} onClick={() => estop(true)}>🛑 Emergency stop</button>}
        </div>
      </div>
      <div className="panel p-4">
        <div className="eyebrow mb-2">Suggested weekly plan</div>
        <div className="cal-grid">{DOW.slice(1).concat('Sun').map((d) => (
          <div key={d} className="cal-cell"><div className="cal-dow">{d}</div><div className="cal-cat">{CAL_PLAN[d]}</div>
            {(byDay[d] || []).map((j) => <div key={j.job_id} className="cal-job">{j.category || 'post'} · {j.status}<button className="cal-x" onClick={() => cancel(j.job_id)}>×</button></div>)}
          </div>
        ))}</div>
        <p className="text-xs mt-2" style={{ color: 'var(--faint)' }}>Plan is a suggestion; actual schedule follows fresh inventory + performance. Enqueue posts from Find products → send to queue (coming online as the IG service consumes the queue).</p>
      </div>
    </div>
  );
}

// ══════════════════════════════════ AGENTS CONTROL PANEL (editable) ═══════════
function AgentsPanel({ active, say }) {
  const [data, setData] = useState(null);
  const reload = () => skApi.agents().then((d) => setData(d.agents || [])).catch(() => setData([]));
  useEffect(() => { if (active && data === null) reload(); }, [active]);
  const save = async (key, value) => { try { const r = await skApi.setAgentSetting(key, value); if (r.ok === false) return say?.(r.error || 'Invalid', 'error'); reload(); say?.(`${key} = ${value}`); } catch { say?.('Save failed', 'error'); } };
  const reset = async (key) => { try { await skApi.clearAgentSetting(key); reload(); say?.(`${key} reset to default`); } catch { say?.('Failed', 'error'); } };
  return (
    <div className="mb-24 flex flex-col gap-4">
      <div className="panel p-4"><div className="eyebrow mb-1">Agents</div>
        <p className="text-xs" style={{ color: 'var(--muted)' }}>Every capability is an agent. Tunable constraints below take effect within a few seconds — no restart. Blank = using the config default.</p></div>
      {data === null ? <div className="panel p-6 text-center"><Spinner size={16} /></div>
        : data.map((a) => (
          <div key={a.name} className="panel p-4">
            <div className="flex items-center gap-2 mb-1"><span className="prog-badge">{a.name}</span><span className="text-xs" style={{ color: 'var(--muted)' }}>{a.role}</span></div>
            {a.editable.length === 0 ? <p className="text-xs" style={{ color: 'var(--faint)' }}>No runtime knobs (behaviour fixed / env-only).</p>
              : <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2">
                  {a.editable.map((k) => <AgentKnob key={k.key} k={k} onSave={save} onReset={reset} />)}
                </div>}
          </div>
        ))}
    </div>
  );
}
function AgentKnob({ k, onSave, onReset }) {
  const [v, setV] = useState(k.value ?? '');
  useEffect(() => { setV(k.value ?? ''); }, [k.value]);
  return (
    <div className="knob-row">
      <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 12.5, fontWeight: 600 }}>{k.label}</div>
        <div className="text-xs font-mono" style={{ color: 'var(--faint)' }}>{k.key}{k.type !== 'str' ? ` · ${k.min}–${k.max}` : ''}</div></div>
      <input className="sk-input" style={{ width: 96 }} value={v} onChange={(e) => setV(e.target.value)} placeholder="default" />
      <button className="mini on" onClick={() => onSave(k.key, v)}>Set</button>
      {k.is_overridden && <button className="mini" onClick={() => onReset(k.key)}>Reset</button>}
    </div>
  );
}

// ══════════════════════════════════ AFFILIATE ACCOUNTS (encrypted .ragskey) ═══
const PROGRAM_LABEL = {
  amazon_associates: 'Amazon Associates', earnkaro: 'EarnKaro', cuelinks: 'Cuelinks',
  inrdeals: 'INRDeals', flipkart_affiliate: 'Flipkart Affiliate', vcommission: 'vCommission',
  admitad: 'Admitad', impact: 'Impact', other: 'Other',
};
function AccountsPanel({ active, say }) {
  const [list, setList] = useState(null);
  const [programs, setPrograms] = useState([]);
  const [form, setForm] = useState({ program: 'amazon_associates', label: '', tracking_id: '', api_key: '', api_secret: '', link_template: '', notes: '' });
  const [busy, setBusy] = useState(false);
  const reload = () => api.affAccounts().then(setList).catch(() => setList([]));
  useEffect(() => { if (active && list === null) { reload(); api.affPrograms().then(setPrograms).catch(() => setPrograms(Object.keys(PROGRAM_LABEL))); } }, [active]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const add = async () => {
    if (!form.tracking_id && !form.api_key) return say?.('Enter a tracking id/tag or an API key', 'error');
    setBusy(true);
    try { await api.affConnect(form); setForm((f) => ({ ...f, label: '', tracking_id: '', api_key: '', api_secret: '', link_template: '', notes: '' })); await reload(); say?.('Affiliate account saved (encrypted)'); }
    catch (e) { say?.(e?.response?.data?.detail || 'Save failed', 'error'); }
    finally { setBusy(false); }
  };
  const del = async (id) => { if (!window.confirm('Remove this affiliate account?')) return; try { await api.affDelete(id); await reload(); say?.('Removed'); } catch { say?.('Delete failed', 'error'); } };
  const toggle = async (a) => { try { await api.affUpdate(a.id, { is_active: !a.is_active }); await reload(); } catch { say?.('Update failed', 'error'); } };

  return (
    <div className="mb-24 flex flex-col gap-4">
      <div className="panel p-4">
        <div className="eyebrow mb-1">Affiliate accounts</div>
        <p className="text-xs mb-3" style={{ color: 'var(--muted)' }}>Secrets are encrypted at rest with your <span className="font-mono">.ragskey</span> and never shown again — only a masked preview. The tracking id/tag is stored as-is (it appears in your affiliate links).</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="fld"><span>Program</span>
            <select className="sk-select" value={form.program} onChange={set('program')}>
              {(programs.length ? programs : Object.keys(PROGRAM_LABEL)).map((p) => <option key={p} value={p}>{PROGRAM_LABEL[p] || p}</option>)}
            </select>
          </label>
          <label className="fld"><span>Label (optional)</span><input className="sk-input" value={form.label} onChange={set('label')} placeholder="e.g. Main Amazon" /></label>
          <label className="fld"><span>Tracking id / tag</span><input className="sk-input" value={form.tracking_id} onChange={set('tracking_id')} placeholder="e.g. sparkle060b-21" /></label>
          <label className="fld"><span>API key (encrypted)</span><input className="sk-input" type="password" value={form.api_key} onChange={set('api_key')} placeholder="optional" autoComplete="new-password" /></label>
          <label className="fld"><span>API secret (encrypted)</span><input className="sk-input" type="password" value={form.api_secret} onChange={set('api_secret')} placeholder="optional" autoComplete="new-password" /></label>
          <label className="fld"><span>Link template (optional)</span><input className="sk-input" value={form.link_template} onChange={set('link_template')} placeholder="https://…?url={url_encoded}" /></label>
        </div>
        <div className="flex justify-end mt-3"><button className="btn btn-sm" onClick={add} disabled={busy}>{busy ? <Spinner size={13} /> : <Icon name="check" size={13} />} Save account</button></div>
      </div>

      {list === null ? <div className="panel p-6 text-center"><Spinner size={16} /></div>
        : list.length === 0 ? <Empty text="No affiliate accounts yet. Add one above — it's stored encrypted and can be used by the engine." />
        : <div className="flex flex-col gap-2">
            {list.map((a) => (
              <div key={a.id} className="acct-row">
                <span className="prog-badge">{PROGRAM_LABEL[a.program] || a.program}</span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{a.label || a.tracking_id || `#${a.id}`}</div>
                  <div className="text-xs font-mono" style={{ color: 'var(--muted)' }}>
                    {a.tracking_id && <>tag {a.tracking_id} · </>}
                    {a.has_api_key ? `key ${a.api_key_masked}` : 'no key'}{a.has_api_secret ? ' · secret set' : ''}
                  </div>
                </div>
                <button className={cx('mini', a.is_active && 'on')} onClick={() => toggle(a)} title="Active">{a.is_active ? 'active' : 'off'}</button>
                <button className="mini danger" onClick={() => del(a.id)}>Remove</button>
              </div>
            ))}
          </div>}
    </div>
  );
}

function ScoreBar({ label, v, title }) {
  const val = Math.max(0, Math.min(100, Number(v) || 0));
  const col = val >= 80 ? '#3fb950' : val >= 60 ? 'var(--accent)' : val >= 40 ? 'var(--amber)' : 'var(--faint)';
  return (
    <div className="score-cell" title={`${title}: ${val}/100`}>
      <div className="score-cap">{label}</div>
      <div className="score-track"><div className="score-fill" style={{ width: `${val}%`, background: col }} /></div>
    </div>
  );
}
const Chip = ({ children, ok }) => <span style={{ border: '1px solid var(--border)', borderRadius: 20, padding: '2px 9px', color: ok === true ? '#3fb950' : ok === false ? 'var(--danger)' : 'var(--muted)' }}>{children}</span>;
const StatusPill = ({ s }) => { const c = s === 'posted' ? '#3fb950' : s === 'failed' ? 'var(--danger)' : 'var(--warn)'; return <span className="font-mono" style={{ fontSize: 11, color: c, border: `1px solid ${c}`, borderRadius: 12, padding: '1px 8px' }}>{s}</span>; };
function PhasePill({ r }) {
  if (r.phase === 'done') return <StatusPill s={r.status || 'done'} />;
  const map = { queued: 'queued', generating: 'generating…', posting: 'posting…' };
  return <span className="font-mono" style={{ fontSize: 11, color: 'var(--accent)', border: '1px solid var(--border)', borderRadius: 12, padding: '1px 8px' }}>{map[r.phase] || r.phase}</span>;
}
const Empty = ({ text }) => <div className="panel p-6 text-center text-sm" style={{ color: 'var(--muted)' }}>{text}</div>;
const Field = ({ label, children }) => <div className="mt-3"><label className="text-xs" style={{ color: 'var(--muted)', display: 'block', marginBottom: 5 }}>{label}</label>{children}</div>;
function Slider({ label, min, max, step = 1, value, onChange, width = 200 }) {
  return <div><label className="text-xs" style={{ color: 'var(--muted)' }}>{label}</label>
    <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ display: 'block', width, accentColor: 'var(--accent)', marginTop: 6 }} /></div>;
}
function NotReachable({ onRetry }) {
  return <div className="fade-up"><p className="eyebrow mb-2">Business-SK</p><h1 className="font-display text-4xl mb-6" style={{ fontWeight: 600 }}>Affiliate</h1>
    <div className="panel p-8 text-center"><Icon name="bolt" size={24} className="mx-auto mb-3" style={{ color: 'var(--danger)' }} />
      <p className="font-display text-lg mb-1">Affiliate API not reachable</p>
      <p className="text-sm" style={{ color: 'var(--muted)' }}>The affiliate_backend (:8100) isn’t responding. Start it in Docker, then retry.</p>
      <button className="btn btn-sm mt-4" onClick={onRetry}>Retry</button></div></div>;
}

// helpers
// Upgrade an Amazon thumbnail URL to the full-resolution original (strip the ._SIZE_ token).
// Idempotent: an already-hi-res URL (no "._") is returned unchanged.
const hiRes = (url) => {
  if (!url) return url;
  const u = String(url).split('?')[0];
  if (!u.includes('._')) return u;
  const base = u.split('._')[0];
  const ext = (u.split('.').pop() || 'jpg').toLowerCase();
  return `${base}.${['jpg', 'jpeg', 'png', 'webp'].includes(ext) ? ext : 'jpg'}`;
};
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } };
function buildCaption(cat, pins) {
  const lines = [`🛍️ Top ${cat} picks this week`, ''];
  pins.forEach((p, i) => lines.push(`${i + 1}. ${(p.product_title || '').slice(0, 70)} — ${p.price}${p.rating ? ` · ${p.rating}★` : ''}`));
  const tags = (pins[0]?.hashtags || []).slice(0, 6).map((h) => '#' + h).join(' ');
  lines.push('', '🔗 Shop all via the link in bio 👆', '', `#ad ${tags}`.trim());
  return lines.join('\n').slice(0, 2200);
}
function downloadCSV(items) {
  const cols = ['asin', 'category', 'product_title', 'price', 'orig_price', 'discount_pct', 'rating', 'reviews', 'affiliate_link'];
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [cols.join(','), ...items.map((it) => cols.map((c) => esc(it[c])).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'affiliate_products.csv'; a.click();
}
const CardStyles = () => <style>{`
  .chip-sk{cursor:pointer;user-select:none;border:1px solid var(--border);border-radius:20px;padding:5px 12px;font-size:13px;color:var(--text);display:inline-flex;gap:7px;align-items:center;background:var(--panel-2)}
  .chip-sk:hover{border-color:var(--accent)} .chip-sk.on{background:rgba(120,180,255,.10);border-color:var(--accent)}
  .chip-sk .rate{font:600 10px ui-monospace,monospace;color:var(--muted)}
  .sk-input{width:100%;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px 11px;color:var(--text);font-size:13px;outline:none}
  .sk-input:focus{border-color:var(--accent)}
  .mini{font:600 10px system-ui;padding:2px 8px;border:1px solid var(--border);border-radius:20px;background:transparent;color:var(--muted);cursor:pointer}
  .mini:hover{border-color:var(--accent);color:var(--text)}
  .toggle-sk{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--text);cursor:pointer}
  .toggle-sk input{width:15px;height:15px;accent-color:var(--accent)}
  .fav-btn{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.5);border:none;border-radius:8px;padding:5px;cursor:pointer;display:inline-flex}

  /* step headers */
  .step-head{display:inline-flex;align-items:center;gap:10px;font-weight:600;font-size:15px;color:var(--text)}
  .step-n{width:23px;height:23px;border-radius:50%;display:grid;place-items:center;font:700 12px system-ui;background:var(--accent);color:#0b1220}
  .divider{height:1px;background:var(--border);margin:18px 0}
  .group-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}

  /* big buttons */
  .btn-lg{font-size:14px;padding:11px 18px;border-radius:11px;gap:8px}

  /* category grid — checkbox cards + per-category stepper */
  .cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:11px}
  .cat-grid.is-disabled{opacity:.4;pointer-events:none}
  .cat-card{position:relative;display:flex;align-items:center;gap:11px;padding:13px 14px;border:1px solid var(--border);border-radius:13px;background:var(--panel-2);cursor:pointer;transition:border-color .15s,background .15s;user-select:none}
  .cat-card:hover{border-color:var(--accent)}
  .cat-card.on{border-color:var(--accent);background:rgba(120,180,255,.09)}
  .cat-check{flex-shrink:0;width:20px;height:20px;border-radius:6px;border:1.5px solid var(--border);display:grid;place-items:center;color:#0b1220;transition:all .15s}
  .cat-check.on{background:var(--accent);border-color:var(--accent)}
  .cat-main{flex:1;min-width:0}
  .cat-name{font-weight:600;font-size:14px;text-transform:capitalize}
  .cat-rate{font:600 10.5px ui-monospace,monospace;color:var(--muted);margin-top:2px}
  .cat-step{flex-shrink:0;display:inline-flex;align-items:center;gap:2px;background:var(--bg-2,rgba(0,0,0,.25));border:1px solid var(--border);border-radius:9px;overflow:hidden}
  .cat-step button{width:26px;height:28px;border:none;background:transparent;color:var(--text);font-size:16px;cursor:pointer;display:grid;place-items:center}
  .cat-step button:hover{background:rgba(120,180,255,.15);color:var(--accent)}
  .cat-step span{min-width:22px;text-align:center;font:700 13px ui-monospace,monospace;color:var(--accent)}

  /* Post to IG — two-column layout + account side panel */
  .post-layout{display:grid;grid-template-columns:1fr;gap:20px}
  @media(min-width:1024px){.post-layout{grid-template-columns:minmax(0,1fr) 320px}}
  .post-main{min-width:0}
  /* right account panel stays FIXED while the posts list scrolls */
  .post-aside{min-width:0}
  @media(min-width:1024px){.post-aside{position:sticky;top:16px;align-self:start;max-height:calc(100vh - 32px);overflow-y:auto}}
  .avatar{width:48px;height:48px;border-radius:50%;object-fit:cover;border:1px solid var(--border);flex-shrink:0}
  .stat-row{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
  .stat{display:flex;flex-direction:column;align-items:center;padding:7px 4px;border:1px solid var(--border);border-radius:9px;background:var(--panel-2)}
  .stat b{font-size:14px} .stat span{font-size:9.5px;color:var(--muted);text-transform:uppercase;margin-top:2px}
  .story-strip{display:flex;gap:7px;flex-wrap:wrap}
  .story-thumb{position:relative;width:52px;height:70px;border-radius:9px;overflow:hidden;border:1px solid var(--border);background:var(--panel-2);cursor:pointer;padding:0;display:block}
  .story-thumb img{width:100%;height:100%;object-fit:cover}
  .story-thumb .ph{display:block;width:100%;height:100%}
  .story-thumb:hover{border-color:var(--accent)}
  .story-loading{position:absolute;inset:0;display:grid;place-items:center;background:rgba(0,0,0,.5)}
  .btn-reset{border:1px solid var(--border);background:var(--panel-2)}
  .tool-note{display:flex;gap:8px;align-items:flex-start;font-size:12px;color:var(--text);padding:6px 0;border-top:1px solid var(--border)}
  .tool-note:first-of-type{border-top:none}
  .tool-note span{color:var(--muted)}

  /* subcategory refine */
  .subcat-wrap{margin-top:14px;padding:13px;border:1px solid var(--border);border-radius:12px;background:var(--panel-2);display:flex;flex-direction:column;gap:11px}
  .subcat-block{display:flex;flex-direction:column;gap:6px}
  .subcat-head{font:600 11px ui-monospace,monospace;color:var(--muted);text-transform:capitalize}
  .subcat-head span{color:var(--faint);text-transform:none}
  .subchip{cursor:pointer;user-select:none;font-size:12px;padding:3px 9px;border-radius:16px;border:1px solid var(--border);background:var(--panel);color:var(--muted);text-transform:capitalize}
  .subchip:hover{border-color:var(--accent);color:var(--text)}
  .subchip.on{background:rgba(120,180,255,.12);border-color:var(--accent);color:var(--accent)}

  /* progress pills */
  .prog-pill{display:inline-flex;align-items:center;gap:6px;font:600 12px system-ui;padding:4px 10px;border:1px solid var(--border);border-radius:20px;color:var(--muted);background:var(--panel-2);text-transform:capitalize}
  .prog-pill.err{border-color:var(--danger);color:var(--danger)}
  .prog-pill .dot{width:7px;height:7px;border-radius:50%;background:var(--faint)}

  /* posts meter (1–10) */
  .posts-meter{font:600 13px system-ui;color:var(--text);display:inline-flex;align-items:baseline;gap:6px}
  .posts-meter b{font-size:18px;color:var(--accent)} .posts-meter span{font-size:11px;color:var(--faint)}
  .posts-meter.over b{color:var(--danger)}

  /* processing panel */
  .proc-panel{border:1px solid var(--border);border-radius:12px;background:var(--panel-2);padding:12px}
  .proc-head{display:flex;align-items:center;gap:7px;font:600 12px system-ui;color:var(--accent);margin-bottom:9px}

  /* per-post publish cards */
  .post-card{border:1px solid var(--border);border-radius:14px;background:var(--panel);padding:15px}
  .post-card.is-done{border-color:#3fb95055} .post-card.is-fail{border-color:var(--danger)}

  /* Instagram-style post preview card */
  .ig-card{border:1px solid var(--border);border-radius:16px;background:var(--panel);overflow:hidden;max-width:560px}
  .ig-card.is-done{border-color:#3fb95066} .ig-card.is-fail{border-color:var(--danger)}
  .ig-top{display:flex;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid var(--border)}
  .ig-dot{width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,var(--amber-2,#ffd25a),var(--accent));flex-shrink:0}
  .ig-user{font-weight:600;font-size:13px} .ig-sub{font-size:11px;color:var(--muted);text-transform:capitalize}
  .ig-media{position:relative;background:#fff;aspect-ratio:1/1;max-height:440px;display:grid;place-items:center;overflow:hidden}
  .ig-media img{width:100%;height:100%;object-fit:contain}
  .ig-ph{width:100%;height:100%;background:var(--panel-2)}
  .ig-nav{position:absolute;top:50%;transform:translateY(-50%);width:30px;height:30px;border-radius:50%;border:none;background:rgba(0,0,0,.45);color:#fff;font-size:19px;cursor:pointer;display:grid;place-items:center}
  .ig-nav.l{left:8px} .ig-nav.r{right:8px} .ig-nav:hover{background:rgba(0,0,0,.7)}
  .ig-idx{position:absolute;top:10px;right:10px;background:rgba(0,0,0,.6);color:#fff;font:600 11px ui-monospace,monospace;padding:2px 8px;border-radius:20px}
  .ig-dots{position:absolute;bottom:10px;left:0;right:0;display:flex;gap:5px;justify-content:center}
  .ig-dots .d{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.5)} .ig-dots .d.on{background:#fff}
  .ig-info{display:flex;align-items:center;gap:8px;padding:11px 14px 4px;flex-wrap:wrap}
  .ig-info b{font-size:15px} .ig-title{font-size:12px;color:var(--muted);flex:1;min-width:120px}
  .ig-cap{padding:4px 14px 12px;font-size:12.5px;line-height:1.5;color:var(--text);white-space:pre-wrap}
  .ig-tags{color:#79c0ff;font-size:12px;margin-top:6px;line-height:1.5}
  .ig-more{border:none;background:none;color:var(--accent);cursor:pointer;font-size:12px;padding:0}
  .ig-foot{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 14px;border-top:1px solid var(--border)}
  /* real-post button — clearly a live action */
  .btn-post{background:#3fb950 !important;border-color:#3fb950 !important;color:#04210e !important;font-weight:700}
  .btn-post:hover{background:#48c95c !important}
  .btn-post:disabled{opacity:.5}
  /* complete-details product list */
  .ig-details{padding:2px 14px 10px}
  .ig-list{display:flex;flex-direction:column;gap:6px;margin-top:8px}
  .ig-list-row{display:flex;align-items:center;gap:10px;padding:6px;border:1px solid var(--border);border-radius:9px;text-decoration:none;color:inherit;background:var(--panel-2)}
  .ig-list-row:hover{border-color:var(--accent)}
  .ig-list-row img,.ig-list-row .ph{width:38px;height:38px;border-radius:6px;object-fit:contain;background:#fff;flex-shrink:0}
  .ig-list-title{font-size:12px;font-weight:600;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ig-list-meta{display:flex;gap:8px;align-items:center;font:600 11px ui-monospace,monospace;color:var(--muted);margin-top:2px}
  .mini-tier{color:#0b1220;border-radius:5px;padding:0 5px;font-weight:800}

  /* Post to IG queue */
  .queue-row{display:flex;align-items:center;gap:12px;padding:9px 11px;border:1px solid var(--border);border-radius:12px;background:var(--panel-2)}
  .queue-thumbs{display:flex;gap:5px}
  .queue-thumbs img,.queue-thumbs .ph{width:34px;height:34px;border-radius:7px;object-fit:contain;background:#fff;border:1px solid var(--border)}
  .queue-thumbs .ph{background:var(--panel)}
  .empty-cta{text-align:center;padding:26px 16px;border:1px dashed var(--border);border-radius:13px}
  .run-toggle{display:inline-flex;align-items:center;gap:8px;font:600 13px system-ui;padding:9px 14px;border:1px solid var(--border);border-radius:11px;cursor:pointer;color:var(--muted);user-select:none}
  .run-toggle .dotp{width:9px;height:9px;border-radius:50%;background:var(--faint)}
  .run-toggle.live{color:#3fb950;border-color:#3fb950}
  .run-toggle.live .dotp{background:#3fb950;box-shadow:0 0 8px #3fb95088}

  /* sticky send bar */
  .action-bar{position:sticky;bottom:16px;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-top:16px;padding:13px 18px;border:1px solid var(--accent);border-radius:14px;background:var(--panel);box-shadow:0 10px 34px rgba(0,0,0,.45)}

  /* tier badge + score bars (discovery engine) */
  .tier-badge{position:absolute;top:8px;left:8px;font:800 11px ui-monospace,monospace;padding:3px 8px;border-radius:8px;color:#0b1220;letter-spacing:.02em}
  .tier-S{background:linear-gradient(135deg,#ffd25a,#ff9d2f)} .tier-A{background:#3fb950;color:#04210e}
  .tier-B{background:#58a6ff;color:#04182e} .tier-C{background:#8b949e;color:#0b1220} .tier-D{background:#484f58;color:#c9d1d9}
  .band-tag{margin-left:auto;font:600 10px ui-monospace,monospace;color:var(--accent);border:1px solid var(--border);border-radius:20px;padding:1px 8px}
  .score-row{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:1px 0 2px}
  .score-cell{display:flex;flex-direction:column;gap:3px}
  .score-cap{font:600 9px ui-monospace,monospace;color:var(--faint);text-transform:uppercase}
  .score-track{height:5px;border-radius:3px;background:var(--panel-2);overflow:hidden}
  .score-fill{height:100%;border-radius:3px;transition:width .3s}

  /* Autopilot studio — sub-nav, intelligence chips, panels */
  .sk-subnav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px;border-bottom:1px solid var(--border);padding-bottom:2px}
  .sk-subtab{border:none;background:none;color:var(--muted);font:600 13px system-ui;padding:8px 14px;border-radius:9px 9px 0 0;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
  .sk-subtab:hover{color:var(--text)}
  .sk-subtab.on{color:var(--accent);border-bottom-color:var(--accent)}
  .sk-select{background:var(--panel-2);border:1px solid var(--border);border-radius:9px;color:var(--text);font-size:12px;padding:5px 8px;text-transform:capitalize}
  .intel-chip{display:inline-flex;align-items:center;gap:3px;font:600 10.5px ui-monospace,monospace;color:var(--muted);background:var(--panel-2);border:1px solid var(--border);border-radius:20px;padding:2px 8px}
  .intel-chip.why{cursor:pointer;color:var(--accent);border-color:var(--accent)}
  .why-list{list-style:none;margin:0;padding:8px 10px;background:var(--panel-2);border:1px solid var(--border);border-radius:9px;display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--muted)}
  .warn-tag{display:inline-flex;align-items:center;gap:4px;font:600 10.5px ui-monospace,monospace;color:var(--amber);border:1px solid var(--amber);border-radius:20px;padding:1px 8px;width:fit-content}
  .style-tag{font:600 10px ui-monospace,monospace;color:var(--accent);border:1px solid var(--border);border-radius:20px;padding:1px 8px;text-transform:capitalize}
  .trend-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .trend-kw{font-weight:600;font-size:13px;min-width:130px}
  .trend-cat{font:600 10px ui-monospace,monospace;color:var(--faint);text-transform:capitalize}
  .dir-tag{font:700 9.5px ui-monospace,monospace;border:1px solid;border-radius:20px;padding:1px 7px}
  .stat-tile{border:1px solid var(--border);border-radius:11px;background:var(--panel-2);padding:11px}
  .stat-v{font:700 18px system-ui;color:var(--text)} .stat-k{font-size:10px;color:var(--muted);text-transform:capitalize;margin-top:2px}
  .reco-row{display:flex;align-items:center;gap:10px;font-size:13px;text-transform:capitalize}
  .q-chip{font:600 11px ui-monospace,monospace;color:var(--muted);background:var(--panel-2);border:1px solid var(--border);border-radius:20px;padding:3px 9px}
  /* affiliate accounts panel */
  .fld{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}
  .sk-input{background:var(--panel-2);border:1px solid var(--border);border-radius:9px;color:var(--text);font-size:13px;padding:7px 10px}
  .sk-input:focus{outline:none;border-color:var(--accent)}
  .acct-row{display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--border);border-radius:12px;background:var(--panel-2)}
  .posted-row{display:flex;align-items:center;gap:12px;padding:9px 11px;border:1px solid #3fb95033;border-radius:11px;background:var(--panel-2)}
  .prog-badge{font:700 10.5px ui-monospace,monospace;color:var(--accent);border:1px solid var(--accent);border-radius:8px;padding:3px 8px;white-space:nowrap}
  .mini.on{color:#3fb950;border-color:#3fb950}
  .mini.danger{color:var(--danger);border-color:var(--danger)}
  /* content calendar */
  .cal-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
  .cal-cell{border:1px solid var(--border);border-radius:11px;background:var(--panel-2);padding:9px;min-height:84px}
  .cal-dow{font:700 11px ui-monospace,monospace;color:var(--faint)}
  .cal-cat{font-size:12.5px;font-weight:600;margin-top:2px}
  .cal-job{display:flex;align-items:center;gap:4px;font:600 10px ui-monospace,monospace;color:var(--accent);background:var(--panel);border:1px solid var(--border);border-radius:7px;padding:2px 6px;margin-top:5px}
  .cal-x{margin-left:auto;border:none;background:none;color:var(--danger);cursor:pointer;font-size:14px;line-height:1}
  /* agent knob rows */
  .knob-row{display:flex;align-items:center;gap:8px;padding:8px;border:1px solid var(--border);border-radius:10px;background:var(--panel-2)}
`}</style>;
