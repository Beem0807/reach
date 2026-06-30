import { useState, useEffect, useCallback, useRef } from 'react';
import type { ApiToken, TenantConfig } from '../types';
import { listApiTokens, createApiToken, revokeApiToken, deleteApiToken, renameApiToken } from '../api';
import { Modal } from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { RefreshButton } from '../components/RefreshButton';
import { relTime } from '../utils';

export function TenantApiTokensPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newToken, setNewToken] = useState<ApiToken | null>(null);
  const [actionTarget, setActionTarget] = useState<ApiToken | null>(null);  // token to revoke or delete
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameLoading, setRenameLoading] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setLoading(true);
    listApiTokens(apiUrl, tenantToken).then(r => setTokens(r.tokens)).finally(() => setLoading(false));
  }, [apiUrl, tenantToken]);

  useEffect(() => { load(); }, [load]);

  // Step 1: revoke an ACTIVE token (soft) - it stays listed as REVOKED for audit.
  const doRevoke = async (t: ApiToken) => {
    await revokeApiToken(apiUrl, tenantToken, t.token_id);
    setActionTarget(null);
    setTokens(prev => prev.map(tok =>
      tok.token_id === t.token_id ? { ...tok, status: 'REVOKED', revoked_at: new Date().toISOString() } : tok));
  };

  // Step 2: hard-delete an already-REVOKED token - removes the record.
  const doDelete = async (t: ApiToken) => {
    await deleteApiToken(apiUrl, tenantToken, t.token_id);
    setActionTarget(null);
    setTokens(prev => prev.filter(tok => tok.token_id !== t.token_id));
  };

  const startRename = (t: ApiToken) => {
    setRenamingId(t.token_id);
    setRenameValue(t.name || '');
    setTimeout(() => renameInputRef.current?.select(), 0);
  };

  const cancelRename = () => { setRenamingId(null); setRenameValue(''); };

  const commitRename = async (tokenId: string) => {
    const name = renameValue.trim();
    if (!name) { cancelRename(); return; }
    setRenameLoading(true);
    try {
      await renameApiToken(apiUrl, tenantToken, tokenId, name);
      setTokens(prev => prev.map(t => t.token_id === tokenId ? { ...t, name } : t));
      cancelRename();
    } finally {
      setRenameLoading(false);
    }
  };

  const activeCount = tokens.filter(t => t.status === 'ACTIVE').length;

  return (
    <div className="min-h-full bg-slate-50">
      {/* Page header */}
      <div className="bg-gradient-to-r from-sky-700 to-sky-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">API Tokens</h1>
              <p className="text-sm text-sky-200">Tokens for CLI and MCP authentication</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {!loading && activeCount > 0 && (
              <span className="inline-flex items-center gap-1.5 bg-emerald-500/20 border border-emerald-400/30 text-emerald-300 text-xs font-semibold px-3 py-1.5 rounded-lg">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                {activeCount} active
              </span>
            )}
            <RefreshButton onClick={load} loading={loading} />
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1.5 bg-white text-sky-700 hover:bg-sky-50 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
            >
              <span className="text-base leading-none">+</span> Create token
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
      {loading && tokens.length === 0 ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <DataTable
            tableId="api-tokens"
            columns={[
              { label: 'Description', sortValue: t => t.name ?? '', required: true },
              { label: 'Status',    sortValue: t => t.status ?? '' },
              { label: 'Created',   sortValue: t => t.created_at ?? '' },
              { label: 'Last used', sortValue: t => t.last_used_at ?? '' },
              { label: 'Revoked',   sortValue: t => t.revoked_at ?? '' },
              { label: 'Token ID',  sortValue: t => t.token_id, defaultHidden: true },
              { label: '' },
            ]}
            rows={tokens}
            fallback="No API tokens - create one to use the CLI or MCP"
            renderRow={t => {
              const revoked = t.status === 'REVOKED';
              return (
              <tr key={t.token_id} className={`hover:bg-gray-50/70 group ${revoked ? 'text-gray-400' : ''}`}>
                <td className="px-4 py-3 font-medium text-gray-900">
                  {renamingId === t.token_id ? (
                    <div className="flex items-center gap-2">
                      <input
                        ref={renameInputRef}
                        value={renameValue}
                        onChange={e => setRenameValue(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') commitRename(t.token_id);
                          if (e.key === 'Escape') cancelRename();
                        }}
                        className="border border-indigo-400 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 w-48"
                        autoFocus
                      />
                      <button
                        onClick={() => commitRename(t.token_id)}
                        disabled={renameLoading}
                        className="text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 px-2.5 py-1 rounded-md disabled:opacity-50 transition-colors"
                      >
                        {renameLoading ? <Spinner className="h-3 w-3" /> : 'Save'}
                      </button>
                      <button onClick={cancelRename} className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className={revoked ? 'text-gray-500 line-through' : ''}>{t.name || '-'}</span>
                      {/* Rename only ACTIVE tokens - a revoked token is on its way out. */}
                      {!revoked && (
                        <button
                          onClick={() => startRename(t)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-indigo-600"
                          title="Rename"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
                          </svg>
                        </button>
                      )}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3.5">
                  {revoked ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 bg-gray-100 border border-gray-200 px-2 py-0.5 rounded-full">
                      <span className="w-1.5 h-1.5 rounded-full bg-gray-400" /> Revoked
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Active
                    </span>
                  )}
                </td>
                <td className="px-4 py-3.5 text-xs text-gray-500">{relTime(t.created_at)}</td>
                <td className="px-4 py-3.5 text-xs text-gray-500">{t.last_used_at ? relTime(t.last_used_at) : '-'}</td>
                <td className="px-4 py-3.5 text-xs text-gray-500">{t.revoked_at ? relTime(t.revoked_at) : '-'}</td>
                <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{t.token_id}</td>
                <td className="px-4 py-3.5 text-right">
                  {revoked ? (
                    <button
                      onClick={() => setActionTarget(t)}
                      className="text-xs font-semibold text-red-600 hover:text-white hover:bg-red-600 bg-red-50 border border-red-200 px-2.5 py-1 rounded-md transition-colors"
                    >
                      Delete
                    </button>
                  ) : (
                    <button
                      onClick={() => setActionTarget(t)}
                      className="text-xs font-semibold text-amber-700 hover:text-white hover:bg-amber-600 bg-amber-50 border border-amber-200 px-2.5 py-1 rounded-md transition-colors"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            );}}
          />
        </div>
      )}

      {showCreate && (
        <CreateTokenModal
          apiUrl={apiUrl}
          tenantToken={tenantToken}
          onClose={() => setShowCreate(false)}
          onCreated={t => { setNewToken(t); setShowCreate(false); load(); }}
        />
      )}

      {newToken && (
        <TokenRevealModal token={newToken} onClose={() => setNewToken(null)} />
      )}

      {actionTarget && (
        <TokenActionModal
          token={actionTarget}
          onClose={() => setActionTarget(null)}
          onConfirm={() => actionTarget.status === 'REVOKED' ? doDelete(actionTarget) : doRevoke(actionTarget)}
        />
      )}
      </div>
    </div>
  );
}

// One modal for both steps. Revoking an active token is a simple confirm (it is
// reversible in the sense that the record remains); permanently deleting a revoked
// token requires typing the name to confirm.
function TokenActionModal({ token, onClose, onConfirm }: { token: ApiToken; onClose: () => void; onConfirm: () => Promise<void> }) {
  const isDelete = token.status === 'REVOKED';
  const confirmKey = token.name || token.token_id;
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const match = !isDelete || input === confirmKey;

  const submit = async () => {
    if (!match) return;
    setLoading(true);
    try { await onConfirm(); } finally { setLoading(false); }
  };

  return (
    <Modal title={isDelete ? 'Delete API token' : 'Revoke API token'} onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          {isDelete ? (
            <>
              <p className="text-sm text-red-800 font-medium">This revoked token will be permanently deleted from your records.</p>
              <p className="text-xs text-red-700 mt-1">This cannot be undone. The audit-log entry for its revocation is kept.</p>
            </>
          ) : (
            <>
              <p className="text-sm text-red-800 font-medium">This token will be revoked and can no longer authenticate.</p>
              <p className="text-xs text-red-700 mt-1">Any CLI or MCP sessions using it lose access immediately. It stays listed as <span className="font-semibold">Revoked</span> (for audit) until you delete it. Revocation is permanent - issue a new token to restore access.</p>
            </>
          )}
        </div>
        {isDelete && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Type <span className="font-mono text-gray-900 bg-gray-100 px-1.5 py-0.5 rounded">{confirmKey}</span> to confirm
            </label>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && match && submit()}
              autoFocus
              placeholder={confirmKey}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
            />
          </div>
        )}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600 hover:text-gray-800 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={!match || loading}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-40 transition-colors"
          >
            {loading && <Spinner className="h-4 w-4" />} {isDelete ? 'Delete permanently' : 'Revoke token'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function CreateTokenModal({ apiUrl, tenantToken, onClose, onCreated }: {
  apiUrl: string; tenantToken: string;
  onClose: () => void; onCreated: (t: ApiToken) => void;
}) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError('Description is required.'); return; }
    setLoading(true); setError('');
    try {
      const t = await createApiToken(apiUrl, tenantToken, name.trim());
      onCreated(t);
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="Create API token" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <input value={name} onChange={e => setName(e.target.value)} autoFocus
            placeholder="e.g. My laptop, CI/CD pipeline, VS Code" className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          <p className="mt-1 text-xs text-gray-400">Helps you identify which token is which later.</p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button type="submit" disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Create
          </button>
        </div>
      </form>
    </Modal>
  );
}

function TokenRevealModal({ token, onClose }: { token: ApiToken; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard.writeText(token.token!); setCopied(true); setTimeout(() => setCopied(false), 2000); };
  return (
    <Modal title="Token created" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm text-amber-800 font-medium">Copy this token now - it won't be shown again.</p>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-1">Token</p>
          <div className="flex items-center gap-2">
            <code className="text-xs font-mono bg-gray-100 px-3 py-2 rounded flex-1 break-all">{token.token}</code>
            <button onClick={copy} className="text-xs text-indigo-600 hover:text-indigo-800 whitespace-nowrap shrink-0">
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-500">Use with: <code className="bg-gray-100 px-1.5 py-0.5 rounded">reach login --token &lt;token&gt;</code></p>
        <div className="flex justify-end pt-1">
          <button onClick={onClose} className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-md">Done</button>
        </div>
      </div>
    </Modal>
  );
}
