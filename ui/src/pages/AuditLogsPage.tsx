import { useState, useEffect, useCallback, useRef } from 'react';
import type { AuditLog } from '../types';
import { listPlatformAuditLogs, listTenantAuditLogs } from '../api';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { RefreshButton } from '../components/RefreshButton';

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

const ACTION_COLOR: Record<string, string> = {
  'admin.login': 'bg-gray-100 text-gray-600',
  'admin.login_failed': 'bg-red-50 text-red-700',
  'tenant.created': 'bg-emerald-50 text-emerald-700',
  'tenant.disabled': 'bg-red-50 text-red-700',
  'tenant.enabled': 'bg-emerald-50 text-emerald-700',
  'user.created': 'bg-indigo-50 text-indigo-700',
  'user.disabled': 'bg-red-50 text-red-700',
  'user.deleted': 'bg-red-50 text-red-700',
  'user.login': 'bg-gray-100 text-gray-600',
  'user.login_failed': 'bg-red-50 text-red-700',
  'user.password_changed': 'bg-amber-50 text-amber-700',
  'user.password_reset': 'bg-amber-50 text-amber-700',
  'user.role_changed': 'bg-purple-50 text-purple-700',
  'api_token.created': 'bg-indigo-50 text-indigo-700',
  'api_token.renamed': 'bg-purple-50 text-purple-700',
  'api_token.revoked': 'bg-red-50 text-red-700',
  'agent.created': 'bg-indigo-50 text-indigo-700',
  'agent.revoked': 'bg-red-50 text-red-700',
  'agent.deleted': 'bg-red-50 text-red-700',
  'agent.removed': 'bg-gray-100 text-gray-700',
  'agent.unreachable': 'bg-amber-50 text-amber-700',
  'agent.recovered': 'bg-emerald-50 text-emerald-700',
  'agent.install_token_reissued': 'bg-amber-50 text-amber-700',
  'agent.tags_changed': 'bg-purple-50 text-purple-700',
  'agent.rotation_requested': 'bg-amber-50 text-amber-700',
  'agent.capability_detected': 'bg-amber-50 text-amber-700',
  'agent.capability_acknowledged': 'bg-emerald-50 text-emerald-700',
  'agent.mode_changed': 'bg-purple-50 text-purple-700',
  'approval.requested': 'bg-amber-50 text-amber-700',
  'approval.approved': 'bg-emerald-50 text-emerald-700',
  'approval.denied': 'bg-red-50 text-red-700',
  'approval.pre_approved': 'bg-emerald-50 text-emerald-700',
  'approval.deleted': 'bg-gray-100 text-gray-700',
  'approval.expired': 'bg-gray-100 text-gray-600',
  'tenant.deleted': 'bg-red-50 text-red-700',
  'user.name_changed': 'bg-purple-50 text-purple-700',
  'user.agents_changed': 'bg-indigo-50 text-indigo-700',
  'user.enabled': 'bg-emerald-50 text-emerald-700',
};

function CapabilityDetail({ meta }: { meta: Record<string, unknown> }) {
  const label = String(meta.label ?? meta.capability ?? '');
  const detected = meta.detected as boolean;
  const outOfBand = meta.out_of_band as boolean;
  const prev = meta.previously_detected;

  const state = detected
    ? (outOfBand ? 'detected - not granted ⚠' : 'detected')
    : 'no longer detected';
  const prevLabel = prev === null || prev === undefined
    ? 'first report'
    : prev ? 'was detected' : 'was not detected';

  return (
    <span className="text-xs text-gray-600">
      <span className="font-medium text-gray-800">{label}</span>
      {' · '}
      <span className={outOfBand && detected ? 'text-amber-700 font-medium' : ''}>{state}</span>
      <span className="text-gray-400 ml-1">({prevLabel})</span>
    </span>
  );
}

