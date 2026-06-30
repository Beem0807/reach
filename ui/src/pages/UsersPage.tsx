import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { Config, Tenant, User, TenantRole } from '../types';
import {
  listTenants, listUsers, createTenantAdminUser,
  platformResetUserPassword, platformDisableUser,
  platformSetUserRole, platformUpdateUserName,
} from '../api';
import { Modal } from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { RefreshButton } from '../components/RefreshButton';
import { relTime } from '../utils';

const ROLES: TenantRole[] = ['admin', 'operator', 'developer'];

const ROLE_STYLE: Record<TenantRole, string> = {
  admin:    'bg-indigo-100 text-indigo-700',
  operator: 'bg-amber-100 text-amber-700',
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

const USERNAME_RE = /^[a-z0-9]+$/;

type ModalType = 'create' | 'creds' | 'role' | 'rename' | null;

export function UsersPage({ config }: { config: Config }) {
  const { apiUrl, adminToken } = config;
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState('');
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<ModalType>(null);
  const [targetUser, setTargetUser] = useState<User | null>(null);
  const [createdCreds, setCreatedCreds] = useState<{ username: string; temp_password: string } | null>(null);
  const [resetPwTarget, setResetPwTarget] = useState<User | null>(null);
  const [disableTarget, setDisableTarget] = useState<User | null>(null);

  useEffect(() => {
    listTenants(apiUrl, adminToken)
      .then(r => {
        setTenants(r.tenants);
        if (r.tenants.length > 0) setTenantId(r.tenants[0].tenant_id);
      })
      .catch(() => setError('Failed to load tenants'));
  }, [apiUrl, adminToken]);

  const loadUsers = useCallback(() => {
    if (!tenantId) return;
    setLoading(true);
    listUsers(apiUrl, adminToken, tenantId)
      .then(r => setUsers(r.users))
      .catch(() => setError('Failed to load users'))
      .finally(() => setLoading(false));
  }, [apiUrl, adminToken, tenantId]);

  useEffect(() => { setUsers([]); loadUsers(); }, [loadUsers]);

  const handleResetPw = async (u: User) => {
    const r = await platformResetUserPassword(apiUrl, adminToken, tenantId, u.user_id);
    setResetPwTarget(null);
    setCreatedCreds({ username: u.username ?? u.user_id, temp_password: r.temp_password });
    setModal('creds');
  };

  const handleDisable = async (u: User) => {
    await platformDisableUser(apiUrl, adminToken, tenantId, u.user_id);
    setDisableTarget(null);
    loadUsers();
  };

  const handleSetRole = async (u: User, role: TenantRole) => {
    try {
      await platformSetUserRole(apiUrl, adminToken, tenantId, u.user_id, role);
      loadUsers();
    } catch (e) { alert((e as Error).message); }
  };

  const handleUpdateName = async (u: User, name: string) => {
    try {
      await platformUpdateUserName(apiUrl, adminToken, tenantId, u.user_id, name);
      loadUsers();
    } catch (e) { alert((e as Error).message); }
  };

  const selectedTenant = tenants.find(t => t.tenant_id === tenantId);

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">Users</h1>
          <p className="text-sm text-gray-500 mt-0.5">Manage tenant users</p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={tenantId}
            onChange={e => setTenantId(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white shadow-sm"
          >
            {tenants.map(t => (
              <option key={t.tenant_id} value={t.tenant_id}>{t.name} ({t.tenant_id})</option>
            ))}
          </select>
          <RefreshButton onClick={loadUsers} loading={loading} variant="onLight" />
          <button
            onClick={() => setModal('create')}
            disabled={!tenantId || selectedTenant?.status === 'DISABLED'}
            title={selectedTenant?.status === 'DISABLED' ? 'Cannot add users to a disabled tenant' : undefined}
            className="inline-flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
          >
            <span className="text-base leading-none">+</span> Add user
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4">
          <span className="shrink-0">⚠</span>{error}
        </div>
      )}

      {selectedTenant && (
        <div className="flex items-center gap-2 mb-5">
          <span className="text-sm font-semibold text-gray-800">{selectedTenant.name}</span>
          <span className="font-mono text-xs text-gray-400">{selectedTenant.tenant_id}</span>
          {selectedTenant.status === 'DISABLED' && (
            <span className="text-[10px] font-semibold bg-red-100 text-red-600 px-2 py-0.5 rounded-full">DISABLED</span>
          )}
        </div>
      )}

      {!tenantId ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <p className="text-sm text-gray-400">Select a tenant above to manage users.</p>
        </div>
      ) : loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <DataTable
            columns={[
              { label: 'Name',       sortValue: u => u.name ?? '' },
              { label: 'Username',   sortValue: u => u.username ?? '' },
              { label: 'Role',       sortValue: u => u.role ?? '' },
              { label: 'Status',     sortValue: u => u.status ?? '' },
              { label: 'Last login', sortValue: u => u.last_login_at ?? '' },
              { label: 'Created',    sortValue: u => u.created_at ?? '' },
              { label: '' },
            ]}
            rows={users}
            fallback={
              <>
                <p>No users in this tenant yet</p>
                {selectedTenant?.status !== 'DISABLED' && (
                  <button onClick={() => setModal('create')} className="mt-2 text-indigo-600 hover:text-indigo-800 text-sm font-medium">
                    Add the first user →
                  </button>
                )}
              </>
            }
            renderRow={u => (
              <tr key={u.user_id} className={`hover:bg-gray-50/70 transition-colors ${u.status === 'REVOKED' ? 'opacity-60' : ''}`}>
                <td className="px-4 py-3.5 font-medium text-gray-800">
                  {u.name || u.username}
                  {u.must_reset_password && (
                    u.last_login_at
                      ? <span className="ml-2 text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-semibold" title="Temporary password issued - pending change">TEMP PW</span>
                      : <span className="ml-2 text-[10px] bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded font-semibold" title="Created but has never logged in">INVITED</span>
                  )}
                </td>
                <td className="px-4 py-3.5 font-mono text-xs text-gray-500">
                  {u.username ? `@${u.username}` : '-'}
                </td>
                <td className="px-4 py-3.5">
                  {u.role ? (
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ROLE_STYLE[u.role as TenantRole] ?? 'bg-gray-100 text-gray-600'}`}>
                      {ROLE_LABEL[u.role as TenantRole] ?? u.role}
                    </span>
                  ) : '-'}
                </td>
                <td className="px-4 py-3.5">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${u.status === 'REVOKED' ? 'bg-red-100 text-red-600' : 'bg-emerald-100 text-emerald-700'}`}>
                    {u.status === 'REVOKED' ? 'disabled' : 'active'}
                  </span>
                </td>
                <td className="px-4 py-3.5 text-xs text-gray-500">{relTime(u.last_login_at)}</td>
                <td className="px-4 py-3.5 text-xs text-gray-500">{relTime(u.created_at)}</td>
                <td className="px-4 py-3.5">
                  {u.status !== 'REVOKED' && (
                    <UserMenu
                      onResetPw={() => setResetPwTarget(u)}
                      onDisable={() => setDisableTarget(u)}
                      onChangeRole={() => { setTargetUser(u); setModal('role'); }}
                      onRename={() => { setTargetUser(u); setModal('rename'); }}
                    />
                  )}
                </td>
              </tr>
            )}
          />
        </div>
      )}

      {modal === 'create' && tenantId && (
        <CreateUserModal
          apiUrl={apiUrl}
          adminToken={adminToken}
          tenantId={tenantId}
          onClose={() => setModal(null)}
          onCreated={(creds) => { setCreatedCreds(creds); setModal('creds'); loadUsers(); }}
        />
      )}

      {modal === 'creds' && createdCreds && (
        <CredentialsModal creds={createdCreds} onClose={() => { setModal(null); setCreatedCreds(null); }} />
      )}

      {modal === 'role' && targetUser && (
        <ChangeRoleModal
          user={targetUser}
          onClose={() => { setModal(null); setTargetUser(null); }}
          onSave={async (role) => { await handleSetRole(targetUser, role); setModal(null); setTargetUser(null); }}
        />
      )}

      {modal === 'rename' && targetUser && (
        <RenameModal
          user={targetUser}
          onClose={() => { setModal(null); setTargetUser(null); }}
          onSave={async (name) => { await handleUpdateName(targetUser, name); setModal(null); setTargetUser(null); }}
        />
      )}

      {resetPwTarget && (
        <ResetPwModal
          user={resetPwTarget}
          onClose={() => setResetPwTarget(null)}
          onConfirm={() => handleResetPw(resetPwTarget)}
        />
      )}

      {disableTarget && (
        <DisableUserModal
          user={disableTarget}
          onClose={() => setDisableTarget(null)}
          onConfirm={() => handleDisable(disableTarget)}
        />
      )}
    </div>
  );
}

