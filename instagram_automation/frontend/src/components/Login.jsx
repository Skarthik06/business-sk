import { useEffect, useRef, useState } from 'react';
import api, { TOKEN_KEY, REFRESH_KEY } from '../services/api';
import { Icon, Spinner } from './ui';

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';

export default function Login({ onAuthed }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showFallback, setShowFallback] = useState(false);
  const gbtn = useRef(null);

  const finish = (r) => {
    localStorage.setItem(TOKEN_KEY, r.access_token);
    if (r.refresh_token) localStorage.setItem(REFRESH_KEY, r.refresh_token);
    onAuthed(r.admin);
  };

  // Load Google Identity Services and render the official "Sign in with Google" button.
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;
    const handleGoogle = async (resp) => {
      setBusy(true); setError('');
      try {
        finish(await api.adminGoogle(resp.credential));
      } catch {
        setError('That Google account is not authorized for this Studio.');
      } finally { setBusy(false); }
    };
    const init = () => {
      if (!window.google?.accounts?.id || !gbtn.current) return;
      window.google.accounts.id.initialize({ client_id: GOOGLE_CLIENT_ID, callback: handleGoogle });
      window.google.accounts.id.renderButton(gbtn.current,
        { theme: 'filled_blue', size: 'large', shape: 'pill', text: 'signin_with', width: 320 });
    };
    if (window.google?.accounts?.id) { init(); return; }
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.async = true; s.defer = true; s.onload = init;
    document.body.appendChild(s);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      finish(await api.adminLogin(username.trim(), password));
    } catch (err) {
      setError(err?.response?.data?.error?.message || err?.response?.data?.detail || 'Invalid credentials');
    } finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen grid place-items-center px-5" style={{ position: 'relative', zIndex: 2 }}>
      <div className="w-full" style={{ maxWidth: 400 }}>
        <div className="flex items-center gap-2.5 mb-8 justify-center">
          <div className="w-10 h-10 rounded-xl grid place-items-center"
            style={{ background: 'linear-gradient(135deg, var(--amber-2), var(--amber))', color: '#1a1206' }}>
            <Icon name="spark" size={22} />
          </div>
          <div>
            <div className="font-display text-2xl leading-none" style={{ fontWeight: 600 }}>Studio</div>
            <div className="eyebrow" style={{ fontSize: '0.56rem' }}>Instagram autopilot</div>
          </div>
        </div>

        <div className="panel p-6">
          <p className="eyebrow mb-1">Private admin</p>
          <h1 className="font-display text-2xl mb-5" style={{ fontWeight: 600 }}>Sign in</h1>

          {/* Primary: Google sign-in (restricted to the owner account) */}
          {GOOGLE_CLIENT_ID ? (
            <div className="grid place-items-center" style={{ minHeight: 44 }}>
              {busy ? <Spinner size={18} /> : <div ref={gbtn} />}
            </div>
          ) : (
            <div className="text-sm" style={{ color: 'var(--danger)' }}>
              Google sign-in isn’t configured (VITE_GOOGLE_CLIENT_ID missing).
            </div>
          )}

          {error && <div className="mt-3 text-sm" style={{ color: 'var(--danger)' }}>{error}</div>}

          {/* Emergency fallback: strong admin password (hidden by default) */}
          <button type="button" onClick={() => setShowFallback((v) => !v)}
            className="mt-5 text-xs font-mono w-full text-center"
            style={{ color: 'var(--faint)', background: 'none', border: 'none', cursor: 'pointer' }}>
            {showFallback ? 'Hide' : 'Use emergency password instead'}
          </button>

          {showFallback && (
            <form onSubmit={submit} className="mt-3 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
              <label className="block mb-3">
                <span className="label">Username</span>
                <input className="input" value={username} onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck="false" />
              </label>
              <label className="block mb-4">
                <span className="label">Password</span>
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password" autoCapitalize="none" autoCorrect="off" spellCheck="false" />
              </label>
              <button className="btn btn-accent w-full justify-center" type="submit" disabled={busy}>
                {busy ? <><Spinner size={16} /> Signing in…</> : 'Sign in with password'}
              </button>
            </form>
          )}

          <p className="mt-4 text-xs font-mono text-center" style={{ color: 'var(--faint)' }}>
            One administrator · private business workspace
          </p>
        </div>
      </div>
    </div>
  );
}