function AgentsChangedDetail({ meta }: { meta: Record<string, unknown> }) {
  const username = meta.target_username ? <span className="font-medium text-gray-800">{String(meta.target_username)}</span> : null;
  const prev = meta.previous as string[] | null | undefined;
  const curr = meta.current as string[] | null | undefined;
  const added = meta.added as string[] | null | undefined;
  const removed = meta.removed as string[] | null | undefined;

  const badge = (ids: string[] | null | undefined, color: string) =>
    ids === null || ids === undefined
      ? <span className={`font-mono text-[10px] font-bold ${color}`} title="all agents">*</span>
      : ids.length === 0
      ? <span className="text-red-400 text-[10px] font-medium">no agents</span>
      : <span className={`font-mono text-[10px] ${color}`}>{ids.length} agent{ids.length !== 1 ? 's' : ''}</span>;

  let detail: React.ReactNode;
  if (added !== null && added !== undefined && removed !== null && removed !== undefined) {
    const parts: React.ReactNode[] = [];
    if (added.length > 0) parts.push(<span key="a" className="text-emerald-600">+{added.length}</span>);
    if (removed.length > 0) parts.push(<span key="r" className="text-red-500">−{removed.length}</span>);
    if (parts.length === 0) parts.push(<span key="nc" className="text-gray-400">no change</span>);
    detail = <span className="space-x-1">{parts}</span>;
  } else {
    detail = <span>{badge(prev, 'text-gray-500')} <span className="text-gray-400 mx-0.5">→</span> {badge(curr, 'text-indigo-600')}</span>;
  }

  return (
    <span className="text-xs text-gray-600">
      {username}{username && <span className="text-gray-400 mx-1">·</span>}{detail}
    </span>
  );
}

function ModeChangeDetail({ meta }: { meta: Record<string, unknown> }) {
  const from = String(meta.from_mode ?? '?');
  const to = String(meta.to_mode ?? '?');
  const host = meta.hostname ? <span className="text-gray-400 ml-1">on {String(meta.hostname)}</span> : null;
  return (
    <span className="text-xs text-gray-600">
      <span className="font-mono bg-gray-100 px-1 py-0.5 rounded text-gray-700">{from}</span>
      <span className="mx-1 text-gray-400">→</span>
      <span className="font-mono bg-purple-50 px-1 py-0.5 rounded text-purple-700">{to}</span>
      {host}
    </span>
  );
}

function AcknowledgeDetail({ meta }: { meta: Record<string, unknown> }) {
  const label = String(meta.label ?? meta.capability ?? '');
  const host = meta.hostname ? <span className="text-gray-400 ml-1">on {String(meta.hostname)}</span> : null;
  return (
    <span className="text-xs text-gray-600">
      <span className="font-medium text-gray-800">{label}</span>
      <span className="ml-1 text-emerald-600 font-medium">acknowledged</span>
      {host}
    </span>
  );
}

function ApprovalDetail({ action, meta }: { action: string; meta: Record<string, unknown> }) {
  const command = meta.command as string | undefined;
  const commands = meta.commands as string[] | undefined;
  const count = typeof meta.count === 'number' ? meta.count : commands?.length;
  const agentId = meta.agent_id as string | undefined;
  const expiresAt = meta.expires_at as string | null | undefined;
  const status = meta.status as string | undefined;

  const cmdNode = command !== undefined && command !== null
    ? <span className="font-mono bg-gray-100 px-1 py-0.5 rounded text-gray-700 break-all">{command}</span>
    : typeof count === 'number'
    ? <span className="text-gray-700">{count} command{count !== 1 ? 's' : ''}</span>
    : null;

  return (
    <span className="text-xs text-gray-600">
      {cmdNode}
      {agentId && <span className="text-gray-400 ml-1">on <span className="font-mono">{agentId}</span></span>}
      {action === 'approval.approved' && (expiresAt
        ? <span className="text-gray-400 ml-1">· until {fmtDate(expiresAt)}</span>
        : <span className="text-gray-400 ml-1">· permanent</span>)}
      {action === 'approval.deleted' && status && <span className="text-gray-400 ml-1">· was {status}</span>}
    </span>
  );
}

