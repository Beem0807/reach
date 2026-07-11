import { useState, useEffect, useCallback } from 'react';
import type { TenantConfig, TenantSettings, TenantSettingKey, TenantWavePolicy, FleetWavePolicy } from '../types';
import { getTenantSettings, updateTenantSettings } from '../api';
import type { TenantSettingsPatch } from '../api';
import { ApiError } from '../api';
import { Spinner } from '../components/Spinner';
import { RefreshButton } from '../components/RefreshButton';
import { WavePolicyRW } from '../components/WavePolicyEditor';

// The editable settings, in display order, grouped for the form. Retention windows
// are in days; the fan-out cap is a count of hosts.
const FIELDS: { key: TenantSettingKey; label: string; help: string; unit: string }[] = [
  { key: 'approval_retention_days', label: 'Approval retention', unit: 'days',
    help: 'How long denied/expired approval records are kept before cleanup.' },
  { key: 'job_retention_days', label: 'Job retention', unit: 'days',
    help: 'How long completed job records are kept before cleanup.' },
  { key: 'run_retention_days', label: 'Run retention', unit: 'days',
    help: 'How long fan-out run records are kept (they outlive their member jobs).' },
  { key: 'audit_retention_days', label: 'Audit-log retention', unit: 'days',
    help: 'How long this tenant’s audit-trail entries are kept before cleanup.' },
  { key: 'agent_history_retention_days', label: 'Agent-history retention', unit: 'days',
    help: 'How long agent status-change history is kept before cleanup.' },
  { key: 'fanout_cap', label: 'Fan-out cap', unit: 'hosts',
    help: 'Blast-radius ceiling: the most hosts a single fan-out can hit unless a fleet sets its own lower cap.' },
];

