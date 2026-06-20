import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { TenantConfig, TenantUser, TenantRole, Agent } from '../types';
import {
  listTenantUsers, createTenantUser, disableTenantUser, enableTenantUser, revokeAllUserTokens,
  setTenantUserRole, resetTenantUserPassword,
  getUserAgentAccess, setUserAgentAccess, listTenantAgents,
} from '../api';
import { Modal } from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
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

export function TenantUsersPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken, role } = config;
  const isAdmin = role === 'admin';

  const [users, setUsers] = useState<TenantUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<'create' | 'role' | 'creds' | null>(null);
  const [targetUser, setTargetUser] = useState<TenantUser | null>(null);
  const [createdCreds, setCreatedCreds] = useState<{ username: string; temp_password: string } | null>(null);
  const [resetPwTarget, setResetPwTarget] = useState<TenantUser | null>(null);
  const [disableTarget, setDisableTarget] = useState<TenantUser | null>(null);
  const [enableTarget, setEnableTarget] = useState<TenantUser | null>(null);
  const [agentAccessTarget, setAgentAccessTarget] = useState<TenantUser | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listTenantUsers(apiUrl, tenantToken)
      .then(r => setUsers(r.users))
      .catch(() => setError('Failed to load users'))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken]);

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
            {!loading && users.length > 0 && (
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
                        onClick={() => (isAdmin && u.user_id !== config.userId && u.status !== 'REVOKED') ? setAgentAccessTarget(u) : undefined}
                        className={`font-medium text-sm text-gray-900 text-left leading-tight ${(isAdmin && u.user_id !== config.userId && u.status !== 'REVOKED') ? 'hover:text-violet-700 transition-colors cursor-pointer' : 'cursor-default'}`}
                      >
                        {u.name || u.username}
                      </button>
                      <div className="mt-0.5">
                        {u.allowed_agent_ids == null
                          ? <span className="text-[11px] text-gray-400 font-mono" title="all agents">* all agents</span>
                          : u.allowed_agent_ids.length === 0
                          ? <span className="text-[11px] font-semibold text-red-500">no agents</span>
                          : <span className="text-[11px] font-semibold text-indigo-500">{u.allowed_agent_ids.length} agent{u.allowed_agent_ids.length !== 1 ? 's' : ''}</span>
                        }
                      </div>
                    </div>
                    {u.must_reset_password && (
                      <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-semibold shrink-0 mt-0.5">TEMP PW</span>
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
                      <button
                        onClick={() => setEnableTarget(u)}
                        className="text-xs font-semibold text-emerald-700 hover:text-white hover:bg-emerald-600 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-md transition-colors"
                      >
                        Enable
                      </button>
                    ) : (
                      <TenantUserMenu
                        onChangeRole={() => { setTargetUser(u); setModal('role'); }}
                        onResetPw={() => setResetPwTarget(u)}
                        onDisable={() => setDisableTarget(u)}
                        onAgentAccess={() => setAgentAccessTarget(u)}
                      />
                    )
                  )}
                </td>
              </tr>
            )}
          />
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
          onChanged={() => { load(); setModal(null); setTargetUser(null); }}
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
  const [restricted, setRestricted] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [agentSearch, setAgentSearch] = useState('');

  useEffect(() => {
    listTenantAgents(apiUrl, tenantToken)
      .then(r => setAgents((r.agents ?? []).filter(a => a.status === 'ACTIVE')))
      .catch(() => {/* non-fatal: agent picker stays empty */});
  }, [apiUrl, tenantToken]);

  const filteredAgents = agentSearch.trim()
    ? agents.filter(a =>
        (a.hostname ?? '').toLowerCase().includes(agentSearch.toLowerCase()) ||
        a.agent_id.toLowerCase().includes(agentSearch.toLowerCase()))
    : agents;

  const toggleAgent = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

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
      const allowed_agent_ids = restricted ? [...selected] : null;
      const r = await createTenantUser(apiUrl, tenantToken, { username, name, role, allowed_agent_ids });
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

        {/* Agent access */}
        <div>
          <div className="flex items-center gap-2 mb-2.5">
            <span className="text-sm font-medium text-gray-700">Agent access</span>
            {!restricted
              ? <span className="text-xs bg-gray-100 text-gray-500 font-mono px-1.5 py-0.5 rounded">* all agents</span>
              : selected.size === 0
              ? <span className="text-xs bg-red-100 text-red-600 font-semibold px-1.5 py-0.5 rounded">no agents</span>
              : <span className="text-xs bg-indigo-100 text-indigo-700 font-semibold px-1.5 py-0.5 rounded">{selected.size} selected</span>
            }
          </div>
          <button
            type="button"
            onClick={() => setRestricted(v => !v)}
            className="flex items-center gap-2.5 mb-3 group"
          >
            <div className={`relative w-9 h-5 rounded-full transition-colors ${restricted ? 'bg-indigo-600' : 'bg-gray-300 group-hover:bg-gray-400'}`}>
              <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all duration-150 ${restricted ? 'left-4' : 'left-0.5'}`} />
            </div>
            <span className="text-sm text-gray-600 group-hover:text-gray-800 select-none">Restrict to specific agents</span>
          </button>
          {restricted && (
            <div className="border border-gray-200 rounded-lg overflow-hidden shadow-sm">
              <div className="px-3 py-2 bg-white border-b border-gray-100 flex items-center gap-2">
                <svg className="w-3.5 h-3.5 text-gray-400 shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
                </svg>
                <input
                  type="text"
                  placeholder="Search agents…"
                  value={agentSearch}
                  onChange={e => setAgentSearch(e.target.value)}
                  className="flex-1 text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent"
                />
                {agentSearch && (
                  <button type="button" onClick={() => setAgentSearch('')} className="text-gray-400 hover:text-gray-600 leading-none">✕</button>
                )}
              </div>
              <div className="flex items-center justify-between px-3 py-1.5 bg-gray-50 border-b border-gray-100">
                <span className="text-[11px] text-gray-500">
                  {selected.size} of {agents.length} selected
                  {agentSearch && filteredAgents.length !== agents.length && ` · ${filteredAgents.length} shown`}
                </span>
                {selected.size > 0 && (
                  <button type="button" onClick={() => setSelected(new Set())} className="text-[11px] text-gray-400 hover:text-red-500">clear all</button>
                )}
              </div>
              <div className="max-h-44 overflow-y-auto">
                {filteredAgents.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-5">{agentSearch ? 'No agents match' : 'No active agents in this tenant'}</p>
                ) : filteredAgents.map(a => {
                  const isSel = selected.has(a.agent_id);
                  return (
                    <label key={a.agent_id}
                      className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer border-b border-gray-50 last:border-0 transition-colors ${isSel ? 'bg-indigo-50 hover:bg-indigo-100/70' : 'hover:bg-gray-50'}`}>
                      <input type="checkbox" checked={isSel} onChange={() => toggleAgent(a.agent_id)} className="sr-only" />
                      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors ${isSel ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300'}`}>
                        {isSel && (
                          <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={3} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                          </svg>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-gray-800 leading-tight">{a.hostname ?? '(unclaimed)'}</p>
                        <p className="text-[10px] font-mono text-gray-400 leading-tight">{a.agent_id}</p>
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
        </div>

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
  onChanged: () => void;
}) {
  const [role, setRole] = useState<TenantRole>((user.role as TenantRole) ?? 'developer');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError('');
    try {
      await setTenantUserRole(apiUrl, tenantToken, user.user_id, role);
      onChanged();
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={`Change role - @${user.username}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          {ROLES.map(r => (
            <button key={r} type="button" onClick={() => setRole(r)}
              className={`w-full text-left px-3 py-2.5 rounded-md border transition-colors ${role === r ? 'bg-indigo-50 border-indigo-400 text-indigo-900' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}>
              <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${ROLE_STYLE[r]}`}>{ROLE_LABEL[r]}</span>
              <span className="text-xs text-gray-500 ml-2">{ROLE_DESC[r]}</span>
            </button>
          ))}
        </div>
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
  onChangeRole, onResetPw, onDisable, onAgentAccess,
}: {
  onChangeRole: () => void;
  onResetPw: () => void;
  onDisable: () => void;
  onAgentAccess: () => void;
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
            <button onClick={() => { setOpen(false); onAgentAccess(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Agent access
            </button>
            <button onClick={() => { setOpen(false); onResetPw(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Reset password
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

function AgentAccessModal({ apiUrl, tenantToken, user, onClose }: {
  apiUrl: string;
  tenantToken: string;
  user: TenantUser;
  onClose: () => void;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [restricted, setRestricted] = useState<boolean>(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    Promise.all([
      listTenantAgents(apiUrl, tenantToken),
      getUserAgentAccess(apiUrl, tenantToken, user.user_id),
    ]).then(([agentsRes, accessRes]) => {
      setAgents(agentsRes.agents ?? []);
      const ids = accessRes.allowed_agent_ids;
      if (ids !== null && ids !== undefined) {
        setRestricted(true);
        setSelected(new Set(ids));
      } else {
        setRestricted(false);
        setSelected(new Set());
      }
    }).catch(() => setError('Failed to load agent access data'))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken, user.user_id]);

  const filteredAgents = search.trim()
    ? agents.filter(a =>
        (a.hostname ?? '').toLowerCase().includes(search.toLowerCase()) ||
        a.agent_id.toLowerCase().includes(search.toLowerCase()))
    : agents;

  const toggle = (agentId: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(agentId) ? next.delete(agentId) : next.add(agentId);
      return next;
    });
  };

  const save = async () => {
    setSaving(true); setError('');
    try {
      await setUserAgentAccess(apiUrl, tenantToken, user.user_id, restricted ? [...selected] : null);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`Agent access · @${user.username}`} onClose={onClose}>
      <div className="space-y-4">
        {loading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setRestricted(v => !v)}
                className="flex items-center gap-2.5 group"
              >
                <div className={`relative w-9 h-5 rounded-full transition-colors ${restricted ? 'bg-indigo-600' : 'bg-gray-300 group-hover:bg-gray-400'}`}>
                  <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all duration-150 ${restricted ? 'left-4' : 'left-0.5'}`} />
                </div>
                <span className="text-sm text-gray-700 group-hover:text-gray-900 select-none">Restrict to specific agents</span>
              </button>
              <div className="ml-auto">
                {!restricted
                  ? <span className="text-xs bg-gray-100 text-gray-500 font-mono px-1.5 py-0.5 rounded">* all agents</span>
                  : selected.size === 0
                  ? <span className="text-xs bg-red-100 text-red-600 font-semibold px-1.5 py-0.5 rounded">no agents</span>
                  : <span className="text-xs bg-indigo-100 text-indigo-700 font-semibold px-1.5 py-0.5 rounded">{selected.size} selected</span>
                }
              </div>
            </div>

            {restricted && (
              <div className="border border-gray-200 rounded-lg overflow-hidden shadow-sm">
                <div className="px-3 py-2 bg-white border-b border-gray-100 flex items-center gap-2">
                  <svg className="w-3.5 h-3.5 text-gray-400 shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
                  </svg>
                  <input
                    type="text"
                    placeholder="Search agents…"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    className="flex-1 text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent"
                  />
                  {search && (
                    <button type="button" onClick={() => setSearch('')} className="text-gray-400 hover:text-gray-600 leading-none">✕</button>
                  )}
                </div>
                <div className="flex items-center justify-between px-3 py-1.5 bg-gray-50 border-b border-gray-100">
                  <span className="text-[11px] text-gray-500">
                    {selected.size} of {agents.length} selected
                    {search && filteredAgents.length !== agents.length && ` · ${filteredAgents.length} shown`}
                  </span>
                  {selected.size > 0 && (
                    <button onClick={() => setSelected(new Set())} className="text-[11px] text-gray-400 hover:text-red-500">clear all</button>
                  )}
                </div>
                <div className="max-h-56 overflow-y-auto">
                  {filteredAgents.length === 0 ? (
                    <p className="text-sm text-gray-400 text-center py-6">{search ? 'No agents match' : 'No agents in this tenant'}</p>
                  ) : filteredAgents.map(a => {
                    const isSel = selected.has(a.agent_id);
                    return (
                      <label key={a.agent_id}
                        className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer border-b border-gray-50 last:border-0 transition-colors ${isSel ? 'bg-indigo-50 hover:bg-indigo-100/70' : 'hover:bg-gray-50'}`}>
                        <input type="checkbox" checked={isSel} onChange={() => toggle(a.agent_id)} className="sr-only" />
                        <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors ${isSel ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300'}`}>
                          {isSel && (
                            <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={3} stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                            </svg>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-gray-800 leading-tight">{a.hostname ?? '(unclaimed)'}</p>
                          <p className="text-[10px] font-mono text-gray-400 leading-tight">{a.agent_id}</p>
                        </div>
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 ${a.status === 'ACTIVE' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
                          {a.status.toLowerCase()}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </>
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
