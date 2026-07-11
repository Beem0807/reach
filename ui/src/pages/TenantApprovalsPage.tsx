import { useState, useEffect, useCallback, useRef } from 'react';
import type { TenantConfig, Approval, Agent, Fleet, K8sRule } from '../types';
import {
  listAllTenantApprovals, listTenantApprovals, approveTenantApproval, denyTenantApproval,
  deleteTenantApproval, tenantPreApprove, tenantPreApproveFleet, tenantPreApproveRule,
  listTenantAgents, listFleets,
} from '../api';
import { Modal } from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { RefreshButton } from '../components/RefreshButton';
import { CopyButton } from '../components/CopyButton';
import { ApprovalTarget, ApprovalScope } from '../components/ApprovalTarget';
import { K8sRuleForm, EMPTY_RULE } from '../components/K8sRuleForm';


function useColumnResize(count: number) {
  const [minWidths, setMinWidths] = useState<number[]>(() => Array(count).fill(0));
  const dragging = useRef<{ col: number; startX: number; startW: number } | null>(null);
  const onResizeStart = useCallback((e: React.MouseEvent, col: number) => {
    e.preventDefault();
    const th = (e.currentTarget as HTMLElement).closest('th') as HTMLElement;
    dragging.current = { col, startX: e.clientX, startW: th.offsetWidth };
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const { col: c, startX, startW: sw } = dragging.current;
      setMinWidths(ws => { const n = [...ws]; n[c] = Math.max(60, sw + ev.clientX - startX); return n; });
    };
    const onUp = () => {
      dragging.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, []);
  return { minWidths, onResizeStart };
}

type StatusTab = 'pending' | 'approved' | 'denied' | 'expired';

const DURATIONS = ['permanent', '1h', '8h', '24h', '7d', '30d', '90d'] as const;
type Duration = typeof DURATIONS[number];

const PAGE_SIZE = 10;

// Prev/next pager shown below a table. `total` is the full count for the current
// kind; page is 0-based. Hidden when everything fits on one page.
function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  const pageCount = Math.ceil(total / PAGE_SIZE);
  if (pageCount <= 1) return null;
  const from = page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);
  const btn = 'px-2.5 py-1 text-sm rounded-md border border-gray-300 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors';
  return (
    <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 bg-gray-50/50">
      <span className="text-xs text-gray-500">Showing {from}–{to} of {total}</span>
      <div className="flex items-center gap-2">
        <button className={btn} onClick={() => onPage(page - 1)} disabled={page <= 0}>Prev</button>
        <span className="text-xs text-gray-500">Page {page + 1} of {pageCount}</span>
        <button className={btn} onClick={() => onPage(page + 1)} disabled={page >= pageCount - 1}>Next</button>
      </div>
    </div>
  );
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function fmtExpiry(iso?: string) {
  if (!iso) return 'permanent';
  const d = new Date(iso);
  if (d.getTime() < Date.now()) return 'expired';
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

const STATUS_STYLE: Record<string, string> = {
  pending:  'bg-amber-50 text-amber-700 border border-amber-200',
  approved: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
  denied:   'bg-red-50 text-red-700 border border-red-200',
  expired:  'bg-gray-100 text-gray-500 border border-gray-200',
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold capitalize ${STATUS_STYLE[status] ?? 'bg-gray-100 text-gray-500'}`}>
      {status}
    </span>
  );
}

// An approval targets a standalone agent or a whole fleet. This picks which set
// the page is looking at (the status tabs + filters below apply within it).
type ApprovalScopeKind = 'agent' | 'fleet';
function ScopeToggle({ value, onChange }: { value: ApprovalScopeKind; onChange: (v: ApprovalScopeKind) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-gray-300 bg-white shadow-sm overflow-hidden">
      {([['agent', 'Agents'], ['fleet', 'Fleets']] as const).map(([k, label]) => (
        <button
          key={k}
          onClick={() => onChange(k)}
          className={`px-3 py-1.5 text-sm font-medium transition-colors ${
            value === k ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function TenantApprovalsPage({ config }: { config: TenantConfig }) {
  const isOperator = config.role === 'admin' || config.role === 'operator';

  return isOperator
    ? <OperatorApprovalsView config={config} />
    : <DeveloperApprovalsView config={config} />;
}

// ---------------------------------------------------------------------------
// Developer view - my pending requests + request button
// ---------------------------------------------------------------------------

function DeveloperApprovalsView({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;
  const { minWidths: dw, onResizeStart: dr } = useColumnResize(4);
  const devTh = (label: string, i: number) => (
    <th key={label} style={dw[i] ? { minWidth: dw[i] } : undefined} className="relative text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">
      {label}
      <div onMouseDown={e => dr(e, i)} className="absolute right-0 top-0 h-full w-1 cursor-col-resize hover:bg-indigo-300 transition-colors" />
    </th>
  );
  const [agents, setAgents] = useState<Agent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [total, setTotal] = useState(0);
  const [kindFilter, setKindFilter] = useState<'k8s' | 'host'>('host');
  const [agentFilter, setAgentFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<'pending' | 'approved'>('pending');
  const [search, setSearch] = useState('');            // input box
  const [appliedSearch, setAppliedSearch] = useState(''); // last submitted via Search
  const [pages, setPages] = useState<{ k8s: number; host: number }>({ k8s: 0, host: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  // Kind, search, and paging all run server-side; default view is the 10 most
  // recent. `approvals` is the current server page. Each kind keeps its own page.
  const page = pages[kindFilter];
  const setPage = (p: number) => setPages(prev => ({ ...prev, [kindFilter]: p }));
  const applySearch = () => { setAppliedSearch(search.trim()); setPage(0); };
  const loadSeqRef = useRef(0);

  useEffect(() => {
    listTenantAgents(apiUrl, tenantToken)
      .then(r => setAgents(r.agents ?? []))
      .catch(() => {});
  }, [apiUrl, tenantToken]);

  const load = useCallback(() => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setError('');
    const params: Record<string, string> = {
      type: kindFilter,
      status: statusFilter,
      limit: String(PAGE_SIZE),
      offset: String(page * PAGE_SIZE),
    };
    if (agentFilter) params.agent_id = agentFilter;
    if (appliedSearch) params.q = appliedSearch;
    listTenantApprovals(apiUrl, tenantToken, params)
      .then(r => {
        if (loadSeqRef.current !== seq) return;
        setApprovals(r.approvals ?? []);
        setTotal(r.total ?? (r.approvals ?? []).length);
      })
      .catch(() => { if (loadSeqRef.current === seq) setError('Failed to load your approval requests'); })
      .finally(() => { if (loadSeqRef.current === seq) setLoading(false); });
  }, [apiUrl, tenantToken, kindFilter, statusFilter, agentFilter, appliedSearch, page]);

  useEffect(() => { load(); }, [load]);

  const handleSubmit = async (agentId: string, payload: { command?: string; rule?: K8sRule }) => {
    if (payload.rule) await tenantPreApproveRule(apiUrl, tenantToken, agentId, payload.rule);
    else await tenantPreApprove(apiUrl, tenantToken, agentId, payload.command ?? '');
    setShowModal(false);
    load();
  };

  return (
    <div className="min-h-full bg-slate-50">
      <div className="bg-gradient-to-r from-indigo-700 to-indigo-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">My Approval Requests</h1>
              <p className="text-sm text-indigo-200">Your pending requests, and approved commands on your agents</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <RefreshButton onClick={load} loading={loading} />
            <button
              onClick={() => setShowModal(true)}
              className="inline-flex items-center gap-1.5 bg-white text-indigo-700 hover:bg-indigo-50 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
            >
              <span className="text-base leading-none">+</span> Request approval
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-4">
        {error && (
          <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
            <span className="shrink-0">⚠</span> {error}
          </div>
        )}

        <div className="flex items-center gap-3 flex-wrap">
          <div className="inline-flex rounded-lg border border-gray-300 bg-white shadow-sm overflow-hidden">
            {(['pending', 'approved'] as const).map(s => (
              <button
                key={s}
                onClick={() => { setStatusFilter(s); setPage(0); }}
                className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                  statusFilter === s ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {s === 'pending' ? 'My pending' : 'Approved'}
              </button>
            ))}
          </div>
          <select
            value={agentFilter}
            onChange={e => {
              const id = e.target.value;
              setAgentFilter(id);
              setPage(0);
              // An agent is a single type, so selecting one locks the Host/Kubernetes
              // toggle to that agent's type; "All agents" frees it again.
              const a = agents.find(x => x.agent_id === id);
              if (a?.type) setKindFilter(a.type === 'k8s' ? 'k8s' : 'host');
            }}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white shadow-sm"
          >
            <option value="">All agents</option>
            {/* Fleet members have no per-agent approvals - manage them under Fleets. */}
            {agents.filter(a => !a.fleet_id).map(a => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.hostname ?? a.agent_id}{a.type ? ` (${a.type})` : ''}
              </option>
            ))}
          </select>
          <div className={`inline-flex rounded-lg border border-gray-300 bg-white shadow-sm overflow-hidden ${agentFilter ? 'opacity-60' : ''}`}>
            {(['k8s', 'host'] as const).map(k => (
              <button
                key={k}
                disabled={!!agentFilter}
                title={agentFilter ? 'Locked to the selected agent’s type - choose “All agents” to switch' : undefined}
                onClick={() => { if (agentFilter) return; setKindFilter(k); setPage(0); }}
                className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                  kindFilter === k ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'
                } ${agentFilter ? 'cursor-not-allowed' : ''}`}
              >
                {k === 'k8s' ? 'Kubernetes' : 'Host'}
              </button>
            ))}
          </div>
          <div className="relative">
            <svg className="w-4 h-4 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applySearch()}
              placeholder={kindFilter === 'k8s' ? 'Search verb, resource, namespace…' : 'Search command, agent…'}
              className="w-64 border border-gray-300 rounded-lg pl-8 pr-7 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent shadow-sm"
            />
            {search && (
              <button onClick={() => { setSearch(''); if (appliedSearch) { setAppliedSearch(''); setPage(0); } }} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-sm">✕</button>
            )}
          </div>
          <button
            onClick={applySearch}
            className="text-sm font-semibold px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors shadow-sm"
          >
            Search
          </button>
          {appliedSearch && <span className="text-xs text-gray-500">Filtered by “{appliedSearch}”</span>}
          {agentFilter && (
            <button onClick={() => { setAgentFilter(''); setPage(0); }} className="text-sm text-indigo-600 hover:text-indigo-800">
              Clear agent filter
            </button>
          )}
        </div>

        {loading ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50">
                  {devTh('Command / rule', 0)}
                  {devTh('Agent', 1)}
                  {devTh('Requested', 2)}
                  {devTh('Status', 3)}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {approvals.length === 0 ? (
                  <tr>
                    <td colSpan={99}>
                      <div className="flex flex-col items-center py-16 text-center">
                        <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mb-3">
                          <svg className="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                          </svg>
                        </div>
                        <p className="text-sm font-medium text-gray-500">
                          {statusFilter === 'approved'
                            ? (appliedSearch
                              ? `No approved ${kindFilter === 'k8s' ? 'Kubernetes' : 'host'} commands match “${appliedSearch}”`
                              : agentFilter
                              ? `No approved ${kindFilter === 'k8s' ? 'Kubernetes' : 'host'} commands for this agent`
                              : `No approved ${kindFilter === 'k8s' ? 'Kubernetes' : 'host'} commands on your agents`)
                            : (appliedSearch
                              ? `No pending ${kindFilter === 'k8s' ? 'Kubernetes' : 'host'} requests match “${appliedSearch}”`
                              : `No pending ${kindFilter === 'k8s' ? 'Kubernetes' : 'host'} requests`)}
                        </p>
                        {statusFilter === 'pending' && !appliedSearch && (
                        <p className="text-xs text-gray-400 mt-1">
                          Use{' '}
                          <button onClick={() => setShowModal(true)} className="text-indigo-600 hover:underline">
                            Request approval
                          </button>{' '}
                          to ask an operator to permit a command.
                        </p>
                        )}
                      </div>
                    </td>
                  </tr>
                ) : approvals.map(a => {
                  return (
                  <tr key={a.approval_id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-4 py-3.5 max-w-[360px]">
                      <ApprovalTarget approval={a} />
                    </td>
                    <td className="px-4 py-3.5">
                      <ApprovalScope approval={a} />
                    </td>
                    <td className="px-4 py-3.5 text-sm text-gray-500 whitespace-nowrap">
                      {fmtDate(a.created_at)}
                    </td>
                    <td className="px-4 py-3.5">
                      <StatusBadge status={a.status} />
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
            <Pager page={page} total={total} onPage={setPage} />
          </div>
        )}
      </div>

      {showModal && (
        <RequestApprovalModal
          agents={agents}
          onClose={() => setShowModal(false)}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Operator/Admin view - full management
// ---------------------------------------------------------------------------

function OperatorApprovalsView({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;
  const canAdd = true; // operator+
  const { minWidths: ow, onResizeStart: or_ } = useColumnResize(10);
  const opTh = (label: string, i: number) => (
    <th key={label} style={ow[i] ? { minWidth: ow[i] } : undefined} className="relative text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">
      {label}
      <div onMouseDown={e => or_(e, i)} className="absolute right-0 top-0 h-full w-1 cursor-col-resize hover:bg-indigo-300 transition-colors" />
    </th>
  );

  const [tab, setTab] = useState<StatusTab>('pending');
  const [scopeKind, setScopeKind] = useState<ApprovalScopeKind>('agent');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [fleets, setFleets] = useState<Fleet[]>([]);
  const [agentFilter, setAgentFilter] = useState('');
  const [fleetFilter, setFleetFilter] = useState('');
  const [kindFilter, setKindFilter] = useState<'k8s' | 'host'>('host');
  const [search, setSearch] = useState('');          // the input box
  const [appliedSearch, setAppliedSearch] = useState(''); // what was last submitted via Search
  // Kind, search, and paging all run server-side (SQL type/LIKE/limit+offset).
  // Default view is the 10 most recent (server orders by created_at desc).
  // Each kind keeps its own page; `approvals` is the current server page.
  const [pages, setPages] = useState<{ k8s: number; host: number }>({ k8s: 0, host: 0 });
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [total, setTotal] = useState(0);
  const page = pages[kindFilter];
  const setPage = (p: number) => setPages(prev => ({ ...prev, [kindFilter]: p }));
  const applySearch = () => { setAppliedSearch(search.trim()); setPage(0); };
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [bulking, setBulking] = useState<'approve' | 'deny' | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkModal, setBulkModal] = useState<'approve' | 'deny' | null>(null);
  const [tabCounts, setTabCounts] = useState<Partial<Record<StatusTab, number>>>({});
  const [showApprovalId, setShowApprovalId] = useState<boolean>(() => {
    try { return localStorage.getItem('approvals_show_id') === 'true'; } catch { return false; }
  });

  const loadSeqRef = useRef(0);

  type ModalState =
    | { type: 'approve'; approval: Approval }
    | { type: 'update-duration'; approval: Approval }
    | { type: 'deny'; approval: Approval }
    | { type: 'delete'; approval: Approval }
    | { type: 'expire-now'; approval: Approval }
    | { type: 'add' }
    | null;
  const [modal, setModal] = useState<ModalState>(null);

  useEffect(() => {
    listTenantAgents(apiUrl, tenantToken)
      .then(r => setAgents(r.agents ?? []))
      .catch(() => {});
    listFleets(apiUrl, tenantToken)
      .then(r => setFleets(r.fleets ?? []))
      .catch(() => {});
  }, [apiUrl, tenantToken]);

  // Fleets are host-only, so in fleet scope the kind is always Host.
  const effectiveKind = scopeKind === 'fleet' ? 'host' : kindFilter;

  const load = useCallback(() => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setError('');
    const params: Record<string, string> = {
      status: tab,
      scope: scopeKind,
      type: effectiveKind,
      limit: String(PAGE_SIZE),
      offset: String(page * PAGE_SIZE),
    };
    if (scopeKind === 'agent' && agentFilter) params.agent_id = agentFilter;
    if (scopeKind === 'fleet' && fleetFilter) params.fleet_id = fleetFilter;
    if (appliedSearch) params.q = appliedSearch;

    listAllTenantApprovals(apiUrl, tenantToken, params)
      .then(r => {
        if (loadSeqRef.current !== seq) return;
        setApprovals(r.approvals ?? []);
        setTotal(r.total ?? (r.approvals ?? []).length);
      })
      .catch(() => {
        if (loadSeqRef.current !== seq) return;
        setError('Failed to load approvals');
      })
      .finally(() => {
        if (loadSeqRef.current !== seq) return;
        setLoading(false);
      });
  }, [apiUrl, tenantToken, tab, scopeKind, agentFilter, fleetFilter, effectiveKind, appliedSearch, page]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => { setSelectedIds(new Set()); }, [tab]);

  // Tab badges show the per-status total for the current scope + kind.
  useEffect(() => {
    (['pending', 'approved', 'expired'] as StatusTab[]).forEach(s => {
      listAllTenantApprovals(apiUrl, tenantToken, { status: s, scope: scopeKind, type: effectiveKind, limit: '1' })
        .then(r => setTabCounts(prev => ({ ...prev, [s]: r.total ?? 0 })))
        .catch(() => {});
    });
  }, [apiUrl, tenantToken, scopeKind, effectiveKind]);

  const reload = useCallback(() => load(), [load]);

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    // Select-all applies to the current page (server-returned rows).
    if (selectedIds.size === approvals.length && approvals.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(approvals.map(a => a.approval_id)));
    }
  };

  const doApprove = async (id: string, duration?: string) => {
    await approveTenantApproval(apiUrl, tenantToken, id, duration);
    setModal(null);
    reload();
  };

  const doDeny = async (id: string) => {
    await denyTenantApproval(apiUrl, tenantToken, id);
    setModal(null);
    reload();
  };

  const doDelete = async (id: string) => {
    await deleteTenantApproval(apiUrl, tenantToken, id);
    setModal(null);
    setApprovals(prev => prev.filter(a => a.approval_id !== id));
  };

  const doAddApproval = async (agentId: string, payload: { command?: string; rule?: K8sRule }, duration?: string) => {
    if (payload.rule) await tenantPreApproveRule(apiUrl, tenantToken, agentId, payload.rule, duration);
    else await tenantPreApprove(apiUrl, tenantToken, agentId, payload.command ?? '', duration);
    setModal(null);
    setTab('approved');
    reload();
  };

  const doAddFleetApproval = async (fleetId: string, command: string, duration?: string) => {
    await tenantPreApproveFleet(apiUrl, tenantToken, fleetId, command, duration);
    setModal(null);
    setTab('approved');
    reload();
  };

  const doBulkApprove = async () => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    setBulking('approve');
    try {
      await Promise.allSettled(ids.map(id => approveTenantApproval(apiUrl, tenantToken, id)));
    } finally {
      setBulking(null);
      setSelectedIds(new Set());
      setBulkModal(null);
      reload();
    }
  };

  const doBulkDeny = async () => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    setBulking('deny');
    try {
      await Promise.allSettled(ids.map(id => denyTenantApproval(apiUrl, tenantToken, id)));
    } finally {
      setBulking(null);
      setSelectedIds(new Set());
      setBulkModal(null);
      reload();
    }
  };

  const TABS: StatusTab[] = ['pending', 'approved', 'denied', 'expired'];

  return (
    <div className="min-h-full bg-slate-50">
      <div className="bg-gradient-to-r from-indigo-700 to-indigo-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Approvals</h1>
              <p className="text-sm text-indigo-200">Review and manage command approval requests</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {tabCounts.pending != null && (
              <span className="inline-flex items-center gap-1.5 bg-amber-500/20 border border-amber-400/30 text-amber-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse shrink-0" />
                {tabCounts.pending} pending
              </span>
            )}
            {tabCounts.approved != null && (
              <span className="inline-flex items-center gap-1.5 bg-emerald-500/20 border border-emerald-400/30 text-emerald-300 text-xs font-semibold px-3 py-1.5 rounded-lg">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                {tabCounts.approved} approved
              </span>
            )}
            {tabCounts.expired != null && tabCounts.expired > 0 && (
              <span className="inline-flex items-center gap-1.5 bg-white/10 border border-white/20 text-indigo-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                {tabCounts.expired} expired
              </span>
            )}
            <RefreshButton onClick={reload} loading={loading} />
            {canAdd && (
              <button
                onClick={() => setModal({ type: 'add' })}
                className="inline-flex items-center gap-1.5 bg-white text-indigo-700 hover:bg-indigo-50 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm ml-1"
              >
                <span className="text-base leading-none">+</span> Add approval
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-4">
        <div className="flex gap-1 border-b border-gray-200">
          {TABS.map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px capitalize flex items-center gap-2 ${
                tab === t
                  ? 'border-indigo-600 text-indigo-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              {t === 'pending' && tab !== 'pending' && approvals.length > 0 && (
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              )}
              {t}
              {tab === t && !loading && approvals.length > 0 && (
                <span className="bg-indigo-100 text-indigo-700 text-xs font-semibold px-1.5 py-0.5 rounded-full">
                  {approvals.length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <ScopeToggle value={scopeKind} onChange={k => {
            if (k === scopeKind) return;
            setScopeKind(k);
            // Each scope has its own picker; reset the other and the page/selection.
            setAgentFilter(''); setFleetFilter(''); setSelectedIds(new Set()); setPage(0);
            if (k === 'fleet') setKindFilter('host');
          }} />
          {scopeKind === 'agent' ? (
            <select
              value={agentFilter}
              onChange={e => {
                const id = e.target.value;
                setAgentFilter(id);
                // An agent is a single type, so selecting one locks the Host/Kubernetes
                // toggle to that agent's type; "All agents" frees it again.
                const a = agents.find(x => x.agent_id === id);
                if (a?.type) { setKindFilter(a.type === 'k8s' ? 'k8s' : 'host'); setSelectedIds(new Set()); }
              }}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white shadow-sm"
            >
              <option value="">All agents</option>
              {/* Fleet members have no per-agent approvals - manage them under Fleets. */}
              {agents.filter(a => !a.fleet_id).map(a => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.hostname ?? a.agent_id}{a.type ? ` (${a.type})` : ''}
                </option>
              ))}
            </select>
          ) : (
            <select
              value={fleetFilter}
              onChange={e => { setFleetFilter(e.target.value); setSelectedIds(new Set()); }}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white shadow-sm"
            >
              <option value="">All fleets</option>
              {fleets.map(f => (
                <option key={f.fleet_id} value={f.fleet_id}>{f.name ?? f.fleet_id}</option>
              ))}
            </select>
          )}
          {scopeKind === 'agent' && (
          <div className={`inline-flex rounded-lg border border-gray-300 bg-white shadow-sm overflow-hidden ${agentFilter ? 'opacity-60' : ''}`}>
            {(['k8s', 'host'] as const).map(k => (
              <button
                key={k}
                disabled={!!agentFilter}
                title={agentFilter ? 'Locked to the selected agent’s type - choose “All agents” to switch' : undefined}
                onClick={() => { if (agentFilter) return; setKindFilter(k); setSelectedIds(new Set()); }}
                className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                  kindFilter === k ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'
                } ${agentFilter ? 'cursor-not-allowed' : ''}`}
              >
                {k === 'k8s' ? 'Kubernetes' : 'Host'}
              </button>
            ))}
          </div>
          )}
          <div className="relative">
            <svg className="w-4 h-4 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applySearch()}
              placeholder={scopeKind === 'fleet' ? 'Search command, fleet…' : kindFilter === 'k8s' ? 'Search verb, resource, namespace…' : 'Search command, agent…'}
              className="w-64 border border-gray-300 rounded-lg pl-8 pr-7 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent shadow-sm"
            />
            {search && (
              <button onClick={() => { setSearch(''); if (appliedSearch) { setAppliedSearch(''); setPage(0); } }} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-sm">✕</button>
            )}
          </div>
          <button
            onClick={applySearch}
            className="text-sm font-semibold px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors shadow-sm"
          >
            Search
          </button>
          {appliedSearch && (
            <span className="text-xs text-gray-500">Filtered by “{appliedSearch}”</span>
          )}
          {scopeKind === 'agent' && agentFilter && (
            <button onClick={() => setAgentFilter('')} className="text-sm text-indigo-600 hover:text-indigo-800">
              Clear agent filter
            </button>
          )}
          {scopeKind === 'fleet' && fleetFilter && (
            <button onClick={() => setFleetFilter('')} className="text-sm text-indigo-600 hover:text-indigo-800">
              Clear fleet filter
            </button>
          )}
          {tab === 'pending' && selectedIds.size > 0 && (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-gray-500">{selectedIds.size} selected</span>
              <button
                onClick={() => setBulkModal('approve')}
                disabled={!!bulking}
                className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors shadow-sm"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
                Approve ({selectedIds.size})
              </button>
              <button
                onClick={() => setBulkModal('deny')}
                disabled={!!bulking}
                className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors shadow-sm"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
                Deny ({selectedIds.size})
              </button>
            </div>
          )}
        </div>

        {error && (
          <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
            <span className="shrink-0">⚠</span> {error}
          </div>
        )}

        {loading && approvals.length === 0 ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : (
          <div className={`bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden relative ${loading ? 'opacity-60 pointer-events-none' : ''}`}>
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center z-10">
                <Spinner />
              </div>
            )}
            <div className="flex justify-end px-3 py-2 border-b border-gray-100 bg-gray-50/60">
              <button
                onClick={() => {
                  const next = !showApprovalId;
                  setShowApprovalId(next);
                  try { localStorage.setItem('approvals_show_id', String(next)); } catch {}
                }}
                className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-colors ${
                  showApprovalId
                    ? 'bg-gray-800 text-white border-gray-800'
                    : 'text-gray-500 border-gray-200 bg-white hover:bg-gray-50 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 4.5v15m6-15v15M3.75 9h16.5M3.75 15h16.5" />
                </svg>
                Approval ID
              </button>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50">
                  {tab === 'pending' && (
                    <th className="w-10 pl-4 pr-2 py-3">
                      <input
                        type="checkbox"
                        checked={approvals.length > 0 && selectedIds.size === approvals.length}
                        ref={el => { if (el) el.indeterminate = selectedIds.size > 0 && selectedIds.size < approvals.length; }}
                        onChange={toggleSelectAll}
                        className="w-4 h-4 cursor-pointer rounded accent-indigo-600"
                      />
                    </th>
                  )}
                  {opTh('Command / rule', 0)}
                  {opTh(scopeKind === 'fleet' ? 'Fleet' : 'Agent', 1)}
                  {opTh('Requested by', 2)}
                  {opTh('Created', 3)}
                  {tab === 'approved' ? (
                    <>
                      {opTh('Reviewed by', 4)}
                      {opTh('Reviewed at', 5)}
                      {opTh('Expiry', 6)}
                    </>
                  ) : (
                    opTh('Status', 4)
                  )}
                  {showApprovalId && opTh('Approval ID', 7)}
                  <th />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {approvals.length === 0 ? (
                  <tr>
                    <td colSpan={99}>
                      <div className="flex flex-col items-center py-16 text-center">
                        <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mb-3">
                          <svg className="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                          </svg>
                        </div>
                        <p className="text-sm font-medium text-gray-500">
                          {(() => {
                            const label = scopeKind === 'fleet' ? 'fleet' : (kindFilter === 'k8s' ? 'Kubernetes' : 'host');
                            return appliedSearch
                              ? `No ${label} ${tab} approvals match “${appliedSearch}”`
                              : `No ${label} ${tab} approvals`;
                          })()}
                        </p>
                        {tab === 'pending' && canAdd && !appliedSearch && (
                          <p className="text-xs text-gray-400 mt-1">
                            Use{' '}
                            <button onClick={() => setModal({ type: 'add' })} className="text-indigo-600 hover:underline">
                              Add approval
                            </button>{' '}
                            to proactively approve a command.
                          </p>
                        )}
                      </div>
                    </td>
                  </tr>
                ) : approvals.map(a => {
                  return (
                  <tr key={a.approval_id} className={`hover:bg-slate-50/80 transition-colors group ${tab === 'pending' && selectedIds.has(a.approval_id) ? 'bg-indigo-50/40' : ''}`}>
                    {tab === 'pending' && (
                      <td className="w-10 pl-4 pr-2 py-3.5">
                        <input
                          type="checkbox"
                          checked={selectedIds.has(a.approval_id)}
                          onChange={() => toggleSelect(a.approval_id)}
                          onClick={e => e.stopPropagation()}
                          className="w-4 h-4 cursor-pointer rounded accent-indigo-600"
                        />
                      </td>
                    )}
                    <td className="px-4 py-3.5 max-w-[360px]">
                      <ApprovalTarget approval={a} />
                    </td>
                    <td className="px-4 py-3.5">
                      <ApprovalScope approval={a} />
                    </td>
                    <td className="px-4 py-3.5 text-sm text-gray-600">
                      {a.requester_name ?? a.requested_by ?? '-'}
                    </td>
                    <td className="px-4 py-3.5 text-sm text-gray-500 whitespace-nowrap">
                      {fmtDate(a.created_at)}
                    </td>
                    {tab === 'approved' ? (
                      <>
                        <td className="px-4 py-3.5 text-sm text-gray-600 whitespace-nowrap">
                          {a.reviewed_by ?? '-'}
                        </td>
                        <td className="px-4 py-3.5 text-sm text-gray-500 whitespace-nowrap">
                          {a.reviewed_at ? fmtDate(a.reviewed_at) : '-'}
                        </td>
                        <td className="px-4 py-3.5 text-sm">
                          <span className={`inline-flex items-center gap-1.5 font-medium ${a.expires_at ? 'text-amber-600' : 'text-emerald-700'}`}>
                            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${a.expires_at ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                            {fmtExpiry(a.expires_at)}
                          </span>
                        </td>
                      </>
                    ) : (
                      <td className="px-4 py-3.5 text-sm">
                        <StatusBadge status={a.status} />
                      </td>
                    )}
                    {showApprovalId && (
                      <td className="px-4 py-3.5 font-mono text-xs text-gray-400 whitespace-nowrap">{a.approval_id}</td>
                    )}
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-1.5 justify-end whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity">
                        {tab === 'pending' && (
                          <>
                            <button
                              onClick={() => setModal({ type: 'approve', approval: a })}
                              className="text-xs font-semibold text-emerald-700 hover:text-white hover:bg-emerald-600 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-md transition-colors"
                            >
                              Approve
                            </button>
                            <button
                              onClick={() => setModal({ type: 'deny', approval: a })}
                              className="text-xs font-semibold text-red-600 hover:text-white hover:bg-red-600 bg-red-50 border border-red-200 px-2.5 py-1 rounded-md transition-colors"
                            >
                              Deny
                            </button>
                          </>
                        )}
                        {tab === 'approved' && (
                          <>
                            <button
                              onClick={() => setModal({ type: 'update-duration', approval: a })}
                              className="text-xs font-medium text-gray-600 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 px-2.5 py-1 rounded-md transition-colors"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => setModal({ type: 'expire-now', approval: a })}
                              className="text-xs font-medium text-amber-700 hover:text-white hover:bg-amber-600 bg-amber-50 border border-amber-200 px-2.5 py-1 rounded-md transition-colors"
                            >
                              Expire now
                            </button>
                          </>
                        )}
                        <button
                          onClick={() => setModal({ type: 'delete', approval: a })}
                          className="w-6 h-6 flex items-center justify-center text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors text-xs"
                          title="Delete record"
                        >
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
            <Pager page={page} total={total} onPage={setPage} />
          </div>
        )}
      </div>

      {bulkModal && (
        <BulkConfirmModal
          action={bulkModal}
          count={selectedIds.size}
          loading={!!bulking}
          onClose={() => setBulkModal(null)}
          onConfirm={bulkModal === 'approve' ? doBulkApprove : doBulkDeny}
        />
      )}

      {modal?.type === 'deny' && (
        <DenyModal
          approval={modal.approval}
          onClose={() => setModal(null)}
          onConfirm={(id: string) => doDeny(id).catch(e => setError((e as Error).message))}
        />
      )}

      {modal?.type === 'delete' && (
        <DeleteApprovalModal
          approval={modal.approval}
          onClose={() => setModal(null)}
          onConfirm={(id: string) => doDelete(id).catch(e => setError((e as Error).message))}
        />
      )}

      {modal?.type === 'expire-now' && (
        <ExpireNowModal
          approval={modal.approval}
          onClose={() => setModal(null)}
          onConfirm={() => doApprove(modal.approval.approval_id, 'now').catch(e => setError((e as Error).message))}
        />
      )}

      {modal?.type === 'approve' && (
        <ApproveModal
          approval={modal.approval}
          onClose={() => setModal(null)}
          onApprove={doApprove}
        />
      )}

      {modal?.type === 'update-duration' && (
        <ApproveModal
          approval={modal.approval}
          title="Update duration"
          showNow
          onClose={() => setModal(null)}
          onApprove={doApprove}
        />
      )}

      {modal?.type === 'add' && (
        <AddApprovalModal
          scope={scopeKind}
          agents={agents}
          fleets={fleets}
          onClose={() => setModal(null)}
          onSubmit={doAddApproval}
          onSubmitFleet={doAddFleetApproval}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bulk confirm modal
// ---------------------------------------------------------------------------

function BulkConfirmModal({ action, count, loading, onClose, onConfirm }: {
  action: 'approve' | 'deny';
  count: number;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const isApprove = action === 'approve';
  return (
    <Modal title={isApprove ? 'Approve selected' : 'Deny selected'} onClose={onClose}>
      <div className="space-y-4">
        <div className={`rounded-lg p-4 border ${isApprove ? 'bg-emerald-50 border-emerald-200' : 'bg-red-50 border-red-200'}`}>
          <p className={`text-sm font-medium ${isApprove ? 'text-emerald-800' : 'text-red-800'}`}>
            {isApprove
              ? `Approve ${count} selected request${count !== 1 ? 's' : ''}? The agents will be permitted to run these commands.`
              : `Deny ${count} selected request${count !== 1 ? 's' : ''}? The agents will not be permitted to run these commands.`}
          </p>
        </div>
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} disabled={loading} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2 disabled:opacity-50">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className={`flex items-center gap-2 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm ${isApprove ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700'}`}
          >
            {loading && <Spinner className="h-4 w-4" />}
            {isApprove ? `Approve ${count}` : `Deny ${count}`}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Developer: request approval modal
// ---------------------------------------------------------------------------

function RequestApprovalModal({
  agents, onClose, onSubmit,
}: {
  agents: Agent[];
  onClose: () => void;
  onSubmit: (agentId: string, payload: { command?: string; rule?: K8sRule }) => Promise<void>;
}) {
  const [agentId, setAgentId] = useState('');
  const [command, setCommand] = useState('');
  const [rule, setRule] = useState<K8sRule>(EMPTY_RULE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const isK8s = agents.find(a => a.agent_id === agentId)?.type === 'k8s';

  const submit = async () => {
    if (!agentId) { setError('Select an agent.'); return; }
    if (!isK8s && !command.trim()) { setError('Command is required.'); return; }
    setLoading(true); setError('');
    try { await onSubmit(agentId, isK8s ? { rule } : { command: command.trim() }); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  // Fleet members don't take per-agent approvals - those are managed on the fleet
  // (Fleets page). Only standalone agents are selectable here.
  const activeAgents = agents.filter(a => (a.status === 'ACTIVE' || a.status === 'INACTIVE') && !a.fleet_id);

  return (
    <Modal title="Request approval" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-2.5">
          <p className="text-xs text-indigo-700">
            Your request will go to <strong>pending</strong> - an operator or admin must approve it before the agent can run it.
          </p>
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1.5">Agent</label>
          <select
            value={agentId}
            onChange={e => setAgentId(e.target.value)}
            autoFocus
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white"
          >
            <option value="">Select agent…</option>
            {activeAgents.map(a => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.hostname ?? a.agent_id}{a.type === 'k8s' ? ' (k8s)' : ''}
              </option>
            ))}
          </select>
        </div>
        {isK8s ? (
          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-1.5">Cluster rule</label>
            <K8sRuleForm value={rule} onChange={setRule} />
          </div>
        ) : (
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1.5">Command</label>
          <input
            value={command}
            onChange={e => setCommand(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder="docker restart app"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          />
          <p className="text-xs text-gray-400 mt-1">
            Prefix match - approving "docker restart" also permits "docker restart app".
          </p>
        </div>
        )}
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />} Submit request
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Operator modals (approve, deny, expire, delete, add)
// ---------------------------------------------------------------------------

function ApproveModal({
  approval, title = 'Approve command', showNow = false, onClose, onApprove,
}: {
  approval: Approval;
  title?: string;
  showNow?: boolean;
  onClose: () => void;
  onApprove: (id: string, duration?: string) => Promise<void>;
}) {
  const [duration, setDuration] = useState<Duration>('permanent');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (d?: string) => {
    setLoading(true); setError('');
    try { await onApprove(approval.approval_id, d); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-gray-50 rounded-lg p-3 border border-gray-100 space-y-2">
          <div>
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1.5">Command</p>
            <code className="text-sm text-gray-800 break-all font-mono block bg-white border border-gray-200 rounded px-3 py-2">{approval.command}</code>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-4 h-4 rounded bg-indigo-100 flex items-center justify-center shrink-0">
              <svg className="w-2.5 h-2.5 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6" />
              </svg>
            </div>
            <p className="text-xs text-gray-500">{approval.agent_hostname ?? approval.agent_id}</p>
          </div>
          <div className="flex items-center justify-between gap-2 pt-0.5 border-t border-gray-200 group/aid">
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Approval ID</span>
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[11px] font-mono text-gray-400 truncate">{approval.approval_id}</span>
              <CopyButton text={approval.approval_id} className="opacity-0 group-hover/aid:opacity-100 transition-opacity shrink-0 !px-1.5 !py-0.5 text-[10px]" />
            </div>
          </div>
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Duration</label>
          <div className="flex flex-wrap gap-2">
            {DURATIONS.map(d => (
              <button
                key={d}
                onClick={() => setDuration(d)}
                className={`px-3 py-1.5 text-sm rounded-lg border transition-all ${
                  duration === d
                    ? 'bg-indigo-600 text-white border-indigo-600 shadow-sm'
                    : 'border-gray-300 text-gray-600 hover:border-indigo-300 hover:text-indigo-600 hover:bg-indigo-50'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          {showNow && (
            <button
              onClick={() => submit('now')}
              disabled={loading}
              className="text-sm font-medium text-amber-700 hover:text-white hover:bg-amber-600 border border-amber-300 bg-amber-50 px-4 py-2 rounded-lg transition-colors"
            >
              Expire now
            </button>
          )}
          <button
            onClick={() => submit(duration === 'permanent' ? undefined : duration)}
            disabled={loading}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />}
            {showNow ? 'Save' : 'Approve'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function ExpireNowModal({ approval, onClose, onConfirm }: {
  approval: Approval;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const submit = () => { setLoading(true); onConfirm(); };
  return (
    <Modal title="Expire approval" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm text-amber-800 font-medium">This approval will be revoked immediately. The agent will need a new approval to run this command.</p>
        </div>
        <div className="bg-gray-50 rounded-lg p-3 border border-gray-100 space-y-2">
          <div>
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1.5">Command</p>
            <code className="text-sm text-gray-800 break-all font-mono block bg-white border border-gray-200 rounded px-3 py-2">{approval.command}</code>
          </div>
          <p className="text-xs text-gray-500">{approval.agent_hostname ?? approval.agent_id}</p>
          <div className="flex items-center justify-between gap-2 pt-0.5 border-t border-gray-200 group/aid">
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Approval ID</span>
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[11px] font-mono text-gray-400 truncate">{approval.approval_id}</span>
              <CopyButton text={approval.approval_id} className="opacity-0 group-hover/aid:opacity-100 transition-opacity shrink-0 !px-1.5 !py-0.5 text-[10px]" />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />} Expire now
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DenyModal({ approval, onClose, onConfirm }: {
  approval: Approval;
  onClose: () => void;
  onConfirm: (id: string) => void;
}) {
  const [loading, setLoading] = useState(false);
  const submit = () => { setLoading(true); onConfirm(approval.approval_id); };
  return (
    <Modal title="Deny request" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm text-red-800 font-medium">The agent will not be permitted to run this command.</p>
        </div>
        <div className="bg-gray-50 rounded-lg p-3 border border-gray-100 space-y-2">
          <div>
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1.5">Command</p>
            <code className="text-sm text-gray-800 break-all font-mono block bg-white border border-gray-200 rounded px-3 py-2">{approval.command}</code>
          </div>
          <p className="text-xs text-gray-500">{approval.agent_hostname ?? approval.agent_id}</p>
          <div className="flex items-center justify-between gap-2 pt-0.5 border-t border-gray-200 group/aid">
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Approval ID</span>
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[11px] font-mono text-gray-400 truncate">{approval.approval_id}</span>
              <CopyButton text={approval.approval_id} className="opacity-0 group-hover/aid:opacity-100 transition-opacity shrink-0 !px-1.5 !py-0.5 text-[10px]" />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />} Deny request
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DeleteApprovalModal({ approval, onClose, onConfirm }: {
  approval: Approval;
  onClose: () => void;
  onConfirm: (id: string) => void;
}) {
  const [loading, setLoading] = useState(false);
  const submit = () => { setLoading(true); onConfirm(approval.approval_id); };
  return (
    <Modal title="Delete record" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm text-red-800 font-medium">This approval record will be permanently deleted.</p>
        </div>
        <div className="bg-gray-50 rounded-lg p-3 border border-gray-100 space-y-2">
          <div>
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1.5">Command</p>
            <code className="text-sm text-gray-800 break-all font-mono block bg-white border border-gray-200 rounded px-3 py-2">{approval.command}</code>
          </div>
          <p className="text-xs text-gray-500">{approval.agent_hostname ?? approval.agent_id}</p>
          <div className="flex items-center justify-between gap-2 pt-0.5 border-t border-gray-200 group/aid">
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Approval ID</span>
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[11px] font-mono text-gray-400 truncate">{approval.approval_id}</span>
              <CopyButton text={approval.approval_id} className="opacity-0 group-hover/aid:opacity-100 transition-opacity shrink-0 !px-1.5 !py-0.5 text-[10px]" />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />} Delete permanently
          </button>
        </div>
      </div>
    </Modal>
  );
}

function AddApprovalModal({
  scope, agents, fleets, onClose, onSubmit, onSubmitFleet,
}: {
  scope: ApprovalScopeKind;
  agents: Agent[];
  fleets: Fleet[];
  onClose: () => void;
  onSubmit: (agentId: string, payload: { command?: string; rule?: K8sRule }, duration?: string) => Promise<void>;
  onSubmitFleet: (fleetId: string, command: string, duration?: string) => Promise<void>;
}) {
  // The modal has its own Agent/Fleet chooser (defaulting to the current view),
  // so you can add either kind regardless of what the list is filtered to.
  const [target, setTarget] = useState<ApprovalScopeKind>(scope);
  const isFleetScope = target === 'fleet';
  const [agentId, setAgentId] = useState('');
  const [fleetId, setFleetId] = useState('');
  const [command, setCommand] = useState('');
  const [rule, setRule] = useState<K8sRule>(EMPTY_RULE);
  const [duration, setDuration] = useState<Duration>('permanent');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const isK8s = !isFleetScope && agents.find(a => a.agent_id === agentId)?.type === 'k8s';

  const submit = async () => {
    const dur = duration === 'permanent' ? undefined : duration;
    setError('');
    try {
      if (isFleetScope) {
        if (!fleetId) { setError('Select a fleet.'); return; }
        if (!command.trim()) { setError('Command is required.'); return; }
        setLoading(true);
        await onSubmitFleet(fleetId, command.trim(), dur);
      } else {
        if (!agentId) { setError('Select an agent.'); return; }
        if (!isK8s && !command.trim()) { setError('Command is required.'); return; }
        setLoading(true);
        await onSubmit(agentId, isK8s ? { rule } : { command: command.trim() }, dur);
      }
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  // Fleet members don't take per-agent approvals - those are managed on the fleet
  // (Fleets page). Only standalone agents are selectable here.
  const activeAgents = agents.filter(a => (a.status === 'ACTIVE' || a.status === 'INACTIVE') && !a.fleet_id);

  return (
    <Modal title="Add approval" onClose={onClose}>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1.5">Target</label>
          <div className="inline-flex rounded-lg border border-gray-300 bg-white shadow-sm overflow-hidden">
            {([['agent', 'Agent'], ['fleet', 'Fleet']] as const).map(([k, label]) => (
              <button
                key={k}
                onClick={() => { setTarget(k); setError(''); }}
                className={`px-4 py-1.5 text-sm font-medium transition-colors ${
                  target === k ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-2.5">
          <p className="text-xs text-indigo-700">
            Creates an <strong>approved</strong> record directly - {isFleetScope ? 'every member of the fleet' : 'the agent'} can run it immediately without a pending request.
          </p>
        </div>
        {isFleetScope ? (
          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-1.5">Fleet</label>
            <select
              value={fleetId}
              onChange={e => setFleetId(e.target.value)}
              autoFocus
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white"
            >
              <option value="">Select fleet…</option>
              {fleets.map(f => (
                <option key={f.fleet_id} value={f.fleet_id}>{f.name ?? f.fleet_id}</option>
              ))}
            </select>
          </div>
        ) : (
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1.5">Agent</label>
          <select
            value={agentId}
            onChange={e => setAgentId(e.target.value)}
            autoFocus
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white"
          >
            <option value="">Select agent…</option>
            {activeAgents.map(a => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.hostname ?? a.agent_id}{a.type === 'k8s' ? ' (k8s)' : ''}
              </option>
            ))}
          </select>
        </div>
        )}
        {isK8s ? (
          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-1.5">Cluster rule</label>
            <K8sRuleForm value={rule} onChange={setRule} />
          </div>
        ) : (
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1.5">Command</label>
          <input
            value={command}
            onChange={e => setCommand(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder="docker restart app"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          />
          <p className="text-xs text-gray-400 mt-1">
            Prefix match - "docker restart" also permits "docker restart app".
          </p>
        </div>
        )}
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Duration</label>
          <div className="flex flex-wrap gap-2">
            {DURATIONS.map(d => (
              <button
                key={d}
                onClick={() => setDuration(d)}
                className={`px-3 py-1.5 text-sm rounded-lg border transition-all ${
                  duration === d
                    ? 'bg-indigo-600 text-white border-indigo-600 shadow-sm'
                    : 'border-gray-300 text-gray-600 hover:border-indigo-300 hover:text-indigo-600 hover:bg-indigo-50'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-400 mt-1.5">Timing applies to the approved record only.</p>
        </div>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-60 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />} Add approval
          </button>
        </div>
      </div>
    </Modal>
  );
}
