import { createContext, useContext, useState, type ReactNode } from 'react';

// Global display-timezone preference for the console. Storage is always UTC (see the backend);
// this only controls how timestamps are RENDERED. Default is the viewer's local timezone; UTC
// is offered for operators who want unambiguous, cross-region times. Persisted per browser.

export type TimeZonePref = 'local' | 'UTC';
const KEY = 'reach.timezone';

// Module mirror of the preference so the pure `formatTs` (used by module-scope formatters that
// aren't React components) can read it without threading it through every call. Kept in sync by
// the provider. Defaults to local.
let _zone: TimeZonePref = 'local';
try {
  const saved = localStorage.getItem(KEY);
  if (saved === 'UTC' || saved === 'local') _zone = saved;
} catch { /* SSR / no storage */ }

export function currentZone(): TimeZonePref {
  return _zone;
}

// Format a UTC ISO timestamp for display in the selected zone. `undefined` timeZone = local.
export function formatTs(iso?: string | null, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, { timeZone: _zone === 'UTC' ? 'UTC' : undefined, ...opts });
}

const TzCtx = createContext<{ zone: TimeZonePref; setZone: (z: TimeZonePref) => void }>({
  zone: 'local',
  setZone: () => {},
});

export const useTimezone = () => useContext(TzCtx);

// Wraps the app. A zone change updates the module mirror + storage and re-renders the whole
// subtree, so every `formatTs` call reflows into the new zone.
export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [zone, setZoneState] = useState<TimeZonePref>(_zone);
  const setZone = (z: TimeZonePref) => {
    _zone = z;
    try { localStorage.setItem(KEY, z); } catch { /* ignore */ }
    setZoneState(z);
  };
  return <TzCtx.Provider value={{ zone, setZone }}>{children}</TzCtx.Provider>;
}

// The IANA name of the viewer's local zone, e.g. "Asia/Kolkata" - shown on the Local option.
function localZoneName(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local'; } catch { return 'local'; }
}

// Local/UTC control for the console (sidebar footer).
//   default  - a labeled segmented toggle (Local | UTC) for the expanded sidebar.
//   compact  - a single square button showing the active zone; clicking flips it. Fits the
//              collapsed sidebar rail so the option is reachable in either sidebar state.
// `dark` styles it for the dark sidebar.
export function TimezoneToggle({ dark = false, compact = false, className = '' }: {
  dark?: boolean; compact?: boolean; className?: string;
}) {
  const { zone, setZone } = useTimezone();

  if (compact) {
    const other = zone === 'UTC' ? 'local' : 'UTC';
    return (
      <button type="button" onClick={() => setZone(other)}
        title={`Times shown in ${zone === 'UTC' ? 'UTC' : `your local timezone (${localZoneName()})`} - click for ${other === 'UTC' ? 'UTC' : 'local'}`}
        className={`w-9 h-7 rounded-md text-[10px] font-bold transition-colors ${dark ? 'bg-slate-800/70 text-slate-300 hover:text-white hover:bg-slate-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'} ${className}`}>
        {zone === 'UTC' ? 'UTC' : 'LOC'}
      </button>
    );
  }

  const base = 'text-[11px] font-medium px-2 py-1 rounded transition-colors';
  const on = dark ? 'bg-slate-700 text-white' : 'bg-white text-gray-800 shadow-sm';
  const off = dark ? 'text-slate-400 hover:text-slate-200' : 'text-gray-500 hover:text-gray-700';
  return (
    <div className={`inline-flex items-center gap-0.5 rounded-md p-0.5 ${dark ? 'bg-slate-800/70' : 'bg-gray-100'} ${className}`}
      title={`Display times in ${zone === 'UTC' ? 'UTC' : 'your local timezone'}`}>
      <button type="button" onClick={() => setZone('local')} className={`${base} ${zone === 'local' ? on : off}`}
        title={`Local (${localZoneName()})`}>Local</button>
      <button type="button" onClick={() => setZone('UTC')} className={`${base} ${zone === 'UTC' ? on : off}`}>UTC</button>
    </div>
  );
}
