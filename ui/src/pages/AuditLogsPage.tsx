import { useState, useEffect, useCallback, useRef } from 'react';
import type { AuditLog, Tenant } from '../types';
import { listPlatformAuditLogs, listTenantAuditLogs, listTenants } from '../api';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { RefreshButton } from '../components/RefreshButton';
import { Modal } from '../components/Modal';

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// A `datetime-local` value ("YYYY-MM-DDTHH:MM", local time) -> UTC ISO for the API. Empty
// (or a bare date, from an older filter) is handled: a bare date becomes the day's start.
function toUtcIso(local: string): string | undefined {
  if (!local) return undefined;
  const v = local.includes('T') ? local : `${local}T00:00`;   // tolerate a plain date
  const d = new Date(v);
  return isNaN(d.getTime()) ? undefined : d.toISOString();
}

// CSV export columns (metadata is JSON-encoded into a single cell). tenant_id is only
// included in platform mode (a tenant admin's export is scoped to their own tenant).
const CSV_COLUMNS: { key: keyof AuditLog; header: string }[] = [
  { key: 'created_at', header: 'timestamp' },
  { key: 'action', header: 'action' },
  { key: 'actor_name', header: 'actor_name' },
  { key: 'actor_role', header: 'actor_role' },
  { key: 'actor_id', header: 'actor_id' },
  { key: 'resource_type', header: 'resource_type' },
  { key: 'resource_id', header: 'resource_id' },
  { key: 'ip_address', header: 'ip_address' },
  { key: 'tenant_id', header: 'tenant_id' },
  { key: 'log_id', header: 'log_id' },
  { key: 'metadata', header: 'metadata' },
];

