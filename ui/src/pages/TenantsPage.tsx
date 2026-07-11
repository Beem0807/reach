import { useState, useEffect, useCallback } from 'react';
import type { Config, Tenant } from '../types';
import { listTenants, createTenant, disableTenant, enableTenant, listUsers, listAgentsAdmin } from '../api';
import { Modal } from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { RefreshButton } from '../components/RefreshButton';
import { TenantSettingsOverrideModal } from '../components/TenantSettingsOverrideModal';
import { tenantPalette, tenantInitials } from '../utils';

function fmtDate(iso?: string) {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function TenantsPage({ config }: { config: Config }) {
  const { apiUrl, adminToken } = config;
  const PAGE = 20;
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const applySearch = () => { setQuery(search.trim()); setOffset(0); };
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [disableTarget, setDisableTarget] = useState<Tenant | null>(null);
  const [enableTarget, setEnableTarget] = useState<Tenant | null>(null);
  const [settingsTarget, setSettingsTarget] = useState<Tenant | null>(null);
  const [counts, setCounts] = useState<Record<string, { users: number; agents: number }>>({});

  const loadTenants = useCallback(() => {
    setLoading(true);
    const params: Record<string, string> = { limit: String(PAGE), offset: String(offset) };
    if (query) params.q = query;
    listTenants(apiUrl, adminToken, params)
      .then(r => {
        setTenants(r.tenants);
        setTotal(r.total ?? r.tenants.length);
        Promise.allSettled(
          r.tenants.map(t =>
            Promise.all([
              listUsers(apiUrl, adminToken, t.tenant_id).then(u => u.users.length).catch(() => 0),
              listAgentsAdmin(apiUrl, adminToken, t.tenant_id).then(a => a.agents.length).catch(() => 0),
            ]).then(([users, agents]) => ({ tenant_id: t.tenant_id, users, agents }))
          )
        ).then(results => {
          const map: Record<string, { users: number; agents: number }> = {};
          for (const r of results) {
            if (r.status === 'fulfilled') map[r.value.tenant_id] = { users: r.value.users, agents: r.value.agents };
          }
          setCounts(map);
        });
      })
      .catch(() => setError('Failed to load tenants'))
      .finally(() => setLoading(false));
  }, [apiUrl, adminToken, query, offset]);

  useEffect(() => { loadTenants(); }, [loadTenants]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await createTenant(apiUrl, adminToken, newName.trim());
      setNewName(''); setCreating(false);
      loadTenants();
    } catch (e) { alert((e as Error).message); }
  };

  const handleEnable = async (tenant: Tenant) => {
    try {
      await enableTenant(apiUrl, adminToken, tenant.tenant_id);
      setEnableTarget(null);
      loadTenants();
    } catch (e) { alert((e as Error).message); }
  };

  const handleDisable = async (tenant: Tenant) => {
    try {
      await disableTenant(apiUrl, adminToken, tenant.tenant_id);
      setDisableTarget(null);
      loadTenants();
    } catch (e) { alert((e as Error).message); }
  };

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">Tenants</h1>
          <p className="text-sm text-gray-500 mt-0.5">Organisations with access to reach</p>
        </div>
        <div className="flex items-center gap-3">
          {tenants.length > 0 && (
            <span className="text-sm text-gray-500 bg-gray-100 px-3 py-1.5 rounded-full font-medium">
              {tenants.length} {tenants.length === 1 ? 'tenant' : 'tenants'}
            </span>
          )}
          <RefreshButton onClick={loadTenants} loading={loading} variant="onLight" />
          <button
            onClick={() => setCreating(true)}
            className="inline-flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
          >
            <span className="text-base leading-none">+</span> New tenant
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6">
          <span className="shrink-0">⚠</span>{error}
        </div>
      )}

      <div className="flex items-center gap-2 mb-4">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') applySearch(); }}
          placeholder="Search tenants by name or ID…"
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 w-72"
        />
        <button onClick={applySearch} className="text-sm text-white bg-slate-800 hover:bg-slate-700 rounded-lg px-3 py-1.5">Search</button>
        {query && (
          <button onClick={() => { setSearch(''); setQuery(''); setOffset(0); }} className="text-sm text-indigo-600 hover:text-indigo-800" aria-label="Clear search">✕</button>
        )}
      </div>

      {loading && tenants.length === 0 ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : tenants.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-28 text-center">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-100 to-indigo-50 flex items-center justify-center mb-5 shadow-sm">
            <svg className="w-8 h-8 text-indigo-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21" />
            </svg>
          </div>
          <p className="font-semibold text-gray-800 text-lg mb-1">No tenants yet</p>
          <p className="text-sm text-gray-500 mb-6 max-w-xs">Create your first tenant to start onboarding users and agents.</p>
          <button onClick={() => setCreating(true)}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-5 py-2.5 rounded-lg transition-colors shadow-sm">
            Create first tenant
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {tenants.map(t => {
            const [avatarCls] = tenantPalette(t.tenant_id);
            const initials = tenantInitials(t.name);
            const created = fmtDate(t.created_at);
            return (
              <div
                key={t.tenant_id}
                className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow p-4 flex flex-col gap-3"
              >
                {/* Avatar + name + id */}
                <div className="flex items-center gap-3 min-w-0">
                  <div className={`w-10 h-10 rounded-lg ${avatarCls} flex items-center justify-center shrink-0`}>
                    <span className="text-white text-sm font-bold">{initials}</span>
                  </div>
                  <div className="min-w-0">
                    <p className="font-semibold text-gray-900 truncate text-sm">{t.name}</p>
                    <p className="font-mono text-[10px] text-gray-400 truncate mt-0.5">{t.tenant_id}</p>
                  </div>
                </div>

                {/* Counts */}
                {counts[t.tenant_id] !== undefined && (
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-500">
                      <span className="font-semibold text-gray-800">{counts[t.tenant_id].agents}</span> agent{counts[t.tenant_id].agents !== 1 ? 's' : ''}
                    </span>
                    <span className="text-gray-200">·</span>
                    <span className="text-xs text-gray-500">
                      <span className="font-semibold text-gray-800">{counts[t.tenant_id].users}</span> user{counts[t.tenant_id].users !== 1 ? 's' : ''}
                    </span>
                  </div>
                )}

                {/* Footer: date + actions */}
                <div className="flex items-center justify-between pt-2 border-t border-gray-100">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400">{created ?? '-'}</span>
                    {t.status === 'DISABLED' && (
                      <span className="text-[10px] font-semibold bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full">DISABLED</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setSettingsTarget(t)}
                      className="text-xs font-medium px-2.5 py-1 rounded-md text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors"
                    >
                      Settings
                    </button>
                    <button
                      onClick={() => t.status === 'DISABLED' ? setEnableTarget(t) : setDisableTarget(t)}
                      className={`text-xs font-medium px-2.5 py-1 rounded-md transition-colors ${
                        t.status === 'DISABLED'
                          ? 'text-emerald-600 hover:text-emerald-800 hover:bg-emerald-50'
                          : 'text-red-400 hover:text-red-600 hover:bg-red-50'
                      }`}
                    >
                      {t.status === 'DISABLED' ? 'Enable' : 'Disable'}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {total > PAGE && (
        <div className="flex items-center justify-between mt-5 text-sm text-gray-600">
          <span>Showing {offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
              disabled={offset === 0 || loading}
              className="px-3 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50"
            >Prev</button>
            <button
              onClick={() => setOffset(offset + PAGE)}
              disabled={offset + PAGE >= total || loading}
              className="px-3 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50"
            >Next</button>
          </div>
        </div>
      )}

      {creating && (
        <Modal title="New tenant" onClose={() => { setCreating(false); setNewName(''); }}>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Organisation name</label>
              <input
                autoFocus
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCreate()}
                placeholder="Acme Corp"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </div>
            <div className="flex justify-end gap-3 pt-1">
              <button onClick={() => { setCreating(false); setNewName(''); }} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
              <button
                onClick={handleCreate}
                disabled={!newName.trim()}
                className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition-colors"
              >
                Create
              </button>
            </div>
          </div>
        </Modal>
      )}

      {disableTarget && (
        <DisableTenantModal
          tenant={disableTarget}
          onClose={() => setDisableTarget(null)}
          onConfirm={handleDisable}
        />
      )}

      {enableTarget && (
        <EnableTenantModal
          tenant={enableTarget}
          onClose={() => setEnableTarget(null)}
          onConfirm={handleEnable}
        />
      )}

      {settingsTarget && (
        <TenantSettingsOverrideModal
          config={config}
          tenantId={settingsTarget.tenant_id}
          tenantName={settingsTarget.name}
          onClose={() => setSettingsTarget(null)}
        />
      )}
    </div>
  );
}

function EnableTenantModal({
  tenant, onClose, onConfirm,
}: {
  tenant: Tenant;
  onClose: () => void;
  onConfirm: (t: Tenant) => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try { await onConfirm(tenant); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Enable tenant" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-emerald-800 mb-1">Users will regain access immediately</p>
          <p className="text-sm text-emerald-700">
            All users in <strong>{tenant.name}</strong> will be able to log in and use the CLI again.
          </p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors"
          >
            {loading && <Spinner className="h-4 w-4" />}
            Enable tenant
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DisableTenantModal({
  tenant, onClose, onConfirm,
}: {
  tenant: Tenant;
  onClose: () => void;
  onConfirm: (t: Tenant) => Promise<void>;
}) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const match = input === tenant.tenant_id;

  const submit = async () => {
    if (!match) return;
    setLoading(true); setError('');
    try { await onConfirm(tenant); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Disable tenant" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-amber-800 mb-1">All user access will be suspended</p>
          <p className="text-sm text-amber-700">
            Users in <strong>{tenant.name}</strong> will be blocked immediately - CLI and UI access will stop.
            Agents will continue running. You can re-enable the tenant at any time.
          </p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Type <code className="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded text-xs font-mono">{tenant.tenant_id}</code> to confirm
          </label>
          <input
            autoFocus
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && match && submit()}
            placeholder={tenant.tenant_id}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-amber-500 focus:border-transparent"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={!match || loading}
            className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors"
          >
            {loading && <Spinner className="h-4 w-4" />}
            Disable tenant
          </button>
        </div>
      </div>
    </Modal>
  );
}
