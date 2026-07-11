import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { TenantConfig, TenantUser, TenantRole, Agent, Fleet, UserAccessScope } from '../types';
import {
  listTenantUsers, createTenantUser, disableTenantUser, enableTenantUser, deleteTenantUser, revokeAllUserTokens,
  setTenantUserRole, resetTenantUserPassword,
  getUserAgentAccess, setUserAgentAccess, listTenantAgents, listFleets,
} from '../api';
import { Modal } from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { RefreshButton } from '../components/RefreshButton';
import { relTime } from '../utils';

const ROLES: TenantRole[] = ['admin', 'operator', 'developer'];

const ROLE_STYLE: Record<TenantRole, string> = {
  admin:     'bg-indigo-100 text-indigo-700',
  operator:  'bg-amber-100 text-amber-700',
  developer: 'bg-gray-100 text-gray-600',
};

const ROLE_LABEL: Record<TenantRole, string> = {
  admin:    'Admin',
  operator: 'Operator',
  developer: 'Developer',
};

const ROLE_DESC: Record<TenantRole, string> = {
  admin:    'Full access: manage users, audit logs.',
  operator: 'Operational: manage agents, review approvals.',
  developer: 'CLI/MCP access: run commands, view jobs.',
};

// Summary of a user's access for the users table: one line per resource type
// (agents, fleets), each showing the read-write / read-only split.
function accessSummary(u: TenantUser) {
  if (u.role === 'admin') return <span className="text-[11px] text-gray-400" title="tenant-wide">tenant-wide</span>;
  const rwA = u.readwrite_agent_ids ?? [], roA = u.readonly_agent_ids ?? [];
  const rwF = u.readwrite_fleet_ids ?? [], roF = u.readonly_fleet_ids ?? [];

  const line = (rw: number, ro: number, noun: string) => {
    if (rw + ro === 0) return null;
    return (
      <span key={noun} className="text-[11px]">
        <span className="text-gray-400">{noun}</span>{' '}
        {rw > 0 && <span className="font-semibold text-indigo-500">{rw} r/w</span>}
        {rw > 0 && ro > 0 && <span className="text-gray-300"> · </span>}
        {ro > 0 && <span className="font-semibold text-sky-500">{ro} read</span>}
      </span>
    );
  };
  const rows = [line(rwA.length, roA.length, 'agents'), line(rwF.length, roF.length, 'fleets')].filter(Boolean);
  if (rows.length === 0) return <span className="text-[11px] font-semibold text-red-500">no access</span>;
  return <span className="flex flex-col leading-tight">{rows}</span>;
}

