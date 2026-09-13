export const cx = (...a) => a.filter(Boolean).join(' ');

const paths = {
  studio: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  settings:
    'M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 13a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1A1.7 1.7 0 004.6 8a1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3H9a1.7 1.7 0 001-1.5V2a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z',
  history: 'M3 3v6h6M3 9a9 9 0 109-6M12 7v5l3 2',
  spark: 'M12 3l1.9 5.6L19.5 10l-5.6 1.4L12 17l-1.9-5.6L4.5 10l5.6-1.4z',
  quote: 'M7 7H4v6h3a2 2 0 002-2V7zM17 7h-3v6h3a2 2 0 002-2V7z',
  news: 'M4 5h13v14H6a2 2 0 01-2-2zM17 8h3v9a2 2 0 01-2 2M7 8h7M7 12h7M7 16h4',
  plus: 'M12 5v14M5 12h14',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  check: 'M5 13l4 4L19 7',
  chevL: 'M15 6l-6 6 6 6',
  chevR: 'M9 6l6 6-6 6',
  ext: 'M14 4h6v6M10 14L20 4M19 14v5a2 2 0 01-2 2H6a2 2 0 01-2-2V8a2 2 0 012-2h5',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z',
  x: 'M6 6l12 12M18 6L6 18',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  building: 'M3 21h18M5 21V5a1 1 0 011-1h8a1 1 0 011 1v16M15 21V9h3a1 1 0 011 1v11M8 8h2M8 12h2M8 16h2',
  pin: 'M12 21s-6-5.686-6-10a6 6 0 1112 0c0 4.314-6 10-6 10zM12 11a2 2 0 100-4 2 2 0 000 4z',
  doc: 'M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8zM14 3v5h5M9 13h6M9 17h4',
  shield: 'M12 3l7 3v6c0 4-3 7-7 8-4-1-7-4-7-8V6z',
  menu: 'M4 6h16M4 12h16M4 18h16',
};

export function Icon({ name, size = 20, className = '', stroke = 2 }) {
  const fill = name === 'studio' ? 'currentColor' : 'none';
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className}
      fill={fill} stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name] || ''} />
    </svg>
  );
}

export function Spinner({ size = 18 }) {
  return (
    <svg className="spin" width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Toast({ toast }) {
  if (!toast) return null;
  return <div className={cx('toast', toast.type === 'error' ? 'err' : 'ok')}>{toast.text}</div>;
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block mt-1 text-xs" style={{ color: 'var(--faint)' }}>{hint}</span>}
    </label>
  );
}
