import { useCallback, useEffect, useState } from 'react';
import api, { TOKEN_KEY, REFRESH_KEY } from './services/api';
import { Icon, Toast, Spinner, cx } from './components/ui';
import Studio from './components/Studio';
import Settings from './components/Settings';
import History from './components/History';
import Business from './components/Business';
import Login from './components/Login';
import Dashboard from './components/Dashboard';
import Properties from './components/Properties';
import Analytics from './components/Analytics';
import MediaLibrary from './components/MediaLibrary';
import Templates from './components/Templates';
import Integrations from './components/Integrations';
import AdminSettings from './components/AdminSettings';
import Leads from './components/Leads';
import Calendar from './components/Calendar';
import CustomPoster from './components/CustomPoster';
import Engagement from './components/Engagement';
import Placeholder from './components/Placeholder';
import BusinessSK from './components/BusinessSK';

// Two business workspaces, each a collapsible dropdown group in the sidebar.
//   Business-JK → the real-estate Instagram platform (all existing panels)
//   Business-SK → the affiliate business (frontend built later; API is live)
const NAV_GROUPS = [
  {
    id: 'jk', label: 'Business-JK', icon: 'building',
    items: [
      { id: 'dashboard', label: 'Dashboard', icon: 'studio' },
      { id: 'business', label: 'Business', icon: 'building' },
      { id: 'engagement', label: 'Engagement', icon: 'bolt' },
      { id: 'custom', label: 'Custom Poster', icon: 'edit' },
      { id: 'properties', label: 'Properties', icon: 'doc' },
      { id: 'media', label: 'Media Library', icon: 'quote' },
      { id: 'templates', label: 'Templates', icon: 'edit' },
      { id: 'analytics', label: 'Analytics', icon: 'history' },
      { id: 'leads', label: 'Leads', icon: 'bolt' },
      { id: 'calendar', label: 'Calendar', icon: 'news' },
      { id: 'integrations', label: 'Integrations', icon: 'ext' },
      { id: 'admin', label: 'Admin', icon: 'shield' },
    ],
  },
  {
    id: 'sk', label: 'Business-SK', icon: 'spark',
    sections: [
      { label: 'Workspace', items: [
        { id: 'sk-affiliate', label: 'Affiliate', icon: 'spark' },
        { id: 'sk-post', label: 'Content Studio', icon: 'pin' },
        { id: 'sk-engagement', label: 'Engagement', icon: 'bolt' },
        { id: 'sk-storefront', label: 'Storefront', icon: 'ext' },
      ] },
      { label: 'Insights', items: [
        { id: 'sk-overview', label: 'Overview', icon: 'studio' },
        { id: 'sk-winners', label: 'Winners', icon: 'spark' },
        { id: 'sk-trends', label: 'Trends', icon: 'bolt' },
        { id: 'sk-intelligence', label: 'Intelligence', icon: 'doc' },
        { id: 'sk-calendar', label: 'Content Calendar', icon: 'news' },
        { id: 'sk-revenue', label: 'Revenue', icon: 'history' },
      ] },
      { label: 'Affiliate settings', items: [
        { id: 'sk-agents', label: 'Agents', icon: 'settings' },
        { id: 'sk-accounts', label: 'Accounts', icon: 'shield' },
        { id: 'sk-history', label: 'History', icon: 'history' },
      ] },
    ],
  },
];

// Flatten a group's items whether it uses flat `items` or nested `sections`.
const groupItems = (g) => g.items || (g.sections || []).flatMap((s) => s.items);