export function TenantUsersPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken, role } = config;
  const isAdmin = role === 'admin';

  const PAGE = 20;
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  // Draft filters - staged in the toolbar. Nothing hits the server until Search: a
  // dropdown choice, like the text box, is only applied on click / Enter.
  const [roleFilter, setRoleFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');
  // Applied filters - what the current results reflect.
  const EMPTY_APPLIED = { role: '', status: '', q: '' };
  const [applied, setApplied] = useState(EMPTY_APPLIED);
  const applySearch = () => { setApplied({ role: roleFilter, status: statusFilter, q: search.trim() }); setOffset(0); };
  const filtersDirty = roleFilter !== applied.role || statusFilter !== applied.status || search.trim() !== applied.q;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<'create' | 'role' | 'creds' | null>(null);
  const [targetUser, setTargetUser] = useState<TenantUser | null>(null);
  const [createdCreds, setCreatedCreds] = useState<{ username: string; temp_password: string } | null>(null);
  const [resetPwTarget, setResetPwTarget] = useState<TenantUser | null>(null);
  const [disableTarget, setDisableTarget] = useState<TenantUser | null>(null);
  const [enableTarget, setEnableTarget] = useState<TenantUser | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TenantUser | null>(null);
  const [revokeTokensTarget, setRevokeTokensTarget] = useState<TenantUser | null>(null);
  const [agentAccessTarget, setAgentAccessTarget] = useState<TenantUser | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    const params: Record<string, string> = { limit: String(PAGE), offset: String(offset) };
    if (applied.role) params.role = applied.role;
    if (applied.status) params.status = applied.status;
    if (applied.q) params.q = applied.q;
    listTenantUsers(apiUrl, tenantToken, params)
      .then(r => { setUsers(r.users); setTotal(r.total ?? r.users.length); })
      .catch(() => setError('Failed to load users'))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken, applied, offset]);

  useEffect(() => { load(); }, [load]);

  const doDisable = async (u: TenantUser, revokeTokens: boolean) => {
    await disableTenantUser(apiUrl, tenantToken, u.user_id);
    if (revokeTokens) await revokeAllUserTokens(apiUrl, tenantToken, u.user_id).catch(() => {});
    setDisableTarget(null);
    load();
  };

  const doEnable = async (u: TenantUser) => {
    await enableTenantUser(apiUrl, tenantToken, u.user_id);
    setEnableTarget(null);
    load();
  };

  const doDelete = async (u: TenantUser) => {
    await deleteTenantUser(apiUrl, tenantToken, u.user_id);
    setDeleteTarget(null);
    load();
  };

  const doRevokeTokens = async (u: TenantUser) => {
    await revokeAllUserTokens(apiUrl, tenantToken, u.user_id);
    setRevokeTokensTarget(null);
    load();
  };

  const doResetPw = async (u: TenantUser) => {
    const r = await resetTenantUserPassword(apiUrl, tenantToken, u.user_id);
    setResetPwTarget(null);
    setCreatedCreds({ username: u.username, temp_password: r.temp_password });
    setModal('creds');
  };

  const adminCount   = users.filter(u => u.role === 'admin').length;
  const operatorCount = users.filter(u => u.role === 'operator').length;
  const devCount     = users.filter(u => u.role === 'developer').length;

  return (
    <div className="min-h-full bg-slate-50">
      {/* Page header */}
      <div className="bg-gradient-to-r from-violet-700 to-violet-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Users</h1>
              <p className="text-sm text-violet-200">Manage tenant users and roles</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {!loading && users.length > 0 && total <= PAGE && !applied.role && !applied.status && !applied.q && (
              <>
                {adminCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-white/15 border border-white/20 text-violet-100 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    {adminCount} admin{adminCount !== 1 ? 's' : ''}
                  </span>
                )}
                {operatorCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-amber-500/20 border border-amber-400/30 text-amber-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    {operatorCount} operator{operatorCount !== 1 ? 's' : ''}
                  </span>
                )}
                {devCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-white/10 border border-white/15 text-violet-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    {devCount} developer{devCount !== 1 ? 's' : ''}
                  </span>
                )}
              </>
            )}
            <RefreshButton onClick={load} loading={loading} />
            {isAdmin && (
              <button
                onClick={() => setModal('create')}
                className="inline-flex items-center gap-1.5 bg-white text-violet-700 hover:bg-violet-50 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
              >
                <span className="text-base leading-none">+</span> Add user
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4">{error}</div>
      )}

      {/* Filters + search are staged and applied together, only on Search / Enter -
          choosing a dropdown option doesn't re-query until you click Search. */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select
          value={roleFilter}
          onChange={e => setRoleFilter(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
        >
          <option value="">All roles</option>
          <option value="admin">Admins</option>
          <option value="operator">Operators</option>
          <option value="developer">Developers</option>
        </select>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
        >
          <option value="">All statuses</option>
          <option value="ACTIVE">Active</option>
          <option value="REVOKED">Disabled</option>
        </select>
        <div className="flex items-center gap-2 ml-auto">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') applySearch(); }}
            placeholder="Search name or username…"
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
          />
          <button
            onClick={applySearch}
            className={`text-sm text-white rounded-lg px-3 py-1.5 ${filtersDirty ? 'bg-indigo-600 hover:bg-indigo-500 ring-2 ring-indigo-300' : 'bg-slate-800 hover:bg-slate-700'}`}
          >Search</button>
          {(applied.role || applied.status || applied.q) && (
            <button
              onClick={() => { setRoleFilter(''); setStatusFilter(''); setSearch(''); setApplied(EMPTY_APPLIED); setOffset(0); }}
              className="text-sm text-indigo-600 hover:text-indigo-800"
              aria-label="Clear filters"
            >✕</button>
          )}
        </div>
      </div>

      {loading && users.length === 0 ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <DataTable
            tableId="users"
            columns={[
              { label: 'Name',       sortValue: u => u.name ?? u.username, required: true },
              { label: 'Username',   sortValue: u => u.username },
              { label: 'Role',       sortValue: u => u.role ?? '' },
              { label: 'Status',     sortValue: u => u.status ?? '' },
              { label: 'Last login', sortValue: u => u.last_login_at ?? '' },
              { label: 'Created',    sortValue: u => u.created_at ?? '' },
              { label: 'User ID',    sortValue: u => u.user_id, defaultHidden: true },
              { label: '' },
            ]}
            rows={users}
            fallback="No users"
            renderRow={u => (
              <tr key={u.user_id} className="hover:bg-gray-50/70">
                <td className="px-4 py-3.5">
                  <div className="flex items-start gap-1.5">
                    <div className="min-w-0">
                      <button
                        onClick={() => (isAdmin && u.role !== 'admin' && u.user_id !== config.userId && u.status !== 'REVOKED') ? setAgentAccessTarget(u) : undefined}
                        className={`font-medium text-sm text-gray-900 text-left leading-tight ${(isAdmin && u.role !== 'admin' && u.user_id !== config.userId && u.status !== 'REVOKED') ? 'hover:text-violet-700 transition-colors cursor-pointer' : 'cursor-default'}`}
                      >
                        {u.name || u.username}
                      </button>
                      <div className="mt-0.5">{accessSummary(u)}</div>
                    </div>
                    {u.must_reset_password && (
                      u.last_login_at
                        ? <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-semibold shrink-0 mt-0.5" title="Temporary password issued - pending change">TEMP PW</span>
                        : <span className="text-[10px] bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded font-semibold shrink-0 mt-0.5" title="Created but has never logged in">INVITED</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3.5 text-sm text-gray-600 font-mono">@{u.username}</td>
                <td className="px-4 py-3.5">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ROLE_STYLE[u.role as TenantRole] ?? 'bg-gray-100 text-gray-600'}`}>
                    {ROLE_LABEL[u.role as TenantRole] ?? u.role}
                  </span>
                </td>
                <td className="px-4 py-3.5">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${u.status === 'REVOKED' ? 'bg-red-100 text-red-600' : 'bg-emerald-100 text-emerald-700'}`}>
                    {u.status === 'REVOKED' ? 'disabled' : 'active'}
                  </span>
                </td>
                <td className="px-4 py-3.5 text-sm text-gray-500">{relTime(u.last_login_at)}</td>
                <td className="px-4 py-3.5 text-sm text-gray-500">{relTime(u.created_at)}</td>
                <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{u.user_id}</td>
                <td className="px-4 py-3.5">
                  {isAdmin && u.user_id !== config.userId && (
                    u.status === 'REVOKED' ? (
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setEnableTarget(u)}
                          className="text-xs font-semibold text-emerald-700 hover:text-white hover:bg-emerald-600 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-md transition-colors"
                        >
                          Enable
                        </button>
                        <button
                          onClick={() => setDeleteTarget(u)}
                          className="text-xs font-semibold text-red-700 hover:text-white hover:bg-red-600 bg-red-50 border border-red-200 px-2.5 py-1 rounded-md transition-colors"
                        >
                          Delete
                        </button>
                      </div>
                    ) : (
                      <TenantUserMenu
                        onChangeRole={() => { setTargetUser(u); setModal('role'); }}
                        onResetPw={() => setResetPwTarget(u)}
                        onRevokeTokens={() => setRevokeTokensTarget(u)}
                        onDisable={() => setDisableTarget(u)}
                        onAgentAccess={() => setAgentAccessTarget(u)}
                        showAgentAccess={u.role !== 'admin'}
                      />
                    )
                  )}
                </td>
              </tr>
            )}
          />
          {total > PAGE && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 bg-gray-50/60 text-sm text-gray-600">
              <span>Showing {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}
                  disabled={offset === 0}
                  className="px-3 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50"
                >Prev</button>
                <button
                  onClick={() => setOffset(offset + PAGE)}
                  disabled={offset + PAGE >= total}
                  className="px-3 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50"
                >Next</button>
              </div>
            </div>
          )}
        </div>
      )}

      {modal === 'create' && (
        <CreateUserModal
          apiUrl={apiUrl}
          tenantToken={tenantToken}
          onClose={() => setModal(null)}
          onCreated={(creds) => { setCreatedCreds(creds); setModal('creds'); load(); }}
        />
      )}

      {modal === 'role' && targetUser && (
        <ChangeRoleModal
          user={targetUser}
          apiUrl={apiUrl}
          tenantToken={tenantToken}
          onClose={() => { setModal(null); setTargetUser(null); }}
          onChanged={(editAgentsFor) => {
            load(); setModal(null); setTargetUser(null);
            if (editAgentsFor) setAgentAccessTarget(editAgentsFor);
          }}
        />
      )}

      {modal === 'creds' && createdCreds && (
        <CredentialsModal creds={createdCreds} onClose={() => { setModal(null); setCreatedCreds(null); }} />
      )}

      {resetPwTarget && (
        <ResetPwModal
          user={resetPwTarget}
          onClose={() => setResetPwTarget(null)}
          onConfirm={() => doResetPw(resetPwTarget)}
        />
      )}

      {disableTarget && (
        <DisableUserModal
          user={disableTarget}
          onClose={() => setDisableTarget(null)}
          onConfirm={(rt) => doDisable(disableTarget, rt)}
        />
      )}

      {enableTarget && (
        <EnableUserModal
          user={enableTarget}
          onClose={() => setEnableTarget(null)}
          onConfirm={() => doEnable(enableTarget)}
        />
      )}

      {deleteTarget && (
        <DeleteUserModal
          user={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onConfirm={() => doDelete(deleteTarget)}
        />
      )}

      {revokeTokensTarget && (
        <RevokeTokensModal
          user={revokeTokensTarget}
          onClose={() => setRevokeTokensTarget(null)}
          onConfirm={() => doRevokeTokens(revokeTokensTarget)}
        />
      )}

      {agentAccessTarget && (
        <AgentAccessModal
          apiUrl={apiUrl}
          tenantToken={tenantToken}
          user={agentAccessTarget}
          onClose={() => { setAgentAccessTarget(null); load(); }}
        />
      )}
      </div>
    </div>
  );
}

