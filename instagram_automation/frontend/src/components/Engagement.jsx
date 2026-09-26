import { useCallback, useEffect, useState } from 'react';
import api from '../services/api';
import { Icon, Spinner, cx } from './ui';

const hum = (s) => (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
const TRIGGERS = ['COMMENT_RECEIVED', 'DM_RECEIVED'];
const OPERATORS = ['any', 'contains', 'equals', 'starts_with', 'ends_with', 'contains_any', 'contains_all'];
const OP_LABEL = { any: 'Any message (match all)' };
const ACTIONS = ['SEND_DM', 'REPLY_TO_COMMENT', 'MARK_LEAD', 'ADD_TAG', 'LOG_EVENT'];

const BLANK = () => ({
  name: '', on_comment: true, on_dm: false, post_id: '', enabled: true, priority: 100,
  match_mode: 'all', keywords: '', operator: 'contains', case_sensitive: false,
  actions: [{ type: 'SEND_DM', message: '', ai: false }],
});
const BLANK_ACTION = () => ({ type: 'ADD_TAG', message: '', ai: false });
const NEEDS_MSG = (t) => t === 'SEND_DM' || t === 'REPLY_TO_COMMENT';

// Compact multi-series SVG line chart (no external deps — CSP-safe, theme-aware).
const SERIES = [
  { key: 'comments', label: 'Comments', color: 'var(--accent)' },
  { key: 'dms', label: 'DMs Sent', color: '#7c9cff' },
  { key: 'replies', label: 'Replies', color: '#4ade80' },
  { key: 'conversations', label: 'Conversations', color: 'var(--amber)' },
];
function EngChart({ data }) {
  if (!data) return <div className="skeleton" style={{ height: 200, borderRadius: 12 }} />;
  const W = 640, H = 200, pad = 26;
  const labels = data.labels || [];
  const all = SERIES.flatMap((s) => data[s.key] || []);
  const max = Math.max(1, ...all);
  const n = Math.max(1, labels.length - 1);
  const x = (i) => pad + (i * (W - pad * 2)) / n;
  const y = (v) => H - pad - (v / max) * (H - pad * 2);
  const path = (arr) => (arr || []).map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const ticks = labels.length > 8 ? labels.filter((_, i) => i % Math.ceil(labels.length / 7) === 0) : labels;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ overflow: 'visible' }}>
      {[0, 0.5, 1].map((g) => (
        <line key={g} x1={pad} x2={W - pad} y1={y(max * g)} y2={y(max * g)} stroke="var(--border)" strokeWidth="1" />
      ))}
      {SERIES.map((s) => (
        <path key={s.key} d={path(data[s.key])} fill="none" stroke={s.color} strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
      ))}
      {SERIES.map((s) => (data[s.key] || []).map((v, i) => (
        <circle key={`${s.key}-${i}`} cx={x(i)} cy={y(v)} r="2.5" fill={s.color} />
      )))}
      {labels.map((l, i) => (ticks.includes(l) ? (
        <text key={l} x={x(i)} y={H - 6} fontSize="9" fill="var(--faint)" textAnchor="middle">{l.slice(5)}</text>
      ) : null))}
    </svg>
  );
}

function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="text-xs font-mono" style={{ color: 'var(--faint)' }}>{hint}</span>}
    </label>
  );
}

