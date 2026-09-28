import { useEffect, useState } from 'react';
import api from '../services/api';
import { Field, Icon, Spinner, cx } from './ui';

const BLANK = { label: '', handle: '', niche: 'quotes', ig_business_id: '', ig_access_token: '', is_active: true };

// Opened from the Business-SK Android app, the launch URL carries the installed version
// (?skapp=1.4.0&skappc=5). Remember it: the SPA drops the query once it navigates.
const APP_KEY = 'sk_android_app';
try {
  const q = new URLSearchParams(window.location.search);
  if (q.get('skapp')) localStorage.setItem(APP_KEY, JSON.stringify({ name: q.get('skapp'), code: Number(q.get('skappc') || 0) }));
} catch { /* storage blocked — the card just shows the download link */ }
const installedApp = () => { try { return JSON.parse(localStorage.getItem(APP_KEY) || 'null'); } catch { return null; } };

function AndroidAppCard() {
  const [latest, setLatest] = useState(null);
  const [err, setErr] = useState(false);
  const inApp = /Android/i.test(navigator.userAgent || '') && installedApp();
  const mine = installedApp();
  const check = () => {
    setErr(false);
    fetch('/helper/version.json', { cache: 'no-store' }).then((r) => r.json()).then(setLatest).catch(() => setErr(true));
  };
  useEffect(check, []);
  const newer = latest && mine && latest.versionCode > (mine.code || 0);
  return (
    <div className="panel p-5 mb-8">
      <div className="flex items-center gap-2 mb-3">
        <Icon name="spark" size={16} style={{ color: 'var(--accent)' }} />
        <span className="eyebrow" style={{ color: 'var(--accent)' }}>Android app · Business-SK + SK Helper</span>
      </div>
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div><div className="font-display text-2xl">{inApp ? mine.name : '—'}</div><div className="text-xs" style={{ color: 'var(--faint)' }}>{inApp ? 'installed on this phone' : 'open from the app to see'}</div></div>
        <div><div className="font-display text-2xl">{latest ? latest.versionName : (err ? '?' : '…')}</div><div className="text-xs" style={{ color: 'var(--faint)' }}>latest</div></div>
      </div>
      {latest?.notes && <p className="text-xs mb-4" style={{ color: 'var(--muted)' }}>{latest.notes}</p>}
      {inApp ? (newer
        // SK Helper does the install (one APK = both icons): download, checksum, Android installer
        ? <a className="btn btn-accent" href="skhelper://update" style={{ display: 'inline-flex' }}><Icon name="check" size={15} /> Update to {latest.versionName}</a>
        : <div className="flex items-center gap-3 text-sm"><Icon name="check" size={15} style={{ color: 'var(--ok)' }} /> You're on the latest version
            <button className="btn btn-ghost btn-sm" onClick={check}>Check again</button></div>)
        : <div className="flex flex-wrap items-center gap-3 text-sm">
            <a className="btn btn-accent btn-sm" href="/helper/sk-helper.apk"><Icon name="plus" size={14} /> Download the app</a>
            <span style={{ color: 'var(--muted)' }}>On the phone: SK Helper → Settings → App updates.</span>
          </div>}
    </div>
  );
}