export function TenantSettingsPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;
  const [data, setData] = useState<TenantSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [wavePolicy, setWavePolicy] = useState<TenantWavePolicy>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const hydrate = useCallback((d: TenantSettings) => {
    setData(d);
    // Seed the form from the effective (in-force) values.
    const v: Record<string, string> = {};
    for (const f of FIELDS) v[f.key] = String(d.settings[f.key]);
    setValues(v);
    setWavePolicy(d.wave_policy || {});
  }, []);

  const setScope = (scope: 'tag' | 'fleet', rw: FleetWavePolicy) => {
    setWavePolicy(prev => {
      const next = { ...prev };
      if (Object.keys(rw).length) next[scope] = rw; else delete next[scope];
      return next;
    });
  };

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getTenantSettings(apiUrl, tenantToken)
      .then(hydrate)
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Failed to load settings'))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken, hydrate]);

  useEffect(() => { load(); }, [load]);

  const isOverridden = (k: TenantSettingKey) => data != null && k in data.overrides;

  const policyDirty = data != null && JSON.stringify(wavePolicy) !== JSON.stringify(data.wave_policy || {});
  const dirty = data != null && (policyDirty || FIELDS.some(f => values[f.key] !== String(data.settings[f.key])));

  const save = async () => {
    if (!data) return;
    // Only send changed keys. A field left at the platform default (and not currently
    // overridden) is sent as null to clear any stale override.
    const patch: TenantSettingsPatch = {};
    for (const f of FIELDS) {
      const raw = values[f.key].trim();
      const n = Number(raw);
      if (raw === '' || !Number.isInteger(n) || n < 1) {
        setError(`${f.label} must be a whole number ≥ 1`);
        return;
      }
      const [lo, hi] = data.bounds[f.key];
      if (n < lo || n > hi) {
        setError(`${f.label} must be between ${lo} and ${hi} ${f.unit}`);
        return;
      }
      if (n !== data.settings[f.key]) {
        // If it matches the platform default, clear the override; else set it.
        patch[f.key] = n === data.defaults[f.key] ? null : n;
      }
    }
    if (policyDirty) {
      // Send the whole policy object (server replaces it wholesale); null clears it.
      patch.wave_policy = Object.keys(wavePolicy).length ? wavePolicy : null;
    }
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateTenantSettings(apiUrl, tenantToken, patch);
      hydrate(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const resetToDefault = (k: TenantSettingKey) => {
    if (!data) return;
    setValues(prev => ({ ...prev, [k]: String(data.defaults[k]) }));
  };

  return (
    <div className="min-h-full bg-slate-50">
      {/* Page header */}
      <div className="bg-gradient-to-r from-sky-700 to-sky-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Settings</h1>
              <p className="text-sm text-sky-200">Retention windows &amp; fan-out cap for this tenant</p>
            </div>
          </div>
          <RefreshButton onClick={load} loading={loading} />
        </div>
      </div>

      <div className="px-8 py-6 max-w-3xl">
        {loading && !data ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : (
          <>
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm divide-y divide-slate-100">
              {FIELDS.map(f => (
                <div key={f.key} className="px-6 py-4 flex items-start justify-between gap-6">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <label htmlFor={f.key} className="text-sm font-semibold text-slate-800">{f.label}</label>
                      {isOverridden(f.key)
                        ? <span className="text-[10px] font-semibold uppercase tracking-wide bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">Custom</span>
                        : <span className="text-[10px] font-semibold uppercase tracking-wide bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">Default</span>}
                    </div>
                    <p className="text-xs text-slate-500 mt-1">{f.help}</p>
                    {data && (
                      <p className="text-[11px] text-slate-400 mt-1">
                        Platform default: {data.defaults[f.key]} {f.unit} · allowed {data.bounds[f.key][0]}–{data.bounds[f.key][1]}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <input
                      id={f.key}
                      type="number"
                      min={data?.bounds[f.key][0]}
                      max={data?.bounds[f.key][1]}
                      value={values[f.key] ?? ''}
                      onChange={e => setValues(prev => ({ ...prev, [f.key]: e.target.value }))}
                      className="w-24 border border-slate-300 rounded-lg px-3 py-1.5 text-sm text-right focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500"
                    />
                    <span className="text-xs text-slate-400 w-10">{f.unit}</span>
                    {/* Fixed-width slot so the input/unit stay column-aligned whether or not
                        the (custom-only) reset link is shown. */}
                    <div className="w-12 text-left">
                      {data && values[f.key] !== String(data.defaults[f.key]) && (
                        <button
                          onClick={() => resetToDefault(f.key)}
                          title="Reset to platform default"
                          className="text-xs text-slate-400 hover:text-sky-600 underline decoration-dotted"
                        >
                          reset
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {/* Staged-rollout wave policy. Wave SIZE comes from the fan-out cap above; this
                sets whether/how a fan-out is staged, per read vs write command. */}
            <div className="mt-8">
              <h2 className="text-sm font-bold text-slate-800">Staged rollout</h2>
              <p className="text-xs text-slate-500 mt-1 mb-3 max-w-2xl">
                A fan-out always runs in waves of the fan-out cap. <span className="font-medium">Auto</span>
                {' '}advances to the next wave automatically; <span className="font-medium">Manual</span>
                {' '}pauses after every wave for you to resume. On a wave failure,
                {' '}<span className="font-medium">Stop</span> pauses the rollout and
                {' '}<span className="font-medium">Continue</span> keeps going. Reads and writes are
                configured separately; a fleet can override the fleet default. <span className="font-medium">Default</span>
                {' '}inherits the platform default - reads <span className="font-mono">auto / continue</span>,
                writes <span className="font-mono">manual / stop</span>.
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                {([['tag', 'Tag fan-outs', 'Runs across standalone agents by tag.'],
                   ['fleet', 'Fleet fan-outs (default)', 'Default for fleet runs; a fleet can override it.']] as const).map(
                  ([scope, title, sub]) => (
                    <div key={scope} className="bg-white rounded-xl border border-slate-200 shadow-sm px-5 py-4">
                      <p className="text-sm font-semibold text-slate-800">{title}</p>
                      <p className="text-[11px] text-slate-400 mb-3">{sub}</p>
                      <WavePolicyRW value={wavePolicy[scope] || {}} onChange={rw => setScope(scope, rw)} />
                    </div>
                  ))}
              </div>
            </div>

            {error && (
              <div className="mt-4 bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-2.5 rounded-lg">{error}</div>
            )}

            <div className="mt-5 flex items-center gap-3">
              <button
                onClick={save}
                disabled={saving || !dirty}
                className="inline-flex items-center gap-2 bg-sky-600 hover:bg-sky-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors shadow-sm"
              >
                {saving ? <Spinner className="w-4 h-4" /> : null}
                Save changes
              </button>
              <button
                onClick={() => { if (data) hydrate(data); setError(null); }}
                disabled={saving || !dirty}
                className="text-sm font-medium text-slate-500 hover:text-slate-800 disabled:opacity-40 disabled:cursor-not-allowed px-2 py-2 transition-colors"
              >
                Discard
              </button>
              {saved && <span className="text-sm text-emerald-600 font-medium">Saved</span>}
              {dirty && !saved && <span className="text-xs text-slate-400">Unsaved changes</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