function CreateUserModal({
  apiUrl, tenantToken, onClose, onCreated,
}: {
  apiUrl: string;
  tenantToken: string;
  onClose: () => void;
  onCreated: (c: { username: string; temp_password: string }) => void;
}) {
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<TenantRole>('developer');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [fleets, setFleets] = useState<Fleet[]>([]);
  const [scope, setScope] = useState<UserAccessScope>(EMPTY_SCOPE);

  useEffect(() => {
    Promise.all([
      listTenantAgents(apiUrl, tenantToken),
      listFleets(apiUrl, tenantToken).catch(() => ({ fleets: [] as Fleet[] })),
    ]).then(([a, f]) => { setAgents(a.agents ?? []); setFleets(f.fleets ?? []); }).catch(() => {});
  }, [apiUrl, tenantToken]);

  const usernameFormatError = username && !/^[a-z0-9]*$/.test(username)
    ? 'Lowercase letters and numbers only - no spaces or special characters'
    : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username) { setError('Username required'); return; }
    if (username.length < 2) { setError('Username must be at least 2 characters'); return; }
    if (usernameFormatError) { setError(usernameFormatError); return; }
    setLoading(true); setError('');
    try {
      // Admins are tenant-wide (no scope sent); non-admins get the access chosen below.
      const body = role === 'admin' ? { username, name, role } : { username, name, role, ...scope };
      const r = await createTenantUser(apiUrl, tenantToken, body);
      onCreated({ username: r.username!, temp_password: r.temp_password! });
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal wide title="Add user" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Full name <span className="text-gray-400">(optional)</span></label>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="Alice Smith" className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-sm font-medium text-gray-700">Username</label>
              <span className={`text-xs tabular-nums ${username.length > 28 ? (username.length >= 32 ? 'text-red-500 font-semibold' : 'text-amber-500') : 'text-gray-400'}`}>
                {username.length}/32
              </span>
            </div>
            <input
              value={username}
              onChange={e => setUsername(e.target.value.toLowerCase())}
              maxLength={32}
              placeholder="alice"
              className={`w-full border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 ${usernameFormatError ? 'border-red-400 focus:ring-red-400' : 'border-gray-300 focus:ring-indigo-500'}`}
            />
            {usernameFormatError && (
              <p className="mt-1 text-xs text-red-500">{usernameFormatError}</p>
            )}
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Role</label>
          <div className="grid grid-cols-3 gap-2">
            {ROLES.map(r => (
              <button key={r} type="button" onClick={() => setRole(r)}
                className={`text-left px-3 py-2.5 rounded-md border transition-colors ${role === r ? 'bg-indigo-50 border-indigo-400 text-indigo-900' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}>
                <div className="mb-1"><span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${ROLE_STYLE[r]}`}>{ROLE_LABEL[r]}</span></div>
                <p className="text-[11px] text-gray-500 leading-tight">{ROLE_DESC[r]}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Agent/fleet access - admins are tenant-wide; others get what you grant here (or later). */}
        {role === 'admin' ? (
          <div className="flex items-start gap-2 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2.5">
            <svg className="w-4 h-4 text-indigo-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
            <p className="text-xs text-indigo-800">Admins have <span className="font-semibold">tenant-wide access</span> to every agent and fleet.</p>
          </div>
        ) : (
          <div className="space-y-3 border-t border-gray-100 pt-3">
            <p className="text-sm font-medium text-gray-700">Access <span className="font-normal text-gray-400">(optional - grant now or later)</span></p>
            <ScopeEditor agents={agents} fleets={fleets} initial={EMPTY_SCOPE} onChange={setScope} />
          </div>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button type="submit" disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Create user
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ChangeRoleModal({
  user, apiUrl, tenantToken, onClose, onChanged,
}: {
  user: TenantUser;
  apiUrl: string;
  tenantToken: string;
  onClose: () => void;
  onChanged: (editAgentsFor?: TenantUser) => void;
}) {
  const [role, setRole] = useState<TenantRole>((user.role as TenantRole) ?? 'developer');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Demoting an admin to a scopeable role is the moment agent access becomes
  // relevant (an admin has none). Offer to set it right after the role change.
  const demotingAdmin = user.role === 'admin' && role !== 'admin';

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError('');
    try {
      await setTenantUserRole(apiUrl, tenantToken, user.user_id, role);
      onChanged(demotingAdmin ? { ...user, role, readwrite_agent_ids: null } : undefined);
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={`Change role - @${user.username}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          {ROLES.map(r => (
            <button key={r} type="button" onClick={() => setRole(r)}
              className={`w-full flex items-center gap-3 text-left px-3 py-2.5 rounded-md border transition-colors ${role === r ? 'bg-indigo-50 border-indigo-400 text-indigo-900' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}>
              <span className="w-24 shrink-0">
                <span className={`inline-block text-xs font-semibold px-1.5 py-0.5 rounded ${ROLE_STYLE[r]}`}>{ROLE_LABEL[r]}</span>
              </span>
              <span className="text-xs text-gray-500">{ROLE_DESC[r]}</span>
            </button>
          ))}
        </div>
        {role === 'admin' && user.role !== 'admin' && user.readwrite_agent_ids != null && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
            <svg className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
            </svg>
            <p className="text-xs text-amber-800">
              This user is currently restricted to{' '}
              <span className="font-semibold">{user.readwrite_agent_ids.length} agent{user.readwrite_agent_ids.length !== 1 ? 's' : ''}</span>.
              Promoting to Admin grants <span className="font-semibold">tenant-wide access</span> - the restriction will be removed.
            </p>
          </div>
        )}
        {demotingAdmin && (
          <div className="flex items-start gap-2 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2.5">
            <svg className="w-4 h-4 text-indigo-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
            <p className="text-xs text-indigo-800">
              Admins have access to all agents. After saving, you can restrict{' '}
              <span className="font-semibold">@{user.username}</span> to specific agents - the agent access editor opens next.
            </p>
          </div>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button type="submit" disabled={loading || role === user.role}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Save
          </button>
        </div>
      </form>
    </Modal>
  );
}

function CredentialsModal({ creds, onClose }: { creds: { username: string; temp_password: string }; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(creds.temp_password);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <Modal title="User credentials" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm text-amber-800 font-medium">Save this temporary password now.</p>
          <p className="text-xs text-amber-700 mt-0.5">It will not be shown again. The user must reset it on first login.</p>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-1">Username</p>
          <code className="text-sm font-mono text-gray-800">@{creds.username}</code>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-1">Temporary password</p>
          <div className="flex items-center gap-2">
            <code className="text-sm font-mono bg-gray-100 px-3 py-1.5 rounded flex-1">{creds.temp_password}</code>
            <button onClick={copy} className="text-xs text-indigo-600 hover:text-indigo-800 whitespace-nowrap">
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>
        </div>
        <div className="flex justify-end pt-1">
          <button onClick={onClose} className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-md">Done</button>
        </div>
      </div>
    </Modal>
  );
}

function TenantUserMenu({
  onChangeRole, onResetPw, onRevokeTokens, onDisable, onAgentAccess, showAgentAccess = true,
}: {
  onChangeRole: () => void;
  onResetPw: () => void;
  onRevokeTokens: () => void;
  onDisable: () => void;
  onAgentAccess: () => void;
  showAgentAccess?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, right: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);

  const toggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
    }
    setOpen(v => !v);
  };

  return (
    <>
      <button
        ref={btnRef}
        onClick={toggle}
        className="text-gray-400 hover:text-gray-700 w-8 h-8 flex items-center justify-center rounded hover:bg-gray-100 text-lg leading-none ml-auto"
      >
        ···
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-xl py-1 w-44 text-sm"
            style={{ top: pos.top, right: pos.right }}
          >
            <button onClick={() => { setOpen(false); onChangeRole(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Change role
            </button>
            {showAgentAccess && (
              <button onClick={() => { setOpen(false); onAgentAccess(); }}
                className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
                Agent access
              </button>
            )}
            <button onClick={() => { setOpen(false); onResetPw(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Reset password
            </button>
            <button onClick={() => { setOpen(false); onRevokeTokens(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Revoke all tokens
            </button>
            <div className="my-1 border-t border-gray-100" />
            <button onClick={() => { setOpen(false); onDisable(); }}
              className="w-full text-left px-4 py-2 text-red-600 hover:bg-red-50 transition-colors">
              Disable user
            </button>
          </div>
        </>,
        document.body,
      )}
    </>
  );
}

function ResetPwModal({ user, onClose, onConfirm }: {
  user: TenantUser;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try { await onConfirm(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Reset password" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-amber-800 mb-1">A new temporary password will be generated</p>
          <p className="text-sm text-amber-700">
            <strong>@{user.username}</strong> will be required to change it on next login.
            Save the password - it is shown only once.
          </p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button onClick={submit} disabled={loading}
            className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors">
            {loading && <Spinner className="h-4 w-4" />}
            Reset password
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DisableUserModal({ user, onClose, onConfirm }: {
  user: TenantUser;
  onClose: () => void;
  onConfirm: (revokeTokens: boolean) => Promise<void>;
}) {
  const [input, setInput] = useState('');
  const [revokeTokens, setRevokeTokens] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const match = input === user.username;

  const submit = async () => {
    if (!match) return;
    setLoading(true); setError('');
    try { await onConfirm(revokeTokens); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Disable user" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-red-800 mb-1">Access will be revoked immediately</p>
          <p className="text-sm text-red-700">
            <strong>@{user.username}</strong> will lose all CLI and UI access right away.
          </p>
        </div>
        <label className="flex items-center gap-2.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={revokeTokens}
            onChange={e => setRevokeTokens(e.target.checked)}
            className="w-4 h-4 rounded border-gray-300 text-red-600 focus:ring-red-500"
          />
          <span className="text-sm text-gray-700">Also revoke all API tokens for this user</span>
        </label>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Type <code className="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded text-xs font-mono">{user.username}</code> to confirm
          </label>
          <input
            autoFocus
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && match && submit()}
            placeholder={user.username}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button onClick={submit} disabled={!match || loading}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors">
            {loading && <Spinner className="h-4 w-4" />}
            Disable user
          </button>
        </div>
      </div>
    </Modal>
  );
}

function EnableUserModal({ user, onClose, onConfirm }: {
  user: TenantUser;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try { await onConfirm(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Enable user" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-emerald-800 mb-1">Restore access</p>
          <p className="text-sm text-emerald-700">
            <strong>@{user.username}</strong> will be able to log in and use the CLI again.
            Their API tokens were not automatically restored - they will need to create new ones.
          </p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button onClick={submit} disabled={loading}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors">
            {loading && <Spinner className="h-4 w-4" />}
            Enable user
          </button>
        </div>
      </div>
    </Modal>
  );
}

function RevokeTokensModal({ user, onClose, onConfirm }: {
  user: TenantUser;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try { await onConfirm(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Revoke all tokens" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-amber-800 mb-1">Revoke API tokens</p>
          <p className="text-sm text-amber-700">
            All of <strong>@{user.username}</strong>'s API tokens stop working immediately - use this
            when a token may be compromised. The account stays active: they keep console access and
            can create new tokens. To block the account entirely, disable it instead.
          </p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button onClick={submit} disabled={loading}
            className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors">
            {loading && <Spinner className="h-4 w-4" />}
            Revoke all tokens
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DeleteUserModal({ user, onClose, onConfirm }: {
  user: TenantUser;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try { await onConfirm(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Delete user" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-red-800 mb-1">Permanently delete this user</p>
          <p className="text-sm text-red-700">
            <strong>@{user.username}</strong> and their API tokens will be permanently removed.
            This can't be undone. Past audit log entries are kept for the record.
          </p>
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1.5">
            Type <span className="font-mono font-semibold text-gray-800">{user.username}</span> to confirm
          </label>
          <input
            value={confirm}
            onChange={e => setConfirm(e.target.value)}
            placeholder={user.username}
            autoFocus
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-red-400 focus:border-transparent"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button onClick={submit} disabled={loading || confirm !== user.username}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors">
            {loading && <Spinner className="h-4 w-4" />}
            Delete user
          </button>
        </div>
      </div>
    </Modal>
  );
}

// A capability granted per agent/fleet. There is no wildcard - "all" is every id.
type Cap = 'none' | 'read' | 'write';
type ScopeItem = { id: string; label: string; sub?: string; badge?: string };

// One access section (agents or fleets): a per-item Read / Read-write picker, with
// quick "All read" / "All read-write" / "Clear" actions that materialize every id
// explicitly (no `*`). Maps directly to the (readwrite, readonly) id lists.
function ScopeSection({ title, items, caps, setCaps }: {
  title: string;
  items: ScopeItem[];
  caps: Map<string, Cap>;
  setCaps: (m: Map<string, Cap>) => void;
}) {
  const [search, setSearch] = useState('');
  const filtered = search.trim()
    ? items.filter(i => i.label.toLowerCase().includes(search.toLowerCase()) || i.id.toLowerCase().includes(search.toLowerCase()))
    : items;
  const setCap = (id: string, c: Cap) => { const n = new Map(caps); c === 'none' ? n.delete(id) : n.set(id, c); setCaps(n); };
  const setAll = (c: Cap) => {
    const n = new Map(caps);
    if (c === 'none') items.forEach(i => n.delete(i.id));
    else items.forEach(i => n.set(i.id, c));   // materialize every id explicitly
    setCaps(n);
  };
  const counts = { read: 0, write: 0 };
  items.forEach(i => { const c = caps.get(i.id); if (c === 'read') counts.read++; else if (c === 'write') counts.write++; });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{title}</span>
        <div className="inline-flex rounded-lg border border-gray-200 overflow-hidden text-[11px]">
          <button type="button" onClick={() => setAll('none')} className="px-2.5 py-1 font-medium bg-white text-gray-600 hover:bg-gray-50">Clear</button>
          <button type="button" onClick={() => setAll('read')} className="px-2.5 py-1 font-medium bg-white text-sky-600 hover:bg-sky-50 border-l border-gray-200">All read</button>
          <button type="button" onClick={() => setAll('write')} className="px-2.5 py-1 font-medium bg-white text-indigo-600 hover:bg-indigo-50 border-l border-gray-200">All read-write</button>
        </div>
      </div>

      <div className="border border-gray-200 rounded-lg overflow-hidden shadow-sm">
        <div className="px-3 py-2 bg-white border-b border-gray-100 flex items-center gap-2">
          <input type="text" placeholder={`Search ${title.toLowerCase()}…`} value={search}
            onChange={e => setSearch(e.target.value)}
            className="flex-1 text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent" />
          {(counts.read > 0 || counts.write > 0) && (
            <span className="text-[11px] text-gray-500">{counts.write} write · {counts.read} read</span>
          )}
        </div>
        <div className="max-h-52 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-6">{search ? 'No matches' : `No ${title.toLowerCase()}`}</p>
          ) : filtered.map(it => {
            const c = caps.get(it.id) ?? 'none';
            return (
              <div key={it.id} className="flex items-center gap-3 px-3 py-2 border-b border-gray-50 last:border-0">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-800 leading-tight truncate">{it.label}</p>
                  {it.sub && <p className="text-[10px] font-mono text-gray-400 leading-tight truncate">{it.sub}</p>}
                </div>
                {it.badge && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 bg-gray-100 text-gray-500">{it.badge}</span>}
                <div className="inline-flex rounded-md border border-gray-200 overflow-hidden text-[11px] shrink-0">
                  {(['none', 'read', 'write'] as Cap[]).map(opt => (
                    <button key={opt} type="button" onClick={() => setCap(it.id, opt)}
                      className={`px-2 py-1 font-medium transition-colors ${c === opt
                        ? (opt === 'write' ? 'bg-indigo-600 text-white' : opt === 'read' ? 'bg-sky-500 text-white' : 'bg-gray-400 text-white')
                        : 'bg-white text-gray-500 hover:bg-gray-50'}`}>
                      {opt === 'none' ? '-' : opt === 'read' ? 'Read' : 'R/W'}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// Parse (readwrite, readonly) id lists into per-item caps. No wildcard.
function parseCaps(rw: string[] | null, ro: string[] | null): Map<string, Cap> {
  const caps = new Map<string, Cap>();
  (ro ?? []).forEach(id => caps.set(id, 'read'));
  (rw ?? []).forEach(id => caps.set(id, 'write'));   // read-write wins if listed in both
  return caps;
}

// Serialize per-item caps back into explicit (readwrite, readonly) id lists.
function serializeCaps(caps: Map<string, Cap>): { rw: string[]; ro: string[] } {
  const rw: string[] = [], ro: string[] = [];
  caps.forEach((c, id) => { if (c === 'write') rw.push(id); else if (c === 'read') ro.push(id); });
  return { rw, ro };
}

const EMPTY_SCOPE: UserAccessScope = {
  readwrite_agent_ids: [], readonly_agent_ids: [], readwrite_fleet_ids: [], readonly_fleet_ids: [],
};

// The agents + fleets capability editor, shared by the Access modal and Create User.
// Reports the current scope via onChange; `initial` is read once at mount.
function ScopeEditor({ agents, fleets, initial, onChange }: {
  agents: Agent[]; fleets: Fleet[]; initial: UserAccessScope; onChange: (s: UserAccessScope) => void;
}) {
  const [agentCaps, setAgentCaps] = useState<Map<string, Cap>>(() => parseCaps(initial.readwrite_agent_ids, initial.readonly_agent_ids));
  const [fleetCaps, setFleetCaps] = useState<Map<string, Cap>>(() => parseCaps(initial.readwrite_fleet_ids, initial.readonly_fleet_ids));

  useEffect(() => {
    const a = serializeCaps(agentCaps);
    const f = serializeCaps(fleetCaps);
    onChange({ readwrite_agent_ids: a.rw, readonly_agent_ids: a.ro, readwrite_fleet_ids: f.rw, readonly_fleet_ids: f.ro });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentCaps, fleetCaps]);

  const noAccess = agentCaps.size === 0 && fleetCaps.size === 0;
  return (
    <>
      <p className="text-xs text-gray-500">
        Grant each agent/fleet <b>Read</b> (read commands + viewing) or <b>Read-write</b> (also write commands, still gated by the agent's mode). "All read / All read-write" grants every one explicitly. Fleet members are granted via the Fleets section, not by agent id.
      </p>
      <ScopeSection title="Agents" caps={agentCaps} setCaps={setAgentCaps}
        items={agents.filter(a => !a.fleet_id).map(a => ({ id: a.agent_id, label: a.hostname ?? '(unclaimed)', sub: a.agent_id, badge: a.status.toLowerCase() }))} />
      <ScopeSection title="Fleets" caps={fleetCaps} setCaps={setFleetCaps}
        items={fleets.map(f => ({ id: f.fleet_id, label: f.name, sub: f.fleet_id }))} />
      {noAccess && <p className="text-xs text-amber-600">This user will have <b>no agent access</b>.</p>}
    </>
  );
}

function AgentAccessModal({ apiUrl, tenantToken, user, onClose }: {
  apiUrl: string;
  tenantToken: string;
  user: TenantUser;
  onClose: () => void;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [fleets, setFleets] = useState<Fleet[]>([]);
  const [initial, setInitial] = useState<UserAccessScope | null>(null);
  const [scope, setScope] = useState<UserAccessScope>(EMPTY_SCOPE);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([
      listTenantAgents(apiUrl, tenantToken),
      listFleets(apiUrl, tenantToken).catch(() => ({ fleets: [] as Fleet[] })),
      getUserAgentAccess(apiUrl, tenantToken, user.user_id),
    ]).then(([agentsRes, fleetsRes, access]) => {
      setAgents(agentsRes.agents ?? []);
      setFleets(fleetsRes.fleets ?? []);
      setInitial({
        readwrite_agent_ids: access.readwrite_agent_ids, readonly_agent_ids: access.readonly_agent_ids,
        readwrite_fleet_ids: access.readwrite_fleet_ids, readonly_fleet_ids: access.readonly_fleet_ids,
      });
    }).catch(() => setError('Failed to load access data'))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken, user.user_id]);

  const save = async () => {
    setSaving(true); setError('');
    try {
      await setUserAgentAccess(apiUrl, tenantToken, user.user_id, scope);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`Access · @${user.username}`} onClose={onClose}>
      <div className="space-y-4">
        {loading || !initial ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : (
          <ScopeEditor agents={agents} fleets={fleets} initial={initial} onChange={setScope} />
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={save}
            disabled={loading || saving}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition-colors"
          >
            {saving && <Spinner className="h-4 w-4" />}
            Save access
          </button>
        </div>
      </div>
    </Modal>
  );
}