function AccountForm({ initial, onSave, onCancel, saving }) {
  const [f, setF] = useState(initial || BLANK);
  const editing = Boolean(initial?.id);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  return (
    <div className="panel p-5 mb-5" style={{ borderColor: 'var(--border-2)' }}>
      <div className="flex items-center gap-2 mb-4">
        <Icon name={editing ? 'edit' : 'plus'} size={16} style={{ color: 'var(--accent)' }} />
        <span className="eyebrow" style={{ color: 'var(--accent)' }}>{editing ? `Edit account #${initial.id}` : 'New account'}</span>
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Field label="Label"><input className="input" value={f.label} onChange={set('label')} placeholder="e.g. Daily Quotes" /></Field>
        <Field label="Handle (@username)" hint="Shown as the overlay on this account's slides">
          <input className="input font-mono" value={f.handle || ''} onChange={set('handle')} placeholder="sparkle06.exe" />
        </Field>
        <Field label="Niche" hint="Pick a preset or type your own (e.g. affiliate, fashion, tech)">
          <input className="input" list="niche-options" value={f.niche || ''} onChange={set('niche')} placeholder="e.g. affiliate" />
          <datalist id="niche-options">
            <option value="affiliate" />
            <option value="quotes" />
            <option value="news" />
            <option value="both" />
            <option value="fashion" />
            <option value="tech" />
            <option value="lifestyle" />
          </datalist>
        </Field>
        <Field label="Instagram Business ID"><input className="input font-mono" value={f.ig_business_id} onChange={set('ig_business_id')} placeholder="numeric IG business id" /></Field>
        <Field label="Access Token" hint={editing ? 'Leave blank to keep the current token' : 'Stored locally in the rags DB (git-ignored), never committed'}>
          <input className="input font-mono" type="password" value={f.ig_access_token} onChange={set('ig_access_token')} placeholder={editing ? '••••••••' : 'IGAA... long-lived token'} />
        </Field>
      </div>
      <div className="flex items-center gap-4 mt-4">
        <label className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: 'var(--muted)' }}>
          <input type="checkbox" checked={f.is_active} onChange={set('is_active')} /> Active
        </label>
        <div className="ml-auto flex gap-2">
          <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
          <button className="btn btn-accent btn-sm" disabled={saving || !f.label} onClick={() => onSave(f)}>
            {saving ? <Spinner size={14} /> : <Icon name="check" size={14} />} {editing ? 'Save' : 'Add account'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Settings({ accounts, settings, reload, notify }) {
  const [form, setForm] = useState(null); // null | BLANK | account
  const [saving, setSaving] = useState(false);
  const [keys, setKeys] = useState({});
  const [keysSaving, setKeysSaving] = useState(false);
  const [gh, setGh] = useState(null); // live GitHub repo-connection status

  const loadGh = () => api.bizGithubStatus().then(setGh).catch(() => setGh(null));
  useEffect(() => { loadGh(); }, []);

  useEffect(() => {
    if (settings) setKeys({
      news_api_key: '', github_username: settings.github_username || '', github_repo: settings.github_repo || '',
      github_branch: settings.github_branch || '', github_token: '', posts_per_batch: settings.posts_per_batch || 3,
      slides_per_post: settings.slides_per_post || 4, fixed_hashtags: settings.fixed_hashtags || '',
    });
  }, [settings]);

  const saveAccount = async (f) => {
    setSaving(true);
    try {
      if (f.id) await api.updateAccount(f.id, f);
      else await api.createAccount(f);
      setForm(null);
      await reload();
      notify(f.id ? 'Account updated' : 'Account added');
    } catch (e) {
      notify(e?.response?.data?.detail || 'Save failed', 'error');
    } finally { setSaving(false); }
  };

  const remove = async (a) => {
    if (!window.confirm(`Delete account "${a.label}"? This cannot be undone.`)) return;
    try { await api.deleteAccount(a.id); await reload(); notify('Account deleted'); }
    catch { notify('Delete failed', 'error'); }
  };

  const saveKeys = async () => {
    setKeysSaving(true);
    try {
      const body = { ...keys, posts_per_batch: Number(keys.posts_per_batch), slides_per_post: Number(keys.slides_per_post) };
      if (!body.news_api_key) delete body.news_api_key; // blank = keep existing
      if (!body.github_token) delete body.github_token; // blank = keep existing token
      await api.updateSettings(body);
      await reload();
      await loadGh();
      notify('Settings saved');
    } catch (e) { notify(e?.response?.data?.detail || 'Save failed', 'error'); }
    finally { setKeysSaving(false); }
  };

  return (
    <div className="fade-up max-w-4xl">
      <p className="eyebrow mb-2">The rags store</p>
      <h1 className="font-display text-4xl md:text-5xl mb-1" style={{ fontWeight: 600 }}>Settings & keys.</h1>
      <p className="text-sm mb-8" style={{ color: 'var(--muted)' }}>
        Accounts and the News key live in the local <span className="font-mono">rags</span> store. The OpenAI key stays in <span className="font-mono">.env</span>.
      </p>

      <AndroidAppCard />

      {/* ACCOUNTS */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-display text-2xl">Instagram accounts</h2>
        {!form && <button className="btn btn-accent btn-sm" onClick={() => setForm(BLANK)}><Icon name="plus" size={15} /> Add account</button>}
      </div>

      <div className="panel p-3.5 mb-5 flex items-start gap-2.5 text-xs" style={{ color: 'var(--muted)', borderColor: 'color-mix(in srgb, var(--ok) 30%, transparent)' }}>
        <span style={{ color: 'var(--ok)' }}>🔒</span>
        <span>Access tokens are <b style={{ color: 'var(--text)' }}>encrypted on disk</b> with a local key (<span className="font-mono">.ragskey</span>, git-ignored) and are <b style={{ color: 'var(--text)' }}>never sent to the browser</b> — the UI only ever sees a masked preview. The API binds to <span className="font-mono">localhost</span>, so only this machine can reach it.</span>
      </div>

      {form && <AccountForm initial={form.id ? form : null} onSave={saveAccount} onCancel={() => setForm(null)} saving={saving} />}

      <div className="space-y-3 mb-12">
        {accounts.length === 0 && !form && (
          <div className="panel p-8 text-center text-sm" style={{ color: 'var(--muted)' }}>
            No accounts yet. Add one to start publishing.
          </div>
        )}
        {accounts.map((a) => (
          <div key={a.id} className={cx('panel p-4 flex items-center gap-4', `accent-${a.niche === 'news' ? 'news' : 'quotes'}`)}>
            <div className="w-10 h-10 rounded-xl grid place-items-center shrink-0"
              style={{ background: 'color-mix(in srgb, var(--accent) 18%, transparent)', color: 'var(--accent)' }}>
              <Icon name={a.niche === 'news' ? 'news' : 'quote'} size={18} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-semibold truncate">{a.label}</span>
                {a.handle && <span className="chip font-mono">@{a.handle}</span>}
                <span className="chip">{a.niche}</span>
                {!a.is_active && <span className="chip" style={{ color: 'var(--danger)' }}>disabled</span>}
              </div>
              <div className="font-mono text-xs mt-0.5 flex items-center gap-1.5" style={{ color: 'var(--faint)' }}>
                id {a.ig_business_id || '—'} · token {a.has_token ? a.ig_access_token_masked : 'not set'}
                {a.has_token && <span title="Encrypted at rest, never sent to the browser" style={{ color: 'var(--ok)' }}>🔒</span>}
              </div>
            </div>
            <div className="ml-auto flex gap-2">
              <button className="btn btn-ghost btn-sm" onClick={() => setForm(a)}><Icon name="edit" size={14} /></button>
              <button className="btn btn-danger btn-sm" onClick={() => remove(a)}><Icon name="trash" size={14} /></button>
            </div>
          </div>
        ))}
      </div>

      {/* KEYS */}
      <h2 className="font-display text-2xl mb-4">Keys & hosting</h2>
      <div className="panel p-5 space-y-5">
        {/* OpenAI status */}
        <div className="flex items-center gap-3 rounded-xl p-3" style={{ background: '#0d0c0a', border: '1px solid var(--border)' }}>
          <span className={settings?.openai_key_set ? 'live-dot' : ''} style={!settings?.openai_key_set ? { width: 7, height: 7, borderRadius: 99, background: 'var(--danger)' } : {}} />
          <span className="text-sm">
            OpenAI key {settings?.openai_key_set ? <b style={{ color: 'var(--ok)' }}>detected</b> : <b style={{ color: 'var(--danger)' }}>missing</b>} in <span className="font-mono">.env</span>
          </span>
          <span className="chip ml-auto font-mono">{settings?.openai_model}</span>
        </div>

        <Field label="News API key" hint="Optional. Blank = Google-News RSS (free). Blank here keeps the existing key.">
          <input className="input font-mono" type="password" value={keys.news_api_key || ''}
            onChange={(e) => setKeys({ ...keys, news_api_key: e.target.value })}
            placeholder={settings?.news_api_key_set ? '•••••••• (set)' : 'not set — using free RSS'} />
        </Field>

        <Field label="Fixed / brand hashtags" hint="Appended to every caption. Space or comma separated, with or without #. Blank = use config.json.">
          <input className="input" value={keys.fixed_hashtags || ''}
            onChange={(e) => setKeys({ ...keys, fixed_hashtags: e.target.value })}
            placeholder="motivation dailyinspiration positivity mindset" />
        </Field>

        <div className="grid md:grid-cols-2 gap-4">
          <Field label="Default posts / batch"><input className="input" type="number" min="1" max="6" value={keys.posts_per_batch} onChange={(e) => setKeys({ ...keys, posts_per_batch: e.target.value })} /></Field>
          <Field label="Default slides / post"><input className="input" type="number" min="1" max="6" value={keys.slides_per_post} onChange={(e) => setKeys({ ...keys, slides_per_post: e.target.value })} /></Field>
        </div>

        <div className="flex justify-end">
          <button className="btn btn-accent btn-sm" onClick={saveKeys} disabled={keysSaving}>
            {keysSaving ? <Spinner size={14} /> : <Icon name="check" size={14} />} Save settings
          </button>
        </div>
      </div>

      {/* GITHUB HOSTING — separate section (image hosting for Instagram publishing) */}
      <h2 className="font-display text-2xl mt-10 mb-1">GitHub image hosting</h2>
      <p className="text-sm mb-4" style={{ color: 'var(--muted)' }}>
        Instagram's Graph API needs public image URLs. Slides are uploaded to this repo via the GitHub API at publish time.
        A token with <span className="font-mono">contents:write</span> enables publishing directly from the app (no local git needed).
      </p>
      <div className="panel p-5 space-y-5">
        <div className="flex items-center gap-3 rounded-xl p-3" style={{ background: '#0d0c0a', border: '1px solid var(--border)' }}>
          <span className={gh?.connected ? 'live-dot' : ''} style={!gh?.connected ? { width: 7, height: 7, borderRadius: 99, background: 'var(--danger)' } : {}} />
          {gh?.connected
            ? <span className="text-sm">Repo <b style={{ color: 'var(--ok)' }}>connected</b> — <span className="font-mono">{gh.repo}</span> ({gh.branch}){gh.can_push === false && <span style={{ color: 'var(--danger)' }}> · token lacks write</span>}</span>
            : <span className="text-sm">Repo <b style={{ color: 'var(--danger)' }}>not connected</b>{gh?.reason ? <span style={{ color: 'var(--faint)' }}> — {gh.reason.replace(/_/g, ' ')}</span> : ''} — add a token with Contents write</span>}
        </div>

        <Field label="GitHub Personal Access Token" hint="Fine-grained PAT with Contents: Read & Write on the repo below. Blank = keep existing. Stored encrypted (rags), never sent to the browser.">
          <input className="input font-mono" type="password" value={keys.github_token || ''}
            onChange={(e) => setKeys({ ...keys, github_token: e.target.value })}
            placeholder={settings?.github_token_set ? '•••••••• (set)' : 'github_pat_...'} />
        </Field>

        <div className="grid md:grid-cols-3 gap-4">
          <Field label="GitHub user"><input className="input font-mono" value={keys.github_username || ''} onChange={(e) => setKeys({ ...keys, github_username: e.target.value })} /></Field>
          <Field label="GitHub repo"><input className="input font-mono" value={keys.github_repo || ''} onChange={(e) => setKeys({ ...keys, github_repo: e.target.value })} /></Field>
          <Field label="Branch"><input className="input font-mono" value={keys.github_branch || ''} onChange={(e) => setKeys({ ...keys, github_branch: e.target.value })} /></Field>
        </div>

        <div className="flex justify-end">
          <button className="btn btn-accent btn-sm" onClick={saveKeys} disabled={keysSaving}>
            {keysSaving ? <Spinner size={14} /> : <Icon name="check" size={14} />} Save GitHub settings
          </button>
        </div>
      </div>
    </div>
  );
}