function UserMenu({
  onResetPw, onDisable, onChangeRole, onRename,
}: {
  onResetPw: () => void;
  onDisable: () => void;
  onChangeRole: () => void;
  onRename: () => void;
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
            className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-xl py-1 w-48 text-sm"
            style={{ top: pos.top, right: pos.right }}
          >
            <button onClick={() => { setOpen(false); onRename(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Update name
            </button>
            <button onClick={() => { setOpen(false); onChangeRole(); }}
              className="w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-50 transition-colors">
              Change role
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

function CreateUserModal({
  apiUrl, adminToken, tenantId, onClose, onCreated,
}: {
  apiUrl: string;
  adminToken: string;
  tenantId: string;
  onClose: () => void;
  onCreated: (c: { username: string; temp_password: string }) => void;
}) {
  const [name, setName] = useState('');
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<TenantRole>('developer');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const usernameError = username && !USERNAME_RE.test(username)
    ? 'Only lowercase letters and numbers allowed'
    : '';

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError('Name is required'); return; }
    if (!username) { setError('Username is required'); return; }
    if (!USERNAME_RE.test(username)) { setError('Username must contain only lowercase letters and numbers'); return; }
    setLoading(true); setError('');
    try {
      const r = await createTenantAdminUser(apiUrl, adminToken, tenantId, { username, name: name.trim(), role });
      onCreated({ username: r.username ?? username, temp_password: r.temp_password! });
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Add user" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Full name</label>
          <input autoFocus value={name} onChange={e => setName(e.target.value)}
            placeholder="Alice Smith"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
          <input value={username} onChange={e => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9]/g, ''))}
            placeholder="alice123"
            className={`w-full border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${usernameError ? 'border-red-400' : 'border-gray-300'}`} />
          {usernameError && <p className="text-xs text-red-500 mt-1">{usernameError}</p>}
          <p className="text-xs text-gray-400 mt-1">Lowercase letters and numbers only</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Role</label>
          <div className="space-y-2">
            {ROLES.map(r => (
              <button key={r} type="button" onClick={() => setRole(r)}
                className={`w-full text-left px-3 py-2.5 rounded-md border transition-colors ${role === r ? 'bg-indigo-50 border-indigo-400 text-indigo-900' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}>
                <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${ROLE_STYLE[r]}`}>{ROLE_LABEL[r]}</span>
                <span className="text-xs text-gray-500 ml-2">{ROLE_DESC[r]}</span>
              </button>
            ))}
          </div>
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
  user, onClose, onSave,
}: {
  user: User;
  onClose: () => void;
  onSave: (role: TenantRole) => Promise<void>;
}) {
  const [role, setRole] = useState<TenantRole>((user.role as TenantRole) ?? 'developer');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError('');
    try { await onSave(role); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={`Change role - ${user.username ? `@${user.username}` : user.name}`} onClose={onClose}>
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

function RenameModal({
  user, onClose, onSave,
}: {
  user: User;
  onClose: () => void;
  onSave: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(user.name);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || name.trim() === user.name) { onClose(); return; }
    setLoading(true); setError('');
    try { await onSave(name.trim()); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Update name" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Display name</label>
          <input autoFocus value={name} onChange={e => setName(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button type="submit" disabled={loading || !name.trim()}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Save
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ResetPwModal({ user, onClose, onConfirm }: { user: User; onClose: () => void; onConfirm: () => Promise<void> }) {
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
            <strong>{user.username ? `@${user.username}` : user.name}</strong> will need to change it on next login.
            Save the temporary password - it will only be shown once.
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

function DisableUserModal({ user, onClose, onConfirm }: { user: User; onClose: () => void; onConfirm: () => Promise<void> }) {
  const confirmKey = user.username ?? user.user_id;
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const match = input === confirmKey;

  const submit = async () => {
    if (!match) return;
    setLoading(true); setError('');
    try { await onConfirm(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Disable user" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm font-semibold text-red-800 mb-1">Access will be revoked immediately</p>
          <p className="text-sm text-red-700">
            <strong>{user.username ? `@${user.username}` : user.name}</strong> will lose all CLI and UI access right away.
          </p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Type <code className="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded text-xs font-mono">{confirmKey}</code> to confirm
          </label>
          <input
            autoFocus
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && match && submit()}
            placeholder={confirmKey}
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

function CredentialsModal({ creds, onClose }: { creds: { username: string; temp_password: string }; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(creds.temp_password);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <Modal title="User created" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm text-amber-800 font-medium">Save this temporary password now.</p>
          <p className="text-xs text-amber-700 mt-0.5">It will not be shown again. The user must change it on first login.</p>
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
