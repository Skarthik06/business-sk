import { useEffect, useState } from 'react';
import skApi from '../services/skApi';
import api from '../services/api';
import { Icon, Spinner, cx } from './ui';

// Amazon-style controls (kept local so this panel is self-contained).
const GOALS = [['balanced', 'Balanced'], ['commission', 'Best sellers'], ['volume', 'High volume']];
const AUD = [['', 'Everyone'], ['men', 'Men'], ['women', 'Women'], ['kids', 'Kids']];
const STYLES = ['auto', 'DEAL_DROP', 'LISTICLE', 'STORY', 'PREMIUM', 'BUDGET', 'VIRAL_FIND'];

// "My Store" — the owner's OWN Shopify store as a first-party product source. Products come straight
// from their catalogue (Admin API), links stay raw (direct sale). Same render → Post-to-IG pipeline.
export default function MyShopifyPanel({ say, setQueue }) {
  const [st, setSt] = useState(null);            // connection status
  const [cols, setCols] = useState([]);          // collections
  const [col, setCol] = useState('');
  const [q, setQ] = useState('');
  const [count, setCount] = useState(8);
  const [goal, setGoal] = useState('balanced');
  const [style, setStyle] = useState('auto');
  const [aud, setAud] = useState('');
  const [priceMax, setPriceMax] = useState(0);
  const [running, setRunning] = useState(false);
  const [group, setGroup] = useState(null);
  const [slides, setSlides] = useState(null);
  const [prev, setPrev] = useState(false);

  const load = () => skApi.myStoreStatus().then((r) => { setSt(r); if (r.connected) skApi.myStoreCollections().then((c) => setCols(c.collections || [])).catch(() => {}); }).catch(() => setSt({ ok: false, connected: false }));
  useEffect(() => { load(); }, []);

  const previewSlides = async () => {
    if (!group) return;
    setPrev(true); setSlides(null);
    try { const res = await api.skRenderPreview(group.products.slice(0, 10), { category: group.category }); setSlides(res.images || res.local || []); if (!(res.images || []).length) say?.('Rendered but no images returned', 'error'); }
    catch { say?.('Preview render failed', 'error'); } finally { setPrev(false); }
  };
  const gen = async () => {
    setRunning(true); setSlides(null);
    try {
      const opts = { count, content: style, goal, audience: aud, q: q.trim(), collection: col };
      if (priceMax) opts.price_max = priceMax;
      const r = await skApi.myStoreGenerate(opts);
      const products = r.items || [];
      if (r.ok && products.length) {
        const g = { id: 'mystore', label: (st?.shop || 'My Store') + ' · Products', category: 'mystore', products, caption: r.caption || '', hashtags: r.hashtags || [], content_style: '', cover_tags: [] };
        setGroup(g);
        setQueue && setQueue((prevQ) => [...(prevQ || []).filter((x) => x.id !== g.id), g]);
        say?.(`Generated ${products.length} products from your store → Post to IG`);
      } else say?.(r.note || r.error || 'Nothing generated — add products or change filters', 'error');
    } catch { say?.('Generate failed', 'error'); } finally { setRunning(false); }
  };
  const discard = (asin) => setGroup((g) => { if (!g) return g; const ng = { ...g, products: g.products.filter((p) => p.asin !== asin) }; setQueue && setQueue((prevQ) => (prevQ || []).map((x) => (x.id === ng.id ? ng : x)).filter((x) => x.products.length)); return ng.products.length ? ng : null; });
  const clearAll = () => { setGroup((g) => { if (g && setQueue) setQueue((prevQ) => (prevQ || []).filter((x) => x.id !== g.id)); return null; }); };

  if (!st) return <div className="panel p-4"><div className="eyebrow">🏪 My Store</div><div className="text-xs mt-2 flex items-center gap-2" style={{ color: 'var(--muted)' }}><Spinner size={12} /> Checking connection…</div></div>;

  // Not connected → setup card.
  if (!st.connected) {
    return (
      <div className="panel p-4" style={{ borderColor: 'var(--border)' }}>
        <div className="eyebrow">🏪 My Store · your own Shopify catalogue</div>
        <div className="text-xs mt-1" style={{ color: 'var(--muted)' }}>Post your own products on Instagram — real photos + prices, direct links (100% margin, no commission split).</div>
        <div className="panel p-3 mt-3" style={{ background: 'var(--panel-2)' }}>
          <div className="text-xs" style={{ fontWeight: 700, marginBottom: 6 }}>Not connected yet — one-time setup:</div>
          <ol className="text-xs" style={{ color: 'var(--muted)', lineHeight: 1.7, paddingLeft: 18 }}>
            <li>Shopify admin → <b>Settings → Apps and sales channels → Develop apps</b></li>
            <li>Create an app → <b>Admin API scopes</b> → enable <b>read_products</b> → Install</li>
            <li>Reveal the <b>Admin API access token</b> (<code>shpat_…</code>)</li>
            <li>Add to the server <code>.env</code>: <code>SHOPIFY_STORE_DOMAIN</code> + <code>SHOPIFY_ADMIN_TOKEN</code>, then restart</li>
          </ol>
          {st.error && <div className="text-xs mt-2" style={{ color: 'var(--danger)' }}>⚠ {st.error}</div>}
          <button className="btn btn-sm mt-2" onClick={load}><Icon name="bolt" size={12} /> Re-check connection</button>
        </div>
      </div>
    );
  }

  // Connected → generator.
  return (
    <div className="panel p-4 flex flex-col gap-3" style={{ borderColor: 'var(--accent)' }}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <div className="eyebrow" style={{ color: 'var(--accent)' }}>🏪 My Store · {st.shop}</div>
          <div className="text-xs" style={{ color: 'var(--muted)' }}>Connected{st.product_count != null ? ` · ${st.product_count} products` : ''} · your catalogue → real product carousels → Post to IG (direct links).</div>
        </div>
        <button className="btn btn-sm btn-ghost" onClick={load} title="Refresh connection / catalogue"><Icon name="bolt" size={12} /> Refresh</button>
      </div>

      <div className="flex gap-2 flex-wrap">
        <input className="sk-input" style={{ flex: 1, minWidth: 180 }} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && gen()} placeholder="Search your catalogue — or leave blank for top products" />
        {cols.length > 0 && (
          <select className="sk-input" style={{ maxWidth: 200 }} value={col} onChange={(e) => setCol(e.target.value)}>
            <option value="">All collections</option>
            {cols.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <div className="ctrl-card-label" style={{ marginBottom: 5 }}>Goal</div>
          <div className="ctrl-chips">{GOALS.map(([k, l]) => <button key={k} type="button" className={cx('chip-sk', goal === k && 'on')} onClick={() => setGoal(k)}>{l}</button>)}</div>
          <div className="ctrl-card-label" style={{ margin: '10px 0 5px' }}>Audience</div>
          <div className="ctrl-chips">{AUD.map(([k, l]) => <button key={k || 'all'} type="button" className={cx('chip-sk', aud === k && 'on')} onClick={() => setAud(k)}>{l}</button>)}</div>
        </div>
        <div className="flex flex-col gap-2">
          <div className="ctrl-card-label">Caption style</div>
          <select className="sk-input" style={{ width: '100%' }} value={style} onChange={(e) => setStyle(e.target.value)}>{STYLES.map((s) => <option key={s} value={s}>{s.replace(/_/g, ' ').toLowerCase()}</option>)}</select>
          <div className="ctrl-card-label" style={{ marginTop: 4 }}>Max price (₹, 0 = any)</div>
          <input className="sk-input" type="number" min={0} step={500} value={priceMax} onChange={(e) => setPriceMax(Number(e.target.value) || 0)} />
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs" style={{ color: 'var(--faint)' }}>products</span>
        <button className="mini" onClick={() => setCount((n) => Math.max(3, n - 1))}>−</button>
        <b style={{ minWidth: 18, textAlign: 'center', display: 'inline-block' }}>{count}</b>
        <button className="mini" onClick={() => setCount((n) => Math.min(10, n + 1))}>+</button>
        <span className="flex-1" />
        {group && <button className="btn btn-sm btn-ghost" onClick={gen} disabled={running} title="Regenerate"><Icon name="bolt" size={12} /> Refresh</button>}
        {group && <button className="btn btn-sm btn-ghost" onClick={clearAll} style={{ color: 'var(--danger)' }} title="Remove from queue"><Icon name="x" size={12} /> Clear</button>}
        <button className="btn btn-sm" onClick={gen} disabled={running || !setQueue}>{running ? <Spinner size={12} /> : <Icon name="bolt" size={12} />} Generate posts</button>
      </div>

      {group && (
        <div>
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className="prog-badge">✓ {group.products.length} products → Post to IG</span>
            <span className="flex-1" />
            <button className="btn btn-sm btn-ghost" onClick={previewSlides} disabled={prev}>{prev ? <Spinner size={12} /> : <Icon name="doc" size={12} />} Preview slides</button>
          </div>
          {group.caption && <div className="panel p-3 mb-2" style={{ background: 'var(--panel-2)' }}><div className="text-xs" style={{ color: 'var(--muted)', whiteSpace: 'pre-wrap' }}>{group.caption}</div></div>}
          {slides && slides.length > 0 && (
            <div className="flex gap-2 overflow-x-auto pb-2 mb-2">{slides.map((u, i) => <img key={i} src={u} alt={`slide ${i + 1}`} style={{ height: 220, borderRadius: 10, border: '1px solid var(--border)', flex: 'none' }} />)}</div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {group.products.map((it) => (
              <div key={it.asin} className="panel p-0 overflow-hidden" style={{ display: 'flex', flexDirection: 'column' }}>
                <div style={{ position: 'relative' }}>
                  {it.image_url ? <img src={it.image_url} alt="" loading="lazy" style={{ width: '100%', height: 130, objectFit: 'contain', background: '#fff' }} /> : <div style={{ height: 130, background: 'var(--panel)' }} />}
                  <button className="fav-btn" onClick={() => discard(it.asin)} title="Discard" style={{ color: '#fff' }}><Icon name="x" size={15} /></button>
                </div>
                <div className="p-3" style={{ display: 'flex', flexDirection: 'column', gap: 5, flex: 1 }}>
                  <div className="text-xs" style={{ fontWeight: 600, lineHeight: 1.3, maxHeight: 34, overflow: 'hidden' }}>{it.product_title}</div>
                  <div className="flex items-center gap-2 text-xs font-mono">{it.price && <b style={{ fontSize: 14 }}>{it.price}</b>}{it.orig_price && <span style={{ textDecoration: 'line-through', color: 'var(--faint)' }}>{it.orig_price}</span>}{it.discount_pct != null && <span style={{ color: '#3fb950' }}>-{it.discount_pct}%</span>}</div>
                  {it.affiliate_link && <a className="btn btn-sm" href={it.affiliate_link} target="_blank" rel="noreferrer" style={{ justifyContent: 'center' }}><Icon name="ext" size={12} /> Open product</a>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