// Universal panels — shared across BOTH businesses (account tokens, API keys,
// personal studio). They live OUTSIDE the Business-JK / Business-SK groups.
const UNIVERSAL = [
  { id: 'accounts', label: 'Accounts', icon: 'spark' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
  { id: 'studio', label: 'Personal', icon: 'quote' },
];

// Flat list (used by the compact mobile bar).
const NAV = [...NAV_GROUPS.flatMap(groupItems), ...UNIVERSAL];

export default function App() {
  const [view, setView] = useState('dashboard');
  const [openGroups, setOpenGroups] = useState({ jk: true, sk: true });
  const toggleGroup = (id) => setOpenGroups((g) => ({ ...g, [id]: !g[id] }));
  const [accounts, setAccounts] = useState([]);
  const [settings, setSettings] = useState(null);
  const [health, setHealth] = useState(null);
  const [toast, setToast] = useState(null);
  const [generating, setGenerating] = useState(false);   // campaign generation in progress
  // auth: null = checking, false = login screen, true = authenticated
  const [authed, setAuthed] = useState(localStorage.getItem(TOKEN_KEY) ? null : false);

  const notify = useCallback((text, type = 'ok') => {
    setToast({ text, type });
    setTimeout(() => setToast(null), 3200);
  }, []);

  const reload = useCallback(async () => {
    const [acc, set] = await Promise.all([api.listAccounts(), api.getSettings()]);
    setAccounts(acc.accounts);
    setSettings(set);
  }, []);

  // Validate an existing token on mount before showing the app.
  useEffect(() => {
    if (localStorage.getItem(TOKEN_KEY)) {
      api.adminMe().then(() => setAuthed(true)).catch(() => setAuthed(false));
    }
  }, []);

  // Load data only once authenticated.
  useEffect(() => {
    if (authed !== true) return;
    reload().catch(() => notify('Cannot reach backend on :8000', 'error'));
    api.health().then(setHealth).catch(() => {});
  }, [authed, reload, notify]);

  const logout = async () => {
    await api.adminLogout();
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    setAuthed(false);
  };

  if (authed === null) {
    return <div className="min-h-screen grid place-items-center" style={{ position: 'relative', zIndex: 2, color: 'var(--muted)' }}>Loading…</div>;
  }
  if (authed === false) {
    return <Login onAuthed={() => setAuthed(true)} />;
  }

  return (
    <div className="min-h-screen flex">
      {/* nav rail */}
      <aside className="hidden md:flex flex-col w-64 shrink-0 p-5 sticky top-0 h-screen"
        style={{ borderRight: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2.5 px-1 mb-9">
          <div className="w-9 h-9 rounded-xl grid place-items-center"
            style={{ background: 'linear-gradient(135deg, var(--amber-2), var(--amber))', color: '#1a1206' }}>
            <Icon name="spark" size={20} />
          </div>
          <div>
            <div className="font-display text-xl leading-none" style={{ fontWeight: 600 }}>Studio</div>
            <div className="eyebrow" style={{ fontSize: '0.56rem' }}>Instagram autopilot</div>
          </div>
        </div>

        <nav className="space-y-2 flex-1 overflow-y-auto">
          {NAV_GROUPS.map((group) => (
            <div key={group.id}>
              <div onClick={() => toggleGroup(group.id)}
                style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '9px 10px',
                         cursor: 'pointer', userSelect: 'none', borderRadius: 10, fontWeight: 600,
                         letterSpacing: '.01em', color: 'var(--text)' }}>
                <Icon name={group.icon} size={18} />
                <span style={{ flex: 1 }}>{group.label}</span>
                <span style={{ display: 'inline-flex', color: 'var(--muted)', transition: 'transform .15s',
                               transform: openGroups[group.id] ? 'rotate(90deg)' : 'none' }}>
                  <Icon name="chevR" size={14} />
                </span>
              </div>
              {openGroups[group.id] && (
                <div className="space-y-1 mt-1" style={{ marginLeft: 11, paddingLeft: 9,
                     borderLeft: '1px solid var(--border)' }}>
                  {(group.sections || [{ items: group.items }]).map((sec, si) => (
                    <div key={sec.label || si} className={si > 0 ? 'mt-2' : ''}>
                      {sec.label && (
                        <div className="eyebrow" style={{ padding: '4px 10px 2px', fontSize: '0.52rem', color: 'var(--faint)' }}>{sec.label}</div>
                      )}
                      {sec.items.map((n) => (
                        <div key={n.id} className={cx('nav-item', view === n.id && 'active')}
                          onClick={() => setView(n.id)}>
                          <Icon name={n.icon} size={16} /> {n.label}
                          <span className="nav-dot" />
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

          {/* Universal — shared across both businesses (tokens / API / personal) */}
          <div className="space-y-1" style={{ marginTop: 12, paddingTop: 12,
               borderTop: '1px solid var(--border)' }}>
            <div className="eyebrow" style={{ padding: '0 10px 4px', fontSize: '0.52rem',
                 color: 'var(--faint)' }}>Universal</div>
            {UNIVERSAL.map((n) => (
              <div key={n.id} className={cx('nav-item', view === n.id && 'active')}
                onClick={() => setView(n.id)}>
                <Icon name={n.icon} size={18} /> {n.label}
                <span className="nav-dot" />
              </div>
            ))}
          </div>
        </nav>

        <div className="panel p-3.5 mt-4 text-xs space-y-2 font-mono" style={{ color: 'var(--muted)' }}>
          <div className="flex items-center gap-2">
            <span className={health?.openai_key_set ? 'live-dot' : ''}
              style={!health?.openai_key_set ? { width: 7, height: 7, borderRadius: 99, background: 'var(--danger)' } : {}} />
            {health?.openai_key_set ? 'OpenAI connected' : 'OpenAI key missing'}
          </div>
          <div style={{ color: 'var(--faint)' }}>{health?.model || 'gpt-4o-mini'}</div>
          <div style={{ color: 'var(--faint)' }}>{accounts.length} account{accounts.length !== 1 ? 's' : ''} linked</div>
        </div>
        <button className="btn btn-sm btn-ghost mt-3 justify-center" onClick={logout}>
          <Icon name="x" size={14} /> Sign out
        </button>
      </aside>

      {/* mobile top bar */}
      <div className="md:hidden fixed top-0 inset-x-0 z-40 flex gap-1 p-2"
        style={{ background: 'var(--bg-2)', borderBottom: '1px solid var(--border)' }}>
        {NAV.map((n) => (
          <button key={n.id} className={cx('nav-item flex-1 justify-center', view === n.id && 'active')} onClick={() => setView(n.id)}>
            <Icon name={n.icon} size={18} />
          </button>
        ))}
      </div>

      {/* main */}
      <main className="flex-1 min-w-0 px-5 md:px-10 py-8 md:py-10 mt-14 md:mt-0">
        <div className="max-w-6xl mx-auto">
          {/* Business stays MOUNTED (hidden when inactive) so an in-progress
              extraction / generated campaign / blueprint edits persist when you
              switch panels and come back — "leave it where I left it". */}
          <div style={{ display: view === 'business' ? 'block' : 'none' }}>
            <Business notify={notify} onGenerating={setGenerating} />
          </div>
          {view === 'dashboard' && <Dashboard notify={notify} go={setView} />}
          {view === 'engagement' && <Engagement notify={notify} />}
          {view === 'custom' && <CustomPoster notify={notify} />}
          {view === 'properties' && <Properties notify={notify} />}
          {view === 'media' && <MediaLibrary notify={notify} />}
          {view === 'templates' && <Templates notify={notify} />}
          {view === 'analytics' && <Analytics notify={notify} />}
          {view === 'leads' && <Leads notify={notify} />}
          {view === 'calendar' && <Calendar notify={notify} />}
          {view === 'integrations' && <Integrations notify={notify} />}
          {view === 'accounts' && <Settings accounts={accounts} settings={settings} reload={reload} notify={notify} />}
          {view === 'admin' && <AdminSettings notify={notify} />}
          {view === 'settings' && (
            <Placeholder
              eyebrow="Universal"
              title="Settings"
              icon="settings"
              lines={[
                'Shared settings that apply across Business-JK and Business-SK.',
                'Useful universal options will live here — added later.',
              ]}
            />
          )}
          {view === 'studio' && <Studio accounts={accounts} settings={settings} notify={notify} />}
          {view === 'history' && <History />}
          {/* Business-SK stays MOUNTED (hidden when inactive) so an in-progress generation /
              queued batch persists as you switch panels and come back. */}
          <div style={{ display: ['sk-overview', 'sk-affiliate', 'sk-winners', 'sk-trends', 'sk-intelligence', 'sk-calendar', 'sk-post', 'sk-storefront', 'sk-revenue', 'sk-agents', 'sk-accounts', 'sk-history'].includes(view) ? 'block' : 'none' }}>
            <BusinessSK notify={notify} accounts={accounts} view={view} onNavigate={setView} />
          </div>
          {view === 'sk-engagement' && <Engagement notify={notify} />}
        </div>
      </main>

      <Toast toast={toast} />

      {/* Global generation indicator — stays visible on every panel so an in-progress
          campaign is obviously still running when you switch away (it never stops). */}
      {generating && view !== 'business' && (
        <button onClick={() => setView('business')}
          className="fixed z-50 flex items-center gap-2.5 px-4 py-3 rounded-2xl"
          style={{ right: 20, bottom: 20, background: 'var(--panel-2)', border: '1px solid var(--accent)', color: 'var(--text)', cursor: 'pointer', boxShadow: '0 8px 30px rgba(0,0,0,.45)' }}>
          <Spinner size={15} />
          <span className="text-sm" style={{ fontWeight: 600 }}>Generating campaign…</span>
          <span className="text-xs" style={{ color: 'var(--faint)' }}>tap to view</span>
        </button>
      )}
    </div>
  );
}