// Every cell is quoted and embedded quotes are doubled, so commas/newlines/quotes in
// metadata can never break the columns. A leading =/+/-/@ is prefixed with a quote to
// defang spreadsheet formula injection.
function csvCell(v: unknown): string {
  let s = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
  if (/^[=+\-@]/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

function logsToCsv(logs: AuditLog[], includeTenant: boolean): string {
  const cols = CSV_COLUMNS.filter(c => c.key !== 'tenant_id' || includeTenant);
  const header = cols.map(c => c.header).join(',');
  const rows = logs.map(l => cols.map(c => csvCell(l[c.key])).join(','));
  return [header, ...rows].join('\r\n');
}

const ACTION_COLOR: Record<string, string> = {
  'admin.login': 'bg-gray-100 text-gray-600',
  'admin.login_failed': 'bg-red-50 text-red-700',
  'tenant.created': 'bg-emerald-50 text-emerald-700',
  'tenant.disabled': 'bg-red-50 text-red-700',
  'tenant.enabled': 'bg-emerald-50 text-emerald-700',
  'tenant.settings_updated': 'bg-purple-50 text-purple-700',
  'tenant.settings_overridden': 'bg-amber-50 text-amber-700',
  'run.dispatched': 'bg-blue-50 text-blue-700',
  'job.dispatched': 'bg-blue-50 text-blue-700',
  'run.paused': 'bg-amber-50 text-amber-700',
  'run.resumed': 'bg-emerald-50 text-emerald-700',
  'run.canceled': 'bg-red-50 text-red-700',
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
  'api_token.deleted': 'bg-red-50 text-red-700',
  'fleet.created': 'bg-emerald-50 text-emerald-700',
  'fleet.updated': 'bg-purple-50 text-purple-700',
  'fleet.token_rotated': 'bg-amber-50 text-amber-700',
  'fleet.member_detached': 'bg-gray-100 text-gray-700',
  'fleet.grants_reconciled': 'bg-amber-50 text-amber-700',
  'fleet.grant_mismatch_accepted': 'bg-amber-50 text-amber-700',
  'fleet.revoked': 'bg-red-50 text-red-700',
  'fleet.deleted': 'bg-red-50 text-red-700',
  'agent.created': 'bg-indigo-50 text-indigo-700',
  'agent.revoked': 'bg-red-50 text-red-700',
  'agent.deleted': 'bg-red-50 text-red-700',
  'agent.removed': 'bg-gray-100 text-gray-700',
  'agent.unreachable': 'bg-amber-50 text-amber-700',
  'agent.reaped': 'bg-red-50 text-red-700',
  'agent.deregistered': 'bg-gray-100 text-gray-700',
  'agent.recovered': 'bg-emerald-50 text-emerald-700',
  'agent.install_token_reissued': 'bg-amber-50 text-amber-700',
  'agent.tags_changed': 'bg-purple-50 text-purple-700',
  'agent.rotation_requested': 'bg-amber-50 text-amber-700',
  'agent.capability_detected': 'bg-amber-50 text-amber-700',
  'agent.capability_acknowledged': 'bg-emerald-50 text-emerald-700',
  'agent.sandbox_acknowledged': 'bg-amber-50 text-amber-700',
  'agent.sandbox_ack_revoked': 'bg-emerald-50 text-emerald-700',
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

function RunDispatchDetail({ meta }: { meta: Record<string, unknown> }) {
  const command = String(meta.command ?? '');
  const scope = String(meta.scope ?? '');
  const target = scope === 'fleet' ? String(meta.fleet_name ?? meta.fleet_id ?? '') : String(meta.tag ?? '');
  const dispatched = typeof meta.dispatched === 'number' ? meta.dispatched : undefined;
  const waves = typeof meta.wave_total === 'number' ? meta.wave_total : undefined;
  const isWrite = meta.is_write === true;
  return (
    <span className="text-xs text-gray-600">
      {command && <span className="font-mono bg-gray-100 px-1 py-0.5 rounded text-gray-700 break-all">{command}</span>}
      {target && <span className="text-gray-400 ml-1">on {scope === 'fleet' ? 'fleet' : 'tag'} <span className="font-medium text-gray-700">{target}</span></span>}
      {dispatched !== undefined && <span className="text-gray-400 ml-1">· {dispatched} agent{dispatched !== 1 ? 's' : ''}</span>}
      {waves !== undefined && waves > 1 && <span className="text-gray-400 ml-1">· {waves} waves</span>}
      {isWrite && <span className="ml-1 text-amber-600 font-medium">write</span>}
    </span>
  );
}

function JobDispatchDetail({ meta }: { meta: Record<string, unknown> }) {
  const command = String(meta.command ?? '');
  const target = String(meta.hostname ?? meta.agent_id ?? '');
  const isWrite = meta.is_write === true;
  return (
    <span className="text-xs text-gray-600">
      {command && <span className="font-mono bg-gray-100 px-1 py-0.5 rounded text-gray-700 break-all">{command}</span>}
      {target && <span className="text-gray-400 ml-1">on <span className="font-medium text-gray-700">{target}</span></span>}
      {isWrite && <span className="ml-1 text-amber-600 font-medium">write</span>}
    </span>
  );
}

// Fallback detail renderer: any action that carries metadata but has no bespoke
// renderer above still shows a compact key: value summary instead of a bare "-".
function GenericDetail({ meta }: { meta: Record<string, unknown> }) {
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined) return '∅';
    if (Array.isArray(v)) return v.length ? v.join(', ') : '(none)';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  };
  const entries = Object.entries(meta).filter(([, v]) => v !== '' && v !== undefined);
  if (entries.length === 0) return <span className="text-xs text-gray-400">-</span>;
  return (
    <span className="text-xs text-gray-600 space-x-2">
      {entries.slice(0, 4).map(([k, v]) => (
        <span key={k} className="whitespace-nowrap">
          <span className="text-gray-400">{k}:</span>{' '}
          <span className="font-medium text-gray-700 break-all">{fmt(v)}</span>
        </span>
      ))}
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
  const [filterTenant, setFilterTenant] = useState('');
  // Platform mode only: the tenant list backs the tenant filter dropdown.
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [filterSince, setFilterSince] = useState('');
  const [filterUntil, setFilterUntil] = useState('');
  const filterRefs = useRef({ action: '', actor: '', resource: '', ip: '', tenant: '', since: '', until: '' });

  const activeFilters = [filterAction, filterActor, filterResource, filterIp, filterTenant, filterSince, filterUntil].filter(Boolean).length;

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
    const since = toUtcIso(f.since); if (since) params.since = since;
    const until = toUtcIso(f.until); if (until) params.until = until;
    if (f.action) params.action = f.action;
    if (f.actor) params.actor = f.actor;
    if (f.resource) params.resource = f.resource;
    if (f.ip) params.ip = f.ip;
    if (f.tenant) params.tenant = f.tenant;
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

  // Populate the tenant filter dropdown (platform mode). One shot; a high limit pulls
  // the full list so every tenant with audit activity is selectable.
  useEffect(() => {
    if (mode !== 'platform') return;
    listTenants(apiUrl, token, { limit: '1000' })
      .then(r => setTenants(r.tenants))
      .catch(() => setTenants([]));
  }, [mode, apiUrl, token]);

  // Filters are staged in input state and only sent when the user hits Search
  // (the default view is the recent page). Enter in any text box also searches.
  function applyFilters() {
    filterRefs.current = {
      action: filterAction, actor: filterActor, resource: filterResource,
      ip: filterIp, tenant: filterTenant, since: filterSince, until: filterUntil,
    };
    load(true);
  }

  function clearFilters() {
    setFilterAction(''); setFilterActor(''); setFilterResource(''); setFilterIp('');
    setFilterTenant(''); setFilterSince(''); setFilterUntil('');
    filterRefs.current = { action: '', actor: '', resource: '', ip: '', tenant: '', since: '', until: '' };
    load(true);
  }

  // Export opens a modal to pick a date range, then downloads the whole range as CSV.
  const [showExport, setShowExport] = useState(false);

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
          <div className="flex items-center gap-2.5">
            <button
              onClick={() => setShowExport(true)}
              title="Choose a date range and download it as CSV"
              className="inline-flex items-center gap-2 rounded-lg bg-white/10 hover:bg-white/20 ring-1 ring-white/20 text-white text-sm font-medium px-3 py-1.5 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>
              Export CSV
            </button>
            <RefreshButton onClick={() => load(true)} loading={loading} />
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4">{error}</div>
      )}

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {mode === 'platform' && (
          <select
            aria-label="Filter by tenant"
            value={filterTenant}
            onChange={e => setFilterTenant(e.target.value)}
            className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400 bg-white w-48"
          >
            <option value="">All tenants</option>
            {tenants.map(t => (
              <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>
            ))}
          </select>
        )}
        <select
          aria-label="Filter by action"
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
            type="datetime-local"
            value={filterSince}
            max={filterUntil || undefined}
            onChange={e => setFilterSince(e.target.value)}
            title="From date & time (local)"
            className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
          <span className="text-gray-400 text-xs">to</span>
          <input
            type="datetime-local"
            value={filterUntil}
            min={filterSince || undefined}
            onChange={e => setFilterUntil(e.target.value)}
            title="To date & time (local)"
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
                ...(mode === 'platform'
                  ? [{ label: 'Tenant', sortValue: (l: AuditLog) => l.tenant_id ?? '' }]
                  : []),
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
                  {mode === 'platform' && (
                    <td className="px-4 py-3 text-xs font-mono text-gray-500 whitespace-nowrap">
                      {l.tenant_id
                        ? <span className="break-all">{l.tenant_id}</span>
                        : <span className="text-gray-400 italic">platform</span>}
                    </td>
                  )}
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
                      : l.action === 'run.dispatched' && l.metadata
                      ? <RunDispatchDetail meta={l.metadata as Record<string, unknown>} />
                      : l.action === 'job.dispatched' && l.metadata
                      ? <JobDispatchDetail meta={l.metadata as Record<string, unknown>} />
                      : l.metadata && Object.keys(l.metadata).length > 0
                      ? <GenericDetail meta={l.metadata as Record<string, unknown>} />
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

      {showExport && (
        <ExportModal
          mode={mode}
          apiUrl={apiUrl}
          token={token}
          filters={filterRefs.current}
          onClose={() => setShowExport(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Export modal: pick a date range, download the whole range as CSV (no row cap).
// ---------------------------------------------------------------------------

interface ExportFilters { action: string; actor: string; resource: string; ip: string; tenant: string; since: string; until: string; }

function ExportModal({ mode, apiUrl, token, filters, onClose }: {
  mode: 'tenant' | 'platform';
  apiUrl: string;
  token: string;
  filters: ExportFilters;
  onClose: () => void;
}) {
  // Pre-fill the range from any date filter already applied on the page.
  const [from, setFrom] = useState(filters.since);
  const [to, setTo] = useState(filters.until);
  const [busy, setBusy] = useState(false);
  const [fetched, setFetched] = useState(0);
  const [error, setError] = useState('');

  const otherFilters = [
    filters.action && `action: ${filters.action}`,
    filters.actor && `actor: ${filters.actor}`,
    filters.resource && `resource: ${filters.resource}`,
    filters.ip && `ip: ${filters.ip}`,
    filters.tenant && `tenant: ${filters.tenant}`,
  ].filter(Boolean) as string[];

  const doExport = async () => {
    if (from && to && new Date(from) > new Date(to)) { setError('The "from" time must be on or before the "to" time.'); return; }
    setBusy(true); setError(''); setFetched(0);
    try {
      const base: Record<string, string> = { limit: '200' };
      const since = toUtcIso(from); if (since) base.since = since;
      const until = toUtcIso(to); if (until) base.until = until;
      if (filters.action) base.action = filters.action;
      if (filters.actor) base.actor = filters.actor;
      if (filters.resource) base.resource = filters.resource;
      if (filters.ip) base.ip = filters.ip;
      if (filters.tenant) base.tenant = filters.tenant;

      // Page through the ENTIRE range - no row cap. Guard only against a non-advancing
      // cursor so a broken backend can't spin forever.
      const all: AuditLog[] = [];
      let cursor: string | undefined;
      let first = true;
      const seen = new Set<string>();
      while (first || cursor) {
        first = false;
        if (cursor) { if (seen.has(cursor)) break; seen.add(cursor); }
        const params = cursor ? { ...base, cursor } : base;
        const r = mode === 'platform'
          ? await listPlatformAuditLogs(apiUrl, token, params)
          : await listTenantAuditLogs(apiUrl, token, params);
        all.push(...r.logs);
        setFetched(all.length);
        cursor = r.next_cursor;
      }

      if (all.length === 0) { setError('No audit logs match this range - nothing to export.'); return; }

      const csv = logsToCsv(all, mode === 'platform');
      const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });  // BOM for Excel
      const stamp = new Date().toISOString().slice(0, 10);
      const safe = (s: string) => s.replace('T', '_').replace(/:/g, '');   // filename-safe datetime
      const range = (from || to) ? `_${from ? safe(from) : 'start'}-to-${to ? safe(to) : 'now'}` : '_all';
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `reach-audit-logs-${mode}${range}_${stamp}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      onClose();
    } catch {
      setError('Failed to export audit logs. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const field = 'w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/40 focus:border-orange-500';

  return (
    <Modal title="Export audit logs" onClose={busy ? () => {} : onClose}>
      <div className="space-y-4">
        <p className="text-sm text-slate-500">
          Choose a start and end date &amp; time. Every matching event in that range is downloaded
          as a single CSV - there is no row limit, so a wide range may take a moment.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs font-semibold text-slate-600">From</span>
            <input type="datetime-local" value={from} max={to || undefined} onChange={e => setFrom(e.target.value)} disabled={busy} className={field} />
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-slate-600">To</span>
            <input type="datetime-local" value={to} min={from || undefined} onChange={e => setTo(e.target.value)} disabled={busy} className={field} />
          </label>
        </div>
        <p className="text-[11px] text-slate-400">Leave both blank to export the entire history. Times are in your local timezone.</p>
        {otherFilters.length > 0 && (
          <p className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
            The filters applied on the page also apply to this export - {otherFilters.join(', ')}.
          </p>
        )}
        {busy && <p className="text-xs text-slate-500 flex items-center gap-2"><Spinner className="w-3.5 h-3.5" /> Fetched {fetched.toLocaleString()} rows…</p>}
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} disabled={busy} className="text-sm text-slate-500 hover:text-slate-700 px-3 py-2 disabled:opacity-50">Cancel</button>
          <button
            onClick={doExport}
            disabled={busy}
            className="inline-flex items-center gap-2 bg-orange-600 hover:bg-orange-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {busy && <Spinner className="w-4 h-4" />}
            {busy ? 'Exporting…' : 'Download CSV'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