interface Props {
  mode: 'platform' | 'tenant';
  apiUrl: string;
  token: string;
}

export function AuditLogsPage({ mode, apiUrl, token }: Props) {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState('');
  const cursorRef = useRef<string | undefined>(undefined);
  const seqRef = useRef(0);

  const [filterAction, setFilterAction] = useState('');
  const [filterActor, setFilterActor] = useState('');
  const [filterResource, setFilterResource] = useState('');
  const [filterIp, setFilterIp] = useState('');
  const [filterSince, setFilterSince] = useState('');
  const [filterUntil, setFilterUntil] = useState('');
  const filterRefs = useRef({ action: '', actor: '', resource: '', ip: '', since: '', until: '' });

  const activeFilters = [filterAction, filterActor, filterResource, filterIp, filterSince, filterUntil].filter(Boolean).length;

  const load = useCallback((reset = true) => {
    const seq = ++seqRef.current;
    setLoading(true); setError('');
    const params: Record<string, string> = { limit: '20' };
    if (!reset && cursorRef.current) {
      params.cursor = cursorRef.current;
    } else {
      cursorRef.current = undefined;
    }
    const f = filterRefs.current;
    if (f.since) params.since = `${f.since}T00:00:00Z`;
    if (f.until) params.until = `${f.until}T23:59:59Z`;
    if (f.action) params.action = f.action;
    if (f.actor) params.actor = f.actor;
    if (f.resource) params.resource = f.resource;
    if (f.ip) params.ip = f.ip;
    const fn = mode === 'platform'
      ? listPlatformAuditLogs(apiUrl, token, params)
      : listTenantAuditLogs(apiUrl, token, params);
    fn
      .then(r => {
        if (seqRef.current !== seq) return;
        setLogs(prev => reset ? r.logs : [...prev, ...r.logs]);
        cursorRef.current = r.next_cursor;
        setHasMore(!!r.next_cursor);
      })
      .catch(() => { if (seqRef.current === seq) setError('Failed to load audit logs'); })
      .finally(() => { if (seqRef.current === seq) setLoading(false); });
  }, [apiUrl, token, mode]);

  useEffect(() => { load(true); }, [load]);

  // Filters are staged in input state and only sent when the user hits Search
  // (the default view is the recent page). Enter in any text box also searches.
  function applyFilters() {
    filterRefs.current = {
      action: filterAction, actor: filterActor, resource: filterResource,
      ip: filterIp, since: filterSince, until: filterUntil,
    };
    load(true);
  }

  function clearFilters() {
    setFilterAction(''); setFilterActor(''); setFilterResource(''); setFilterIp('');
    setFilterSince(''); setFilterUntil('');
    filterRefs.current = { action: '', actor: '', resource: '', ip: '', since: '', until: '' };
    load(true);
  }

  return (
    <div className="min-h-full bg-slate-50">
      {/* Page header */}
      <div className="bg-gradient-to-r from-orange-700 to-orange-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V19.5a2.25 2.25 0 002.25 2.25h.75" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Audit Logs</h1>
              <p className="text-sm text-orange-200">
                {mode === 'platform' ? 'All platform-level events' : 'Events within your tenant'}
              </p>
            </div>
          </div>
          <RefreshButton onClick={() => load(true)} loading={loading} />
        </div>
      </div>

      <div className="px-8 py-6">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4">{error}</div>
      )}

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select
          value={filterAction}
          onChange={e => setFilterAction(e.target.value)}
          className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400 bg-white w-52"
        >
          <option value="">All actions</option>
          {Object.keys(ACTION_COLOR).map(a => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <input
          value={filterActor}
          onChange={e => setFilterActor(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()}
          placeholder="Actor…"
          className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400 w-36"
        />
        <input
          value={filterResource}
          onChange={e => setFilterResource(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()}
          placeholder="Resource…"
          className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400 w-44"
        />
        <input
          value={filterIp}
          onChange={e => setFilterIp(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()}
          placeholder="IP…"
          className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400 w-32"
        />
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            value={filterSince}
            onChange={e => setFilterSince(e.target.value)}
            title="From date"
            className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
          <span className="text-gray-400 text-xs">to</span>
          <input
            type="date"
            value={filterUntil}
            onChange={e => setFilterUntil(e.target.value)}
            title="To date"
            className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
        </div>
        <button
          onClick={applyFilters}
          className="text-sm font-semibold px-3 py-1.5 rounded-lg bg-orange-600 text-white hover:bg-orange-700 transition-colors shadow-sm"
        >
          Search
        </button>
        {activeFilters > 0 && (
          <button
            onClick={clearFilters}
            className="text-xs text-red-500 hover:text-red-700 font-medium"
          >
            Clear filters
          </button>
        )}
      </div>

      {loading && logs.length === 0 ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : (
        <>
          <div className={`bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden relative ${loading ? 'opacity-60 pointer-events-none' : ''}`}>
            {loading && <div className="absolute inset-0 flex items-center justify-center z-10"><Spinner /></div>}
            <DataTable
              tableId="audit-logs"
              columns={[
                { label: 'Time',     sortValue: l => l.created_at, required: true },
                { label: 'Action',   sortValue: l => l.action, required: true },
                { label: 'Details',  sortValue: l => String((l.metadata as Record<string, unknown>)?.label ?? '') },
                { label: 'Actor',    sortValue: l => l.actor_name ?? l.actor_id ?? '' },
                { label: 'Resource', sortValue: l => l.resource_type ?? '' },
                { label: 'IP',       sortValue: l => l.ip_address ?? '' },
              ]}
              rows={logs}
              fallback="No audit events yet"
              renderRow={l => (
                <tr key={l.log_id} className="hover:bg-gray-50/70">
                  <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{fmtDate(l.created_at)}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ACTION_COLOR[l.action] || 'bg-gray-100 text-gray-600'}`}>
                      {l.action}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {l.action === 'agent.capability_detected' && l.metadata
                      ? <CapabilityDetail meta={l.metadata as Record<string, unknown>} />
                      : l.action === 'agent.capability_acknowledged' && l.metadata
                      ? <AcknowledgeDetail meta={l.metadata as Record<string, unknown>} />
                      : l.action === 'agent.mode_changed' && l.metadata
                      ? <ModeChangeDetail meta={l.metadata as Record<string, unknown>} />
                      : l.action === 'user.agents_changed' && l.metadata
                      ? <AgentsChangedDetail meta={l.metadata as Record<string, unknown>} />
                      : l.action.startsWith('approval.') && l.metadata
                      ? <ApprovalDetail action={l.action} meta={l.metadata as Record<string, unknown>} />
                      : <span className="text-xs text-gray-400">-</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-700">
                    <span className="font-medium">{l.actor_name || l.actor_id || '-'}</span>
                    {l.actor_role && (
                      <span className="ml-1.5 text-gray-400">({l.actor_role.replace('_', ' ').toLowerCase()})</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs font-mono text-gray-500 max-w-[220px]">
                    <div title={l.resource_type && l.resource_id ? `${l.resource_type}/${l.resource_id}` : l.resource_id ?? ''}>
                      {l.resource_type && <span className="text-gray-400">{l.resource_type}/</span>}
                      <span className="break-all">{l.resource_id ?? '-'}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400">{l.ip_address || '-'}</td>
                </tr>
              )}
            />
          </div>
          {hasMore && (
            <div className="mt-4 text-center">
              <button onClick={() => load(false)} disabled={loading}
                className="text-sm text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
                {loading ? 'Loading…' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}
      </div>
    </div>
  );
}