export default function Engagement({ notify }) {
  const [accounts, setAccounts] = useState([]);
  const [accountId, setAccountId] = useState('');
  const [rules, setRules] = useState([]);
  const [stats, setStats] = useState([]);
  const [form, setForm] = useState(null);        // null | BLANK | rule-edit
  const [busy, setBusy] = useState(false);
  const [sim, setSim] = useState({ text: '', trigger_type: 'COMMENT_RECEIVED', username: 'tester' });
  const [simOut, setSimOut] = useState(null);
  const [props, setProps] = useState([]);          // properties, for auto-filling messages
  const [propId, setPropId] = useState('');
  const [filling, setFilling] = useState('');
  const [tab, setTab] = useState('automations');   // automations | inbox | comments | activity
  const [summary, setSummary] = useState(null);
  const [activity, setActivity] = useState([]);
  const [convos, setConvos] = useState([]);
  const [comments, setComments] = useState([]);
  const [posts, setPosts] = useState([]);
  const [postDetail, setPostDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  const [syncStatus, setSyncStatus] = useState(null);
  const [chart, setChart] = useState(null);
  const [chartDays, setChartDays] = useState(7);
  const [topPosts, setTopPosts] = useState([]);
  const [fgate, setFgate] = useState(null);        // official follow-gate status
  const loadFgate = useCallback(() => { api.followGateStatus().then(setFgate).catch(() => setFgate(null)); }, []);
  useEffect(() => { loadFgate(); const id = setInterval(loadFgate, 30000); return () => clearInterval(id); }, [loadFgate]);
  const toggleFgate = async () => {
    if (!fgate) return;
    try { const r = await api.followGateToggle(!fgate.official); setFgate((f) => ({ ...f, official: r.official })); notify?.(r.official ? 'Follow gate ON — links go only to verified followers' : 'Follow gate OFF — links go to everyone who comments'); }
    catch { notify?.('Toggle failed', 'error'); }
  };
  const [scopePosts, setScopePosts] = useState([]);   // published posts for the scope dropdown
  const [leads, setLeads] = useState({ leads: [], stats: {}, statuses: [] });
  const [leadFilter, setLeadFilter] = useState('');
  const [convOpen, setConvOpen] = useState(null);     // selected conversation (DM inbox)
  const [convMsgs, setConvMsgs] = useState([]);
  const [msgDraft, setMsgDraft] = useState('');
  const [commentFilter, setCommentFilter] = useState('all');
  const [commentSearch, setCommentSearch] = useState('');
  const [replyFor, setReplyFor] = useState(null);     // comment pk being replied to
  const [replyDraft, setReplyDraft] = useState('');

  useEffect(() => {
    api.bizAccounts().then((r) => {
      setAccounts(r.accounts || []);
      setAccountId((c) => c || (r.accounts?.[0]?.id ? String(r.accounts[0].id) : ''));
    }).catch(() => {});
    api.bizProperties().then((r) => {
      setProps(r.properties || []);
      setPropId((c) => c || (r.properties?.[0]?.id ? String(r.properties[0].id) : ''));
    }).catch(() => {});
  }, []);

  // Fill one action's message with a ready-made, property-grounded template
  // (facilities + Google-Maps link + phone numbers). kind: 'dm' | 'reply'.
  const fillTemplate = async (idx, kind) => {
    if (!propId) return notify?.('Add a property first (generate a post in Business)', 'error');
    setFilling(`${idx}:${kind}`);
    try {
      const r = await api.engSuggest(propId, kind);
      setAction(idx, 'message', r.message);
    } catch (e) { notify?.(e?.response?.data?.detail || 'Could not build template', 'error'); }
    finally { setFilling(''); }
  };

  const reload = useCallback(async () => {
    if (!accountId) return;
    try {
      const [a, s, sm, tp] = await Promise.all([
        api.engAutomations(accountId), api.engStats(accountId),
        api.engSummary(accountId), api.engTopPosts(accountId, 5)]);
      setRules(a.automations || []);
      setStats(s.rules || []);
      setSummary(sm);
      setTopPosts(tp.posts || []);
    } catch { notify?.('Cannot load automations', 'error'); }
  }, [accountId, notify]);
  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (!accountId) return;
    api.engCharts(accountId, chartDays).then(setChart).catch(() => {});
  }, [accountId, chartDays]);

  // published posts for the scope dropdown (Post #N → its media id)
  useEffect(() => {
    if (!accountId) return;
    api.engPosts(accountId).then((r) => setScopePosts(r.posts || [])).catch(() => {});
  }, [accountId]);

  // Load the active tab's data (also used by auto-refresh).
  const loadTab = useCallback((t = tab, resetDetail = true) => {
    if (!accountId) return;
    if (t === 'activity') api.engActivity(accountId).then((r) => setActivity(r.activity || [])).catch(() => {});
    if (t === 'inbox') api.engConversations(accountId).then((r) => setConvos(r.conversations || [])).catch(() => {});
    if (t === 'comments') api.engComments(accountId).then((r) => setComments(r.comments || [])).catch(() => {});
    if (t === 'posts') { if (resetDetail) setPostDetail(null); api.engPosts(accountId).then((r) => setPosts(r.posts || [])).catch(() => {}); }
    if (t === 'leads') api.engLeads(accountId, leadFilter).then(setLeads).catch(() => {});
  }, [tab, accountId, leadFilter]);

  const openConversation = async (c) => {
    setConvOpen(c);
    try {
      const r = await api.engMessages(c.id); setConvMsgs(r.messages || []);
      await api.engConvRead(accountId, c.id);
    } catch { setConvMsgs([]); }
  };
  const sendMsg = async () => {
    if (!msgDraft.trim() || !convOpen) return;
    try { await api.engConvSend(accountId, convOpen.id, msgDraft); setMsgDraft(''); await openConversation(convOpen); notify?.('Message sent'); }
    catch (e) { notify?.(e?.response?.data?.detail || 'Send failed', 'error'); }
  };
  const setLeadStatus = async (id, status) => {
    try { await api.engLeadStatus(accountId, id, status); loadTab('leads'); notify?.(`Lead → ${status}`); }
    catch (e) { notify?.(e?.response?.data?.detail || 'Update failed', 'error'); }
  };
  const doReply = async (pk) => {
    if (!replyDraft.trim()) return;
    try { await api.engCommentReply(accountId, pk, replyDraft); setReplyFor(null); setReplyDraft(''); loadTab('comments'); notify?.('Reply posted'); }
    catch (e) { notify?.(e?.response?.data?.detail || 'Reply failed', 'error'); }
  };
  const hideComment = async (pk, hidden) => {
    try { await api.engCommentHide(accountId, pk, hidden); loadTab('comments'); notify?.(hidden ? 'Hidden' : 'Unhidden'); }
    catch (e) { notify?.(e?.response?.data?.detail || 'Hide failed', 'error'); }
  };
  useEffect(() => { loadTab(tab); }, [tab, accountId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll the auto-sync status, and auto-refresh the panel, every 30s — so the panel
  // stays live (the backend poller pulls comments/DMs every minute on its own).
  useEffect(() => {
    if (!accountId) return;
    let cancelled = false;
    const tick = () => {
      api.engSyncStatus(accountId).then((s) => !cancelled && setSyncStatus(s)).catch(() => {});
      reload();
      loadTab(tab, false);
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, [accountId, tab, reload, loadTab]);

  const syncAll = async () => {
    setSyncingAll(true);
    try {
      const r = await api.engSyncAll(accountId, true);
      notify?.(`Synced ${r.posts} posts · ${r.new_comments} new comments · ${r.rules_fired} rules fired · ${r.new_dms} new DMs`);
      (r.warnings || []).slice(0, 3).forEach((w) => notify?.(w, 'error'));
      await reload(); loadTab(tab, false);
      api.engSyncStatus(accountId).then(setSyncStatus).catch(() => {});
    } catch (e) { notify?.(e?.response?.data?.detail || 'Sync failed', 'error'); }
    finally { setSyncingAll(false); }
  };

  const openPost = async (campaignId) => {
    setLoadingDetail(true); setPostDetail({ campaign_id: campaignId });
    try { setPostDetail(await api.engPostDetail(accountId, campaignId)); }
    catch (e) { notify?.(e?.response?.data?.detail || 'Could not load post', 'error'); setPostDetail(null); }
    finally { setLoadingDetail(false); }
  };

  const syncPost = async (campaignId, runRules) => {
    setSyncing(true);
    try {
      const r = await api.engSyncPost(accountId, campaignId, runRules);
      notify?.(`Synced ${r.synced_comments} comments (${r.new_comments} new)${runRules ? `, ${r.rules_fired} rules fired` : ''}`);
      (r.warnings || []).forEach((w) => notify?.(w, 'error'));
      await openPost(campaignId);
    } catch (e) { notify?.(e?.response?.data?.detail || 'Sync failed', 'error'); }
    finally { setSyncing(false); }
  };

  const statFor = (id) => stats.find((s) => s.id === id) || {};
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  // multi-action helpers
  const setAction = (i, k, v) => setForm((f) => ({ ...f, actions: f.actions.map((a, j) => (j === i ? { ...a, [k]: v } : a)) }));
  const addAction = () => setForm((f) => ({ ...f, actions: [...f.actions, BLANK_ACTION()] }));
  const removeAction = (i) => setForm((f) => ({ ...f, actions: f.actions.length > 1 ? f.actions.filter((_, j) => j !== i) : f.actions }));
  // Quick multi-select: toggle whether an action TYPE is part of the rule.
  const hasAction = (t) => (form?.actions || []).some((a) => a.type === t);
  const toggleActionType = (t) => setForm((f) => {
    const present = f.actions.some((a) => a.type === t);
    if (present) {
      const remaining = f.actions.filter((a) => a.type !== t);
      return { ...f, actions: remaining.length ? remaining : [{ type: 'SEND_DM', message: '', ai: false }] };
    }
    return { ...f, actions: [...f.actions, { type: t, message: '', ai: false }] };
  });

  const triggerOf = (f) => (f.on_comment && f.on_dm) ? 'BOTH' : (f.on_dm ? 'DM_RECEIVED' : 'COMMENT_RECEIVED');
  const toPayload = (f) => ({
    name: f.name || 'Rule', trigger_type: triggerOf(f), post_id: f.post_id?.trim() || null,
    enabled: f.enabled, priority: Number(f.priority) || 100, match_mode: f.match_mode,
    conditions: [{ operator: f.operator, keywords: f.keywords.split(/[,\n]/).map((k) => k.trim()).filter(Boolean), case_sensitive: f.case_sensitive }],
    actions: f.actions.map((a) => ({
      type: a.type, message: NEEDS_MSG(a.type) ? a.message : '', ai: NEEDS_MSG(a.type) && !!a.ai,
      tag: a.type === 'MARK_LEAD' ? 'Potential Lead' : (a.type === 'ADD_TAG' ? (a.message || 'Tagged') : ''),
    })),
  });

  const fromRule = (r) => ({
    id: r.id, name: r.name,
    on_comment: r.trigger_type === 'COMMENT_RECEIVED' || r.trigger_type === 'BOTH' || r.trigger_type === 'ANY',
    on_dm: r.trigger_type === 'DM_RECEIVED' || r.trigger_type === 'BOTH' || r.trigger_type === 'ANY',
    post_id: r.post_id || '',
    enabled: r.enabled, priority: r.priority, match_mode: r.match_mode || 'all',
    keywords: (r.conditions?.[0]?.keywords || []).join(', '),
    operator: r.conditions?.[0]?.operator || 'contains',
    case_sensitive: r.conditions?.[0]?.case_sensitive || false,
    actions: (r.actions?.length ? r.actions : [{ type: 'SEND_DM' }]).map((a) => ({
      type: a.type || 'SEND_DM', message: a.message || '', ai: !!a.ai })),
  });

  const save = async () => {
    if (!form.on_comment && !form.on_dm) return notify?.('Pick at least one trigger (comment or DM)', 'error');
    if (form.operator !== 'any' && !form.keywords.trim()) return notify?.('Add at least one keyword (or use "Any message")', 'error');
    if (form.actions.some((a) => NEEDS_MSG(a.type) && !a.message.trim())) return notify?.('A reply/DM action needs a message', 'error');
    setBusy(true);
    try {
      if (form.id) await api.engUpdate(accountId, form.id, toPayload(form));
      else await api.engCreate(accountId, toPayload(form));
      setForm(null); await reload(); notify?.('Automation saved');
    } catch (e) { notify?.(e?.response?.data?.detail || 'Save failed', 'error'); } finally { setBusy(false); }
  };
  const toggle = async (r) => {
    try { await api.engUpdate(accountId, r.id, { ...toPayload(fromRule(r)), enabled: !r.enabled }); await reload(); }
    catch { notify?.('Update failed', 'error'); }
  };
  const remove = async (r) => {
    if (!window.confirm(`Delete automation "${r.name}"?`)) return;
    try { await api.engDelete(accountId, r.id); await reload(); notify?.('Deleted'); }
    catch { notify?.('Delete failed', 'error'); }
  };
  const runSim = async () => {
    if (!sim.text.trim()) return;
    try { setSimOut(await api.engSimulate({ account_id: Number(accountId), ...sim })); }
    catch (e) { notify?.(e?.response?.data?.detail || 'Simulate failed', 'error'); }
  };

  return (
    <div className="fade-up accent-business max-w-5xl">
      <p className="eyebrow mb-2">Engagement automation</p>
      <h1 className="font-display text-4xl md:text-5xl" style={{ fontWeight: 600, lineHeight: 1 }}>Engagement.</h1>
      <p className="text-sm mt-2 mb-6" style={{ color: 'var(--muted)' }}>
        Deterministic, rule-based replies &amp; DMs — comment a keyword, run an action. No AI required.
      </p>

      <div className="panel p-4 mb-4 flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2">
          <span className="label" style={{ margin: 0 }}>Instagram account</span>
          <select className="select" style={{ width: 260 }} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.length === 0 && <option value="">No accounts (add in Settings)</option>}
            {accounts.map((a) => <option key={a.id} value={String(a.id)}>{a.label}{a.handle ? ` · @${a.handle}` : ''}</option>)}
          </select>
        </label>
        <div className="flex items-center gap-2">
          <span className={cx('pill', summary?.live ? 'pill-ok' : 'pill-warn')}>
            {summary?.live ? '● Live sending ON' : '○ Dry-run (nothing posts)'}
          </span>
          {tab === 'automations' && !form && <button className="btn btn-accent btn-sm" onClick={() => setForm(BLANK())}><Icon name="plus" size={15} /> Create Automation</button>}
        </div>
      </div>

      {/* Official follow gate — is_user_follow_business (compliant, no provider) */}
      {fgate && (
        <div className="panel p-4 mb-4" style={{ borderColor: fgate.official ? 'var(--accent)' : 'var(--border)' }}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="eyebrow" style={{ color: fgate.official ? 'var(--accent)' : 'var(--muted)' }}>🔒 Official follow gate</div>
              <div className="text-sm mt-1" style={{ color: 'var(--muted)' }}>
                {fgate.official
                  ? 'Comment the keyword → we verify follow via Instagram’s official API → links go to VERIFIED followers only. Others are held (no public nudge) and re-checked every 30 min for 7 days — links go out the moment they follow.'
                  : 'OFF — anyone who comments the keyword gets the links immediately.'}
                <span style={{ color: 'var(--faint)' }}> · store: {fgate.backend}</span>
              </div>
            </div>
            <button className={cx('btn btn-sm', fgate.official ? 'btn-accent' : 'btn-ghost')} onClick={toggleFgate}>
              {fgate.official ? '● Gate ON' : '○ Gate OFF'}
            </button>
          </div>
          <div className="flex flex-wrap gap-2 mt-3">
            {[['verified', 'links sent to followers'], ['held', 'held · not verified'], ['pending', 'awaiting follow now'], ['sent', 'released via DM']].map(([k, label]) => (
              <div key={k} className="panel p-2.5" style={{ minWidth: 108 }}>
                <div style={{ fontSize: 20, fontWeight: 800 }}>{fgate[k] ?? 0}</div>
                <div className="eyebrow" style={{ fontSize: '0.5rem' }}>{label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* universal sync bar */}
      <div className="panel p-4 mb-4 flex flex-wrap items-center justify-between gap-3" style={{ borderColor: 'var(--accent)' }}>
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-xl grid place-items-center shrink-0" style={{ background: 'var(--panel-2)' }}>
            {syncStatus?.auto_sync ? <span className="live-dot" /> : <Icon name="history" size={16} />}
          </div>
          <div className="min-w-0">
            <div className="text-sm" style={{ fontWeight: 600 }}>
              {syncStatus?.auto_sync ? `Auto-sync ON · every ${syncStatus?.interval_seconds || 60}s` : 'Auto-sync off'}
              {syncStatus?.live === false && <span className="pill pill-warn ml-2">dry-run</span>}
            </div>
            <div className="text-xs font-mono" style={{ color: 'var(--faint)' }}>
              {syncStatus?.last_sync
                ? `Last: ${new Date(syncStatus.last_sync.ran_at * 1000).toLocaleTimeString()} · ${syncStatus.last_sync.posts} posts · ${syncStatus.last_sync.new_comments} new comments · ${syncStatus.last_sync.rules_fired} replies`
                : 'Pulls all comments, DMs & post analytics automatically while the app runs.'}
            </div>
          </div>
        </div>
        <button className="btn btn-accent btn-sm" disabled={syncingAll || !accountId} onClick={syncAll}>
          {syncingAll ? <Spinner size={14} /> : <Icon name="history" size={15} />} Sync everything now
        </button>
      </div>

      {/* KPI cards with 7-day deltas */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          {[
            ['Active rules', 'active_rules', 'bolt'], ['DMs sent', 'dms_sent', 'ext'],
            ['Comment replies', 'replies_sent', 'quote'], ['Conversations', 'conversations', 'spark'],
            ['Comments received', 'comments', 'news'],
          ].map(([label, key, icon]) => {
            const d = summary.deltas?.[key] || {}; const dp = d.delta_pct;
            return (
              <div key={key} className="panel p-3.5">
                <div className="flex items-center justify-between">
                  <Icon name={icon} size={15} />
                  {dp != null && <span className="text-xs font-mono" style={{ color: dp >= 0 ? 'var(--ok)' : 'var(--danger)' }}>{dp >= 0 ? '▲' : '▼'} {Math.abs(dp)}%</span>}
                </div>
                <div className="font-display text-2xl mt-1.5" style={{ fontWeight: 600 }}>{summary[key] ?? 0}</div>
                <div className="eyebrow" style={{ fontSize: '0.52rem' }}>{label}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* engagement overview: chart + top-performing posts */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-4 mb-5">
        <div className="panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-display text-base" style={{ fontWeight: 600 }}>Engagement overview</h3>
            <div className="flex gap-1">
              {[7, 30, 90].map((d) => (
                <button key={d} className={cx('btn btn-sm', chartDays === d ? 'btn-accent' : 'btn-ghost')} onClick={() => setChartDays(d)}>{d}d</button>
              ))}
            </div>
          </div>
          <div className="flex gap-3 flex-wrap mb-2">
            {SERIES.map((s) => (
              <span key={s.key} className="text-xs flex items-center gap-1.5" style={{ color: 'var(--muted)' }}>
                <span style={{ width: 10, height: 3, borderRadius: 2, background: s.color, display: 'inline-block' }} /> {s.label}
              </span>
            ))}
          </div>
          <EngChart data={chart} />
        </div>
        <div className="panel p-4">
          <h3 className="font-display text-base mb-3" style={{ fontWeight: 600 }}>Top posts</h3>
          {topPosts.length === 0 && <div className="text-sm py-6 text-center" style={{ color: 'var(--faint)' }}>No published posts yet.</div>}
          <div className="space-y-2">
            {topPosts.map((p) => (
              <div key={p.campaign_id} className="flex items-center gap-2.5">
                {p.cover && <img src={p.cover} alt="" style={{ width: 34, height: 34, borderRadius: 7, objectFit: 'cover' }} />}
                <div className="min-w-0 flex-1">
                  <div className="text-sm truncate" style={{ fontWeight: 600 }}>{p.label}{p.project_name ? ` · ${p.project_name}` : ''}</div>
                  <div className="text-xs font-mono" style={{ color: 'var(--faint)' }}>
                    💬 {p.comments} · 📩 {p.dms} · ↩ {p.replies} · 👁 {p.reach ?? 'N/A'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* tabs */}
      <div className="flex gap-1 mb-5 flex-wrap">
        {[['automations', 'Automations'], ['posts', 'Posts'], ['inbox', 'DM Inbox'], ['comments', 'Comments'], ['leads', 'Leads'], ['activity', 'Activity']].map(([id, label]) => (
          <button key={id} className={cx('btn btn-sm', tab === id ? 'btn-accent' : 'btn-ghost')} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {tab === 'automations' && (<>
      {/* rule builder */}
      {form && (
        <div className="panel p-5 mb-5">
          <div className="eyebrow mb-4" style={{ color: 'var(--accent)' }}>{form.id ? 'Edit' : 'Create'} automation</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Name"><input className="input" value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Contact Request" /></Field>
            <Field label="Scope" hint="Account-wide applies to every post; or target one published post.">
              <select className="select" value={form.post_id} onChange={(e) => set('post_id', e.target.value)}>
                <option value="">Entire account (account-wide)</option>
                {scopePosts.filter((p) => p.ig_media_id).map((p) => (
                  <option key={p.campaign_id} value={p.ig_media_id}>{p.label}{p.project_name ? ` · ${p.project_name}` : ''}</option>
                ))}
              </select></Field>
            <Field label="When (trigger)" hint="Tick both to run this rule on comments AND DMs.">
              <div className="flex items-center gap-4" style={{ minHeight: 42 }}>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.on_comment} onChange={(e) => set('on_comment', e.target.checked)} /> Comment received</label>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.on_dm} onChange={(e) => set('on_dm', e.target.checked)} /> DM received</label>
              </div></Field>
            <Field label="Condition">
              <select className="select" value={form.operator} onChange={(e) => set('operator', e.target.value)}>
                {OPERATORS.map((o) => <option key={o} value={o}>{OP_LABEL[o] || hum(o)}</option>)}</select></Field>
            <Field label="Keywords" hint={form.operator === 'any' ? 'Not needed — this rule fires on EVERY comment/DM.' : 'Comma-separated. Case-insensitive by default.'}>
              <input className="input" value={form.keywords} disabled={form.operator === 'any'} onChange={(e) => set('keywords', e.target.value)}
                placeholder={form.operator === 'any' ? 'Matches any message' : 'CONTACT, PRICE, INFO'} /></Field>
          </div>
          {/* shared property auto-fill source */}
          <div className="panel p-3 mt-4 mb-3" style={{ background: 'var(--panel-2)' }}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="label" style={{ margin: 0 }}>Auto-fill source</span>
              <select className="select" style={{ minWidth: 190 }} value={propId} onChange={(e) => setPropId(e.target.value)}>
                {props.length === 0 && <option value="">No property yet</option>}
                {props.map((p) => <option key={p.id} value={String(p.id)}>{p.project_name || p.name || p.id}</option>)}
              </select>
              <span className="text-xs font-mono" style={{ color: 'var(--faint)' }}>Fills any DM/reply below with the property's facilities + map + phones.</span>
            </div>
          </div>

          {/* multiple actions per rule — e.g. public reply + DM + mark lead */}
          <div className="eyebrow mb-2">Then — actions ({form.actions.length})</div>
          {/* quick multi-select: tick any/all action types this rule should run */}
          <div className="flex gap-2 flex-wrap mb-3">
            {ACTIONS.map((t) => (
              <button key={t} type="button" className={cx('btn btn-sm', hasAction(t) ? 'btn-accent' : 'btn-ghost')} onClick={() => toggleActionType(t)}>
                {hasAction(t) ? '✓ ' : '+ '}{hum(t)}
              </button>
            ))}
            <button type="button" className="btn btn-sm btn-ghost" title="Add every action" onClick={() => setForm((f) => ({ ...f, actions: ACTIONS.map((t) => f.actions.find((a) => a.type === t) || { type: t, message: '', ai: false }) }))}>⚡ All</button>
          </div>
          <div className="space-y-3">
            {form.actions.map((a, i) => (
              <div key={i} className="panel p-3" style={{ background: 'var(--panel-2)' }}>
                <div className="flex items-center gap-2">
                  <span className="chip">{i + 1}</span>
                  <select className="select" style={{ maxWidth: 230 }} value={a.type} onChange={(e) => setAction(i, 'type', e.target.value)}>
                    {ACTIONS.map((t) => <option key={t} value={t}>{hum(t)}</option>)}
                  </select>
                  {form.actions.length > 1 && <button type="button" className="btn btn-sm btn-danger ml-auto" onClick={() => removeAction(i)}><Icon name="trash" size={12} /></button>}
                </div>
                {NEEDS_MSG(a.type) && (
                  <div className="mt-2">
                    <div className="flex gap-2 mb-1 flex-wrap items-center">
                      <button type="button" className="btn btn-sm btn-ghost" disabled={filling === `${i}:dm` || !propId} onClick={() => fillTemplate(i, 'dm')}>
                        {filling === `${i}:dm` ? <Spinner size={12} /> : <Icon name="ext" size={12} />} Fill DM
                      </button>
                      <button type="button" className="btn btn-sm btn-ghost" disabled={filling === `${i}:reply` || !propId} onClick={() => fillTemplate(i, 'reply')}>
                        {filling === `${i}:reply` ? <Spinner size={12} /> : <Icon name="quote" size={12} />} Fill reply
                      </button>
                      <label className="flex items-center gap-1.5 text-sm ml-auto" title="Rephrase with AI — grounded on the text, never invents.">
                        <input type="checkbox" checked={a.ai} onChange={(e) => setAction(i, 'ai', e.target.checked)} /> ✨ AI
                      </label>
                    </div>
                    <textarea className="input" rows={a.type === 'REPLY_TO_COMMENT' ? 3 : 7} value={a.message}
                      onChange={(e) => setAction(i, 'message', e.target.value)} style={{ resize: 'vertical' }}
                      placeholder={a.type === 'REPLY_TO_COMMENT' ? 'Thanks {{username}}! Check your DM 📩' : 'Hi {{username}}! Details: …'} />
                    <div className="text-xs font-mono mt-1" style={{ color: (a.message?.length || 0) > 1000 ? 'var(--danger)' : 'var(--faint)' }}>
                      {a.message?.length || 0} / 1000 · supports {'{{username}}'}
                    </div>
                  </div>
                )}
                {a.type === 'ADD_TAG' && (
                  <input className="input mt-2" value={a.message} onChange={(e) => setAction(i, 'message', e.target.value)} placeholder="Tag name (e.g. Hot lead)" />
                )}
                {a.type === 'MARK_LEAD' && <div className="text-xs font-mono mt-2" style={{ color: 'var(--faint)' }}>Marks the conversation as a Potential Lead.</div>}
              </div>
            ))}
          </div>
          <button type="button" className="btn btn-sm btn-ghost mt-2" onClick={addAction}><Icon name="plus" size={13} /> Add action</button>

          <div className="flex items-center gap-4 mt-3 flex-wrap">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.case_sensitive} onChange={(e) => set('case_sensitive', e.target.checked)} /> Case sensitive</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} /> Enabled</label>
          </div>
          <div className="flex gap-2 mt-5">
            <button className="btn btn-accent btn-sm" disabled={busy} onClick={save}>{busy ? <Spinner size={14} /> : <Icon name="check" size={14} />} Save automation</button>
            <button className="btn btn-sm btn-ghost" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </div>
      )}

      {/* automations list */}
      <div className="eyebrow mb-2">Automations ({rules.length})</div>
      <div className="space-y-3 mb-6">
        {rules.length === 0 && <div className="panel p-8 text-center text-sm" style={{ color: 'var(--muted)' }}>No automations yet. Create one to auto-reply to comments or send DMs.</div>}
        {rules.map((r) => {
          const st = statFor(r.id);
          const total = st.executions || 0;
          const rate = total ? Math.round((st.success || 0) / total * 100) : null;
          return (
            <div key={r.id} className="panel p-4 flex items-start gap-4">
              <span style={{ width: 9, height: 9, borderRadius: 99, marginTop: 6, background: r.enabled ? 'var(--ok)' : 'var(--faint)' }} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold">{r.name}</span>
                  <span className="chip">{r.conditions?.[0]?.operator === 'any' ? 'Any message' : `${hum(r.conditions?.[0]?.operator)}: ${(r.conditions?.[0]?.keywords || []).join(', ')}`}</span>
                  {(r.actions || []).map((a, i) => <span key={i} className="chip">→ {hum(a.type)}</span>)}
                  <span className="chip">{r.post_id ? (scopePosts.find((p) => p.ig_media_id === r.post_id)?.label || 'post-specific') : 'account-wide'}</span>
                </div>
                <div className="text-xs font-mono mt-1" style={{ color: 'var(--faint)' }}>
                  {(r.trigger_type === 'BOTH' || r.trigger_type === 'ANY') ? 'Comment + DM' : hum(r.trigger_type)} · {total} executions{rate != null ? ` · ${rate}% success` : ''} · {st.skipped || 0} skipped · {st.duplicate || 0} duplicate
                </div>
              </div>
              <div className="flex gap-2">
                <button className="btn btn-sm btn-ghost" onClick={() => toggle(r)}>{r.enabled ? 'Disable' : 'Enable'}</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setForm(fromRule(r))}><Icon name="edit" size={13} /></button>
                <button className="btn btn-sm btn-danger" onClick={() => remove(r)}><Icon name="trash" size={13} /></button>
              </div>
            </div>
          );
        })}
      </div>

      {/* simulate / test */}
      <div className="panel p-5">
        <div className="flex items-center gap-2.5 mb-3">
          <Icon name="bolt" size={18} /><h3 className="font-display text-lg" style={{ fontWeight: 600 }}>Test a comment / DM</h3>
        </div>
        <p className="text-sm mb-3" style={{ color: 'var(--muted)' }}>Preview exactly which rules fire and what would be sent — no posting, no side-effects.</p>
        <div className="grid grid-cols-1 md:grid-cols-[160px_1fr_auto] gap-3 items-end">
          <Field label="As">
            <select className="select" value={sim.trigger_type} onChange={(e) => setSim({ ...sim, trigger_type: e.target.value })}>
              {TRIGGERS.map((t) => <option key={t} value={t}>{hum(t)}</option>)}</select></Field>
          <Field label="Text"><input className="input" value={sim.text} onChange={(e) => setSim({ ...sim, text: e.target.value })} placeholder="CONTACT" /></Field>
          <button className="btn btn-accent h-[42px]" onClick={runSim}><Icon name="bolt" size={15} /> Simulate</button>
        </div>
        {simOut && (
          <div className="mt-4">
            <div className="text-sm mb-2">Matched rules: <b>{simOut.matched_rules}</b></div>
            {(simOut.executions || []).map((e, i) => (
              <div key={i} className="panel p-3 mb-2" style={{ background: 'var(--panel-2)' }}>
                <div className="text-sm"><b>{e.rule}</b> → {hum(e.action)} <span className="chip ml-1">{e.status}</span>{e.ai && <span className="chip ml-1">✨ AI</span>}</div>
                {e.message && <div className="evidence mt-1">{e.message}</div>}
                {e.error && <div className="pill pill-warn mt-1">{e.error}</div>}
              </div>
            ))}
            {simOut.matched_rules === 0 && <div className="text-sm" style={{ color: 'var(--faint)' }}>No rules matched this text.</div>}
          </div>
        )}
      </div>
      </>)}

      {/* Posts — per-post engagement tracked by Post #N */}
      {tab === 'posts' && !postDetail && (
        <div className="space-y-3">
          {posts.length === 0 && (
            <div className="panel p-8 text-center text-sm" style={{ color: 'var(--muted)' }}>
              No published posts yet. Publish a carousel from the Business panel — it'll appear here as “Post #N”, and you can track its comments &amp; insights.
            </div>
          )}
          {posts.map((p) => (
            <button key={p.campaign_id} className="panel p-4 flex items-center gap-4 w-full text-left" style={{ cursor: 'pointer' }} onClick={() => openPost(p.campaign_id)}>
              {p.cover && <img src={p.cover} alt="" style={{ width: 52, height: 52, borderRadius: 10, objectFit: 'cover' }} />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold">{p.label}</span>
                  {p.project_name && <span className="chip">{p.project_name}</span>}
                  {p.angle && <span className="chip">{hum(p.angle)}</span>}
                  {!p.ig_media_id && <span className="pill pill-warn">not on IG</span>}
                </div>
                <div className="text-xs font-mono mt-1" style={{ color: 'var(--faint)' }}>
                  {p.comment_count} comments · {p.dms_sent} DMs sent · {p.replies_sent} replies
                </div>
              </div>
              <Icon name="ext" size={15} />
            </button>
          ))}
        </div>
      )}

      {/* Post detail */}
      {tab === 'posts' && postDetail && (
        <div>
          <button className="btn btn-sm btn-ghost mb-4" onClick={() => setPostDetail(null)}><Icon name="x" size={13} /> Back to posts</button>
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <h3 className="font-display text-2xl" style={{ fontWeight: 600 }}>{postDetail.label}</h3>
            {postDetail.permalink && <a className="btn btn-sm btn-ghost" href={postDetail.permalink} target="_blank" rel="noreferrer"><Icon name="ext" size={13} /> View on Instagram</a>}
            {postDetail.ig_media_id && <button className="btn btn-sm btn-ghost" disabled={syncing} onClick={() => syncPost(postDetail.campaign_id, false)}>{syncing ? <Spinner size={13} /> : <Icon name="history" size={13} />} Sync from Instagram</button>}
            {postDetail.ig_media_id && <button className="btn btn-sm btn-accent" disabled={syncing} onClick={() => syncPost(postDetail.campaign_id, true)} title="Pull comments and run your automations against any new ones">{syncing ? <Spinner size={13} /> : <Icon name="bolt" size={13} />} Sync &amp; run rules</button>}
          </div>
          {loadingDetail && <div className="panel p-6 text-center"><Spinner size={18} /></div>}
          {!loadingDetail && (<>
            {(postDetail.warnings || []).map((w, i) => <div key={i} className="pill pill-warn mb-2">{w}</div>)}
            {/* insights */}
            {postDetail.insights && Object.keys(postDetail.insights).length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
                {Object.entries(postDetail.insights).map(([k, v]) => (
                  <div key={k} className="panel p-3 text-center">
                    <div className="font-display text-2xl" style={{ fontWeight: 600 }}>{v ?? '—'}</div>
                    <div className="eyebrow" style={{ fontSize: '0.55rem' }}>{hum(k)}</div>
                  </div>
                ))}
              </div>
            )}
            {/* automation activity on this post */}
            <div className="eyebrow mb-2">Automation on this post</div>
            <div className="panel p-4 mb-5">
              {(postDetail.activity?.by_action || []).length === 0 && <div className="text-sm" style={{ color: 'var(--faint)' }}>No automation actions yet.</div>}
              <div className="flex gap-2 flex-wrap">
                {(postDetail.activity?.by_action || []).map((a, i) => (
                  <span key={i} className="chip">{hum(a.action_type)} · {a.status} · {a.n}</span>
                ))}
              </div>
            </div>
            {/* comments */}
            <div className="eyebrow mb-2">Comments ({(postDetail.live_comments?.length || 0) + (postDetail.stored_comments?.length || 0)})</div>
            <div className="space-y-2">
              {(postDetail.live_comments || []).length === 0 && (postDetail.stored_comments || []).length === 0 && (
                <div className="panel p-6 text-center text-sm" style={{ color: 'var(--muted)' }}>No comments captured. Live comments pull from Instagram when a valid token is set.</div>
              )}
              {(postDetail.live_comments || []).map((c, i) => (
                <div key={`l${i}`} className="panel p-3">
                  <div className="flex items-center gap-2"><span className="font-semibold">@{c.username || 'user'}</span>
                    {c.like_count != null && <span className="chip">{c.like_count} likes</span>}
                    <span className="chip">live</span></div>
                  <div className="text-sm mt-1">{c.text}</div>
                </div>
              ))}
              {(postDetail.stored_comments || []).map((c) => (
                <div key={`s${c.id}`} className="panel p-3">
                  <div className="flex items-center gap-2"><span className="font-semibold">@{c.username || 'user'}</span>
                    {c.reply_status && <span className="chip">{hum(c.reply_status)}</span>}</div>
                  <div className="text-sm mt-1">{c.text}</div>
                </div>
              ))}
            </div>
          </>)}
        </div>
      )}

      {/* DM Inbox — two-pane: list + thread + composer */}
      {tab === 'inbox' && (
        <div className="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-4">
          <div className="space-y-2">
            {convos.length === 0 && (
              <div className="panel p-6 text-center text-sm" style={{ color: 'var(--muted)' }}>No conversations yet. DM threads appear here after a Sync (or when webhooks deliver).</div>
            )}
            {convos.map((c) => (
              <button key={c.id} className={cx('panel p-3 flex items-start gap-2.5 w-full text-left', convOpen?.id === c.id && 'active')} style={{ cursor: 'pointer', borderColor: convOpen?.id === c.id ? 'var(--accent)' : undefined }} onClick={() => openConversation(c)}>
                <div className="w-8 h-8 rounded-full grid place-items-center shrink-0" style={{ background: 'var(--panel-2)' }}><Icon name="quote" size={14} /></div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5"><span className="font-semibold text-sm truncate">@{c.ig_user_ref || 'user'}</span>{c.status && <span className="chip">{c.status}</span>}</div>
                  <div className="text-xs truncate" style={{ color: 'var(--muted)' }}>{c.last_text || '—'}</div>
                </div>
              </button>
            ))}
          </div>
          <div className="panel p-4">
            {!convOpen && <div className="text-sm py-10 text-center" style={{ color: 'var(--faint)' }}>Select a conversation to view the thread.</div>}
            {convOpen && (<>
              <div className="flex items-center justify-between mb-3">
                <div className="font-semibold">@{convOpen.ig_user_ref || 'user'}</div>
                <div className="flex gap-1">
                  {['OPEN', 'CLOSED'].map((s) => <button key={s} className={cx('btn btn-sm', convOpen.status === s ? 'btn-accent' : 'btn-ghost')} onClick={async () => { await api.engConvStatus(accountId, convOpen.id, s); setConvOpen({ ...convOpen, status: s }); }}>{s}</button>)}
                </div>
              </div>
              <div className="space-y-2 mb-3" style={{ maxHeight: 360, overflowY: 'auto' }}>
                {convMsgs.length === 0 && <div className="text-sm" style={{ color: 'var(--faint)' }}>No messages loaded.</div>}
                {convMsgs.map((m, i) => (
                  <div key={i} className={cx('text-sm px-3 py-2 rounded-xl', m.direction === 'out' ? 'ml-auto' : '')} style={{ maxWidth: '80%', background: m.direction === 'out' ? 'var(--accent)' : 'var(--panel-2)', color: m.direction === 'out' ? '#0b0b0b' : 'var(--text)', width: 'fit-content' }}>
                    {m.text}
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <input className="input flex-1" value={msgDraft} onChange={(e) => setMsgDraft(e.target.value)} placeholder="Type a message…" onKeyDown={(e) => e.key === 'Enter' && sendMsg()} />
                <button className="btn btn-accent" onClick={sendMsg}><Icon name="ext" size={15} /></button>
              </div>
              <p className="text-xs font-mono mt-1" style={{ color: 'var(--faint)' }}>Sends via Graph (24h messaging window applies).</p>
            </>)}
          </div>
        </div>
      )}

      {/* Comments — filters + reply / hide / mark read */}
      {tab === 'comments' && (
        <div>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            {['all', 'unread', 'replied', 'unreplied', 'hidden'].map((f) => (
              <button key={f} className={cx('btn btn-sm', commentFilter === f ? 'btn-accent' : 'btn-ghost')} onClick={() => setCommentFilter(f)}>{hum(f)}</button>
            ))}
            <input className="input" style={{ maxWidth: 220 }} value={commentSearch} onChange={(e) => setCommentSearch(e.target.value)} placeholder="Search comments…" />
          </div>
          <div className="space-y-3">
            {(() => {
              const filtered = comments.filter((c) => {
                if (commentSearch && !(c.text || '').toLowerCase().includes(commentSearch.toLowerCase())) return false;
                if (commentFilter === 'unread') return !c.read_at;
                if (commentFilter === 'replied') return c.reply_status === 'REPLIED';
                if (commentFilter === 'unreplied') return c.reply_status !== 'REPLIED' && c.reply_status !== 'HIDDEN';
                if (commentFilter === 'hidden') return c.reply_status === 'HIDDEN';
                return true;
              });
              if (filtered.length === 0) return <div className="panel p-8 text-center text-sm" style={{ color: 'var(--muted)' }}>No comments match. Incoming comments appear here after a Sync or webhook.</div>;
              return filtered.map((c) => (
                <div key={c.id} className="panel p-4">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold">@{c.username || 'user'}</span>
                    {c.reply_status && <span className="chip">{hum(c.reply_status)}</span>}
                    {c.automation_status && <span className="chip">{hum(c.automation_status)}</span>}
                    {c.post_id && <span className="chip">{scopePosts.find((p) => p.ig_media_id === c.post_id)?.label || 'post'}</span>}
                    <span className="text-xs font-mono ml-auto" style={{ color: 'var(--faint)' }}>{c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
                  </div>
                  <div className="text-sm mt-1">{c.text}</div>
                  <div className="flex gap-2 mt-2 flex-wrap">
                    <button className="btn btn-sm btn-ghost" onClick={() => { setReplyFor(replyFor === c.id ? null : c.id); setReplyDraft(''); }}><Icon name="quote" size={12} /> Reply</button>
                    <button className="btn btn-sm btn-ghost" onClick={() => hideComment(c.id, c.reply_status !== 'HIDDEN')}>{c.reply_status === 'HIDDEN' ? 'Unhide' : 'Hide'}</button>
                    {!c.read_at && <button className="btn btn-sm btn-ghost" onClick={async () => { await api.engCommentRead(accountId, c.id); loadTab('comments'); }}>Mark read</button>}
                  </div>
                  {replyFor === c.id && (
                    <div className="flex gap-2 mt-2">
                      <input className="input flex-1" value={replyDraft} onChange={(e) => setReplyDraft(e.target.value)} placeholder="Public reply…" onKeyDown={(e) => e.key === 'Enter' && doReply(c.id)} />
                      <button className="btn btn-accent btn-sm" onClick={() => doReply(c.id)}>Send</button>
                    </div>
                  )}
                </div>
              ));
            })()}
          </div>
        </div>
      )}

      {/* Leads — pipeline */}
      {tab === 'leads' && (
        <div>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <button className={cx('btn btn-sm', !leadFilter ? 'btn-accent' : 'btn-ghost')} onClick={() => setLeadFilter('')}>All ({Object.values(leads.stats || {}).reduce((a, b) => a + b, 0)})</button>
            {(leads.statuses || []).map((s) => (
              <button key={s} className={cx('btn btn-sm', leadFilter === s ? 'btn-accent' : 'btn-ghost')} onClick={() => setLeadFilter(s)}>{hum(s)} ({leads.stats?.[s] || 0})</button>
            ))}
          </div>
          <div className="space-y-2">
            {(leads.leads || []).length === 0 && <div className="panel p-8 text-center text-sm" style={{ color: 'var(--muted)' }}>No leads yet. A MARK_LEAD action (e.g. on CONTACT/PRICE) captures leads here automatically.</div>}
            {(leads.leads || []).map((l) => (
              <div key={l.id} className="panel p-4 flex items-center gap-3 flex-wrap">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><span className="font-semibold">@{l.username || 'lead'}</span><span className="chip">{l.label}</span>
                    {l.source_post_id && <span className="chip">{scopePosts.find((p) => p.ig_media_id === l.source_post_id)?.label || 'post'}</span>}</div>
                  <div className="text-xs font-mono mt-0.5" style={{ color: 'var(--faint)' }}>Created {l.created_at ? new Date(l.created_at).toLocaleDateString() : ''}</div>
                </div>
                <select className="select" style={{ maxWidth: 170 }} value={l.status} onChange={(e) => setLeadStatus(l.id, e.target.value)}>
                  {(leads.statuses || []).map((s) => <option key={s} value={s}>{hum(s)}</option>)}
                </select>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Activity feed */}
      {tab === 'activity' && (
        <div className="space-y-2">
          {activity.length === 0 && (
            <div className="panel p-8 text-center text-sm" style={{ color: 'var(--muted)' }}>
              No activity yet. Every DM/reply an automation performs is logged here (real sends when live, dry-run entries otherwise).
            </div>
          )}
          {activity.map((a) => (
            <div key={a.id} className="panel p-3 flex items-start gap-3">
              <span className="chip" style={{ background: a.status === 'SUCCESS' ? 'var(--ok)' : a.status === 'FAILED' ? 'var(--danger)' : 'var(--panel-2)', color: a.status === 'SUCCESS' || a.status === 'FAILED' ? '#0b0b0b' : 'var(--muted)' }}>{a.status}</span>
              <div className="min-w-0 flex-1">
                <div className="text-sm"><b>{a.rule_name || 'Rule'}</b> · {hum(a.action_type)}{a.username ? ` → @${a.username}` : ''}</div>
                {a.inbound_text && <div className="text-xs mt-0.5" style={{ color: 'var(--faint)' }}>“{a.inbound_text}”</div>}
                {a.error_message && <div className="pill pill-warn mt-1">{a.error_message}</div>}
              </div>
              <span className="text-xs font-mono" style={{ color: 'var(--faint)' }}>{a.executed_at ? new Date(a.executed_at).toLocaleString() : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
