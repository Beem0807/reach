import { useState, useEffect, useCallback } from 'react';
import type { Config } from '../types';
import type { TenantSettings, TenantSettingKey, TenantWavePolicy, FleetWavePolicy } from '../types';
import { adminGetTenantSettings, adminUpdateTenantSettings } from '../api';
import type { TenantSettingsPatch } from '../api';
import { ApiError } from '../api';
import { Modal } from './Modal';
import { Spinner } from './Spinner';
import { WavePolicyRW } from './WavePolicyEditor';

// Same field set as the tenant-facing settings form, but here the platform admin's
// override bypasses the tenant bounds - so bounds are shown as info only, not enforced.
const FIELDS: { key: TenantSettingKey; label: string; unit: string }[] = [
  { key: 'approval_retention_days', label: 'Approval retention', unit: 'days' },
  { key: 'job_retention_days', label: 'Job retention', unit: 'days' },
  { key: 'run_retention_days', label: 'Run retention', unit: 'days' },
  { key: 'audit_retention_days', label: 'Audit-log retention', unit: 'days' },
  { key: 'agent_history_retention_days', label: 'Agent-history retention', unit: 'days' },
  { key: 'fanout_cap', label: 'Fan-out cap', unit: 'hosts' },
];

// Platform-admin override of a single tenant's settings. Unlike the tenant form this
// ignores the per-key bounds (the override endpoint is unbounded) and every change is
// recorded as a `tenant.settings_overridden` audit event server-side.
export function TenantSettingsOverrideModal({
  config, tenantId, tenantName, onClose,
}: {
  config: Config; tenantId: string; tenantName: string; onClose: () => void;
}) {
  const { apiUrl, adminToken } = config;
  const [data, setData] = useState<TenantSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [wavePolicy, setWavePolicy] = useState<TenantWavePolicy>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const hydrate = useCallback((d: TenantSettings) => {
    setData(d);
    const v: Record<string, string> = {};
    for (const f of FIELDS) v[f.key] = String(d.settings[f.key]);
    setValues(v);
    setWavePolicy(d.wave_policy || {});
  }, []);

  useEffect(() => {
    setLoading(true);
    adminGetTenantSettings(apiUrl, adminToken, tenantId)
      .then(hydrate)
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Failed to load settings'))
      .finally(() => setLoading(false));
  }, [apiUrl, adminToken, tenantId, hydrate]);

  const setScope = (scope: 'tag' | 'fleet', rw: FleetWavePolicy) => {
    setWavePolicy(prev => {
      const next = { ...prev };
      if (Object.keys(rw).length) next[scope] = rw; else delete next[scope];
      return next;
    });
  };

  const isOverridden = (k: TenantSettingKey) => data != null && k in data.overrides;
  const policyDirty = data != null && JSON.stringify(wavePolicy) !== JSON.stringify(data.wave_policy || {});
  const dirty = data != null && (policyDirty || FIELDS.some(f => values[f.key] !== String(data.settings[f.key])));

  const save = async () => {
    if (!data) return;
    const patch: TenantSettingsPatch = {};
    for (const f of FIELDS) {
      const raw = values[f.key].trim();
      const n = Number(raw);
      if (raw === '' || !Number.isInteger(n) || n < 1) {
        setError(`${f.label} must be a whole number ≥ 1`);
        return;
      }
      if (n !== data.settings[f.key]) {
        // Match the platform default -> clear the override; else set it (bounds bypassed).
        patch[f.key] = n === data.defaults[f.key] ? null : n;
      }
    }
    if (policyDirty) {
      patch.wave_policy = Object.keys(wavePolicy).length ? wavePolicy : null;
    }
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await adminUpdateTenantSettings(apiUrl, adminToken, tenantId, patch);
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
    <Modal
      wide
      onClose={onClose}
      title={<span>Override settings · <span className="font-normal text-gray-500">{tenantName}</span></span>}
    >
      {loading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : !data ? (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error || 'Failed to load settings'}
        </div>
      ) : (
        <>
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Platform override bypasses this tenant's bounds. Each change is recorded in the audit log.
          </div>

          <div className="rounded-xl border border-slate-200 divide-y divide-slate-100">
            {FIELDS.map(f => (
              <div key={f.key} className="px-4 py-3 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <label htmlFor={`ov-${f.key}`} className="text-sm font-semibold text-slate-800">{f.label}</label>
                    {isOverridden(f.key)
                      ? <span className="text-[10px] font-semibold uppercase tracking-wide bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">Custom</span>
                      : <span className="text-[10px] font-semibold uppercase tracking-wide bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">Default</span>}
                  </div>
                  <p className="text-[11px] text-slate-400 mt-1">
                    Platform default {data.defaults[f.key]} · tenant bounds {data.bounds[f.key][0]}–{data.bounds[f.key][1]} {f.unit} (bypassed)
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <input
                    id={`ov-${f.key}`}
                    type="number"
                    min={1}
                    value={values[f.key] ?? ''}
                    onChange={e => setValues(prev => ({ ...prev, [f.key]: e.target.value }))}
                    className="w-24 border border-slate-300 rounded-lg px-3 py-1.5 text-sm text-right focus:outline-none focus:ring-2 focus:ring-slate-500/40 focus:border-slate-500"
                  />
                  <span className="text-xs text-slate-400 w-10">{f.unit}</span>
                  <div className="w-12 text-left">
                    {values[f.key] !== String(data.defaults[f.key]) && (
                      <button
                        onClick={() => resetToDefault(f.key)}
                        title="Reset to platform default"
                        className="text-xs text-slate-400 hover:text-slate-700 underline decoration-dotted"
                      >
                        reset
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-5">
            <h3 className="text-sm font-bold text-slate-800">Staged rollout</h3>
            <p className="text-[11px] text-slate-400 mt-1 mb-3">
              Wave policy per fan-out scope. <span className="font-medium">Default</span> inherits the platform
              default (reads auto/continue, writes manual/stop).
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {([['tag', 'Tag fan-outs'], ['fleet', 'Fleet fan-outs (default)']] as const).map(([scope, title]) => (
                <div key={scope} className="rounded-xl border border-slate-200 px-4 py-3">
                  <p className="text-sm font-semibold text-slate-800 mb-3">{title}</p>
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
              className="inline-flex items-center gap-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors shadow-sm"
            >
              {saving ? <Spinner className="w-4 h-4" /> : null}
              Save override
            </button>
            <button
              onClick={() => { hydrate(data); setError(null); }}
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
    </Modal>
  );
}
