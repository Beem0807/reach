import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { TenantConfig, Agent, AgentHistory, TenantUser, Fleet } from '../types';
import {
  listTenantAgents,
  listTenantUsers,
  listFleets,
  removeFleetMember,
  listAgentVersions,
  createTenantAgent,
  reissueTenantInstallToken,
  requestAgentRotation,
  revokeTenantAgent,
  deleteTenantAgent,
  removeTenantAgent,
  setTenantAgentMode,
  setTenantAgentTags,
  acknowledgeCapability,
  listAgentHistory,
} from '../api';
import { Modal } from '../components/Modal';
import { RunCommandModal } from '../components/RunCommandModal';
import { RefreshButton } from '../components/RefreshButton';
import { Badge } from '../components/Badge';
import { K8sPermissionsView } from '../components/K8sPermissionsView';
import { Spinner } from '../components/Spinner';
import { CopyButton, TokenBox } from '../components/CopyButton';
import { DataTable } from '../components/DataTable';
import { relTime, memberMismatchAccepted } from '../utils';

const MODES = ['wild', 'readonly', 'approved'] as const;
type Mode = typeof MODES[number];

const MODE_LABEL: Record<string, string> = {
  wild:     'Wild',
  readonly: 'Read-only',
  approved: 'Approved',
};

const MODE_DESC: Record<string, string> = {
  wild:     'Agent can run any command freely.',
  readonly: 'Agent runs only read/observe commands.',
  approved: 'Read commands run freely; write commands need approval.',
};


function CapabilityCell({ granted, detected, onAcknowledge, fleetDrift, fleetWants }: {
  granted?: boolean; detected?: boolean; onAcknowledge?: () => void;
  // Set for fleet members whose grant differs from the fleet's desired grant - the
  // the same "grant mismatch" the Fleets screen flags, surfaced here for consistency.
  fleetDrift?: boolean; fleetWants?: boolean;
}) {
  const outOfBand   = detected && !granted;
  const active      = detected && granted;
  const grantedOnly = granted && !detected;

  const tooltip = outOfBand
    ? 'Detected without a grant - out-of-band access, needs acknowledgement'
    : active
    ? 'Granted and currently detected running on this agent'
    : grantedOnly
    ? 'Admin enabled - permission granted but not yet detected running on this agent'
    : 'Not configured - no permission grant and not currently detected';

  const badge = outOfBand ? (
    <div className="flex flex-col items-start gap-0.5">
      <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
        </svg>
        Detected
      </span>
      {onAcknowledge && (
        <button
          onClick={e => { e.stopPropagation(); onAcknowledge(); }}
          className="text-[10px] text-amber-600 hover:text-amber-800 font-medium underline underline-offset-2 leading-none px-0.5"
        >
          Acknowledge
        </button>
      )}
    </div>
  ) : active ? (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
      </svg>
      Active
    </span>
  ) : grantedOnly ? (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
      Granted
    </span>
  ) : (
    <span className="text-gray-300 text-sm">-</span>
  );

  return (
    <div className="relative group/cap inline-flex flex-col items-start gap-0.5">
      {badge}
      {fleetDrift && (
        <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1 py-0.5 rounded"
          title={`Fleet grant mismatch: this member has it ${granted ? 'on' : 'off'}, but the fleet now wants it ${fleetWants ? 'on' : 'off'}. Reconcile it from the Fleets screen (verified against detection).`}>
          ⚠ grant mismatch
        </span>
      )}
      <div className="pointer-events-none absolute bottom-full left-0 mb-1.5 z-20 w-56 bg-gray-900 text-white text-[11px] leading-snug rounded-lg px-2.5 py-2 opacity-0 group-hover/cap:opacity-100 transition-opacity shadow-xl whitespace-normal">
        {tooltip}
        <div className="absolute top-full left-4 -mt-px border-4 border-transparent border-t-gray-900" />
      </div>
    </div>
  );
}

// RbacCell is the Kubernetes counterpart of CapabilityCell. Docker/service-mgmt
// model a grant; cluster RBAC instead has a self-reported permission set that
// must be acknowledged. The cell is icon-only: drift (a change since the last
// ack, including the first report) shows a warning, acknowledged shows a check,
// and nothing-reported shows a dash. Hover explains the state; clicking opens
// the agent's RBAC detail (where the rules are reviewed and acknowledged).
function RbacCell({ reported, drift, onOpen }: { reported?: boolean; drift?: boolean; onOpen?: () => void }) {
  const tooltip = drift
    ? 'Cluster RBAC changed - needs acknowledgement. Click to review the rules and acknowledge.'
    : reported
    ? 'Cluster RBAC reported and acknowledged. Click to view the rules.'
    : 'No cluster RBAC reported yet';

  const icon = drift ? (
    <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
    </svg>
  ) : reported ? (
    <svg className="w-4 h-4 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ) : (
    <span className="text-gray-300 text-sm">-</span>
  );

  // No reported permissions yet: plain dash, nothing to open.
  if (!reported && !drift) {
    return (
      <div className="relative group/cap inline-flex">
        {icon}
        <div className="pointer-events-none absolute bottom-full left-0 mb-1.5 z-20 w-56 bg-gray-900 text-white text-[11px] leading-snug rounded-lg px-2.5 py-2 opacity-0 group-hover/cap:opacity-100 transition-opacity shadow-xl whitespace-normal">
          {tooltip}
          <div className="absolute top-full left-4 -mt-px border-4 border-transparent border-t-gray-900" />
        </div>
      </div>
    );
  }

  return (
    <button
      onClick={e => { e.stopPropagation(); onOpen?.(); }}
      className="relative group/cap inline-flex rounded hover:bg-gray-100 p-0.5 -m-0.5"
      aria-label={drift ? 'Cluster RBAC needs review' : 'View cluster RBAC'}
    >
      {icon}
      <div className="pointer-events-none absolute bottom-full left-0 mb-1.5 z-20 w-56 bg-gray-900 text-white text-[11px] leading-snug rounded-lg px-2.5 py-2 opacity-0 group-hover/cap:opacity-100 transition-opacity shadow-xl whitespace-normal text-left">
        {tooltip}
        <div className="absolute top-full left-4 -mt-px border-4 border-transparent border-t-gray-900" />
      </div>
    </button>
  );
}

function fmtDate(iso?: string) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

type ModalState =
  | { type: 'create' }
  | { type: 'install'; agent: Agent & { install_token: string; commands: Record<string, string> } }
  | { type: 'reissue'; agent: Agent }
  | { type: 'set-mode'; agent: Agent }
  | { type: 'set-tags'; agent: Agent }
  | { type: 'detail'; agent: Agent; backFleetId?: string | null }
  | { type: 'confirm-revoke'; agent: Agent }
  | { type: 'confirm-delete'; agent: Agent }
  | { type: 'confirm-remove'; agent: Agent }
  | { type: 'rotate'; agent: Agent }
  | { type: 'detach-fleet'; agent: Agent }
  | { type: 'run-agent'; agent: Agent }
  | null;

export function TenantAgentsPage({ config, focusAgentId, backFleetId, onBackToFleet, onFocusConsumed }: {
  config: TenantConfig;
  focusAgentId?: string | null;
  backFleetId?: string | null;
  onBackToFleet?: (fleetId: string) => void;
  onFocusConsumed?: () => void;
}) {
  const { apiUrl, tenantToken, role } = config;
  const isOperator = role === 'admin' || role === 'operator';

  const PAGE = 20;
  const [agents, setAgents] = useState<Agent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  // Draft (form) filters - what the toolbar shows. Nothing hits the server until the
  // user clicks Search, so choosing a dropdown option just stages it.
  const [search, setSearch] = useState('');
  const [tagFilters, setTagFilters] = useState<Set<string>>(new Set());
  const [modeFilter, setModeFilter] = useState('');
  const [accessFilter, setAccessFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [fleetFilter, setFleetFilter] = useState('');
  // Applied filters - the set the current server results reflect. Search copies the
  // draft here (all at once) and the load runs against these.
  const EMPTY_APPLIED = { tags: '', mode: '', access: '', type: '', fleet: '', q: '' };
  const [applied, setApplied] = useState(EMPTY_APPLIED);
  // Full tag universe for the filter dropdown, from the server facet (every accessible
  // agent, not just the current page).
  const [allTags, setAllTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<ModalState>(null);
  const [fleets, setFleets] = useState<Fleet[]>([]);
  const [tagPickerOpen, setTagPickerOpen] = useState(false);
  const tagPickerRef = useRef<HTMLDivElement>(null);

  // Search applies the whole filter form at once (button / Enter) - like the Approvals
  // & Audit pages, the API call fires only on submit, never on a keystroke or a
  // dropdown change. This re-queries the server so filters span every page, not just
  // the loaded one.
  const applyFilters = () => {
    setApplied({
      tags: [...tagFilters].sort().join(','),
      mode: modeFilter, access: accessFilter, type: typeFilter, fleet: fleetFilter,
      q: search.trim(),
    });
    setOffset(0);
  };

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    // Server-side filter + search + pagination: one page (PAGE) of the matched agents,
    // so the console never loads every agent in a large tenant.
    const params: Record<string, string> = { limit: String(PAGE), offset: String(offset) };
    if (applied.q) params.q = applied.q;
    if (applied.tags) params.tag = applied.tags;
    if (applied.mode) params.mode = applied.mode;
    if (applied.access) params.access = applied.access;
    if (applied.type) params.type = applied.type;
    if (applied.fleet) params.fleet = applied.fleet;
    listTenantAgents(apiUrl, tenantToken, params)
      .then(r => {
        setAgents(r.agents ?? []);
        setTotal(r.total ?? (r.agents?.length ?? 0));
        setAllTags(r.all_tags ?? []);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
    // Fleets are only needed to label/filter by fleet name; failure is non-fatal
    // (developers can't list fleets - the column just falls back to the id).
    listFleets(apiUrl, tenantToken).then(r => setFleets(r.fleets)).catch(() => {});
  }, [apiUrl, tenantToken, applied, offset]);

  useEffect(() => { load(); }, [load]);

  // Deep-link from the Fleets page: open a specific agent's detail once loaded,
  // carrying the originating fleet so the modal can offer a "Back to fleet" link.
  useEffect(() => {
    if (!focusAgentId) return;
    const target = agents.find(a => a.agent_id === focusAgentId);
    if (target) {
      setModal({ type: 'detail', agent: target, backFleetId });
      onFocusConsumed?.();
    }
  }, [focusAgentId, backFleetId, agents, onFocusConsumed]);

  const closeAndReload = () => { setModal(null); load(); };

  const handleAcknowledge = async (agent: Agent, capability: 'docker' | 'service_mgmt' | 'k8s_permissions') => {
    try {
      await acknowledgeCapability(apiUrl, tenantToken, agent.agent_id, capability);
      setModal(null);  // close the (now-stale) detail modal so the reloaded list shows cleared drift
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const fleetById = useMemo(() => new Map(fleets.map(f => [f.fleet_id, f])), [fleets]);
  const fleetLabel = (id?: string | null) => id ? (fleetById.get(id)?.name ?? id) : '';

  // Filtering happens on the server now (over the full tenant set), so the table just
  // renders the page it was handed.
  const draftFilterCount = tagFilters.size + (modeFilter ? 1 : 0) + (accessFilter ? 1 : 0) + (typeFilter ? 1 : 0) + (fleetFilter ? 1 : 0);
  const anyApplied = !!(applied.q || applied.tags || applied.mode || applied.access || applied.type || applied.fleet);
  // The draft has un-applied changes - the Search button is highlighted until submitted.
  const filtersDirty = [...tagFilters].sort().join(',') !== applied.tags
    || modeFilter !== applied.mode || accessFilter !== applied.access
    || typeFilter !== applied.type || fleetFilter !== applied.fleet
    || search.trim() !== applied.q;

  const clearFilters = () => {
    setTagFilters(new Set()); setModeFilter(''); setAccessFilter(''); setTypeFilter(''); setFleetFilter(''); setSearch('');
    setApplied(EMPTY_APPLIED); setOffset(0);
  };

  const activeCount   = agents.filter(a => a.status === 'ACTIVE').length;
  const inactiveCount = agents.filter(a => a.status === 'INACTIVE').length;
  const revokedCount  = agents.filter(a => a.status === 'REVOKED' || a.status === 'DELETED').length;

  const toggleTag = (tag: string) => {
    setTagFilters(prev => {
      const next = new Set(prev);
      next.has(tag) ? next.delete(tag) : next.add(tag);
      return next;
    });
  };

  useEffect(() => {
    if (!tagPickerOpen) return;
    const handler = (e: MouseEvent) => {
      if (tagPickerRef.current && !tagPickerRef.current.contains(e.target as Node)) {
        setTagPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [tagPickerOpen]);

  return (
    <div className="min-h-full bg-slate-50">
      {/* Page header */}
      <div className="bg-gradient-to-r from-slate-800 to-slate-700 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3m-19.5 0a4.5 4.5 0 01.9-2.7L5.737 5.1a3.375 3.375 0 012.7-1.35h7.126c1.062 0 2.062.5 2.7 1.35l2.587 3.45a4.5 4.5 0 01.9 2.7" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Agents</h1>
              <p className="text-sm text-slate-300">Machines registered to your tenant</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {!loading && agents.length > 0 && (
              <>
                {activeCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-emerald-500/20 border border-emerald-400/30 text-emerald-300 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                    {activeCount} active
                  </span>
                )}
                {inactiveCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-white/10 border border-white/20 text-slate-300 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    {inactiveCount} inactive
                  </span>
                )}
                {revokedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-red-500/20 border border-red-400/30 text-red-300 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    {revokedCount} revoked
                  </span>
                )}
              </>
            )}
            <RefreshButton onClick={load} loading={loading} />
            {isOperator && (
              <button
                onClick={() => setModal({ type: 'create' })}
                className="inline-flex items-center gap-1.5 bg-white text-slate-800 hover:bg-slate-100 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
              >
                <span className="text-base leading-none">+</span> New agent
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
        {error && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4">{error}</div>
        )}

        {/* Search + filters are applied together, server-side, only on click / Enter -
            so every filter spans all pages, not just the loaded one. */}
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-md">
            <svg className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35m0 0A7.5 7.5 0 105.6 5.6a7.5 7.5 0 0011.05 11.05z" />
            </svg>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyFilters()}
              placeholder="Search agents by hostname, ID, or tag…"
              className="w-full border border-gray-300 rounded-lg pl-9 pr-8 py-2 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-slate-500"
            />
            {search && (
              <button onClick={() => { setSearch(''); if (applied.q) { setApplied(a => ({ ...a, q: '' })); setOffset(0); } }} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700" aria-label="Clear search">✕</button>
            )}
          </div>
          <button onClick={applyFilters}
            className={`text-sm font-semibold px-3.5 py-2 rounded-lg text-white transition-colors shadow-sm ${filtersDirty ? 'bg-indigo-600 hover:bg-indigo-500 ring-2 ring-indigo-300' : 'bg-slate-800 hover:bg-slate-700'}`}>
            Search
          </button>
          {filtersDirty && <span className="text-xs text-indigo-600 whitespace-nowrap">Filters changed - click Search</span>}
          {!loading && (
            <span className="text-xs text-gray-500 whitespace-nowrap ml-auto">
              {total} agent{total !== 1 ? 's' : ''}{anyApplied ? ' matching' : ''}
            </span>
          )}
        </div>

        {/* Filter toolbar */}
        {!loading && (
          <div className="flex flex-wrap items-center gap-2 mb-4">
            {/* Tag dropdown */}
            {allTags.length > 0 && (
              <div className="relative" ref={tagPickerRef}>
                <button
                  onClick={() => setTagPickerOpen(p => !p)}
                  className={`inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border transition-all ${
                    tagFilters.size > 0
                      ? 'bg-slate-800 text-white border-slate-800'
                      : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                  }`}
                >
                  <svg className="w-3.5 h-3.5 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 6h.008v.008H6V6z" />
                  </svg>
                  Tags
                  {tagFilters.size > 0 && (
                    <span className="bg-white/20 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full leading-none">{tagFilters.size}</span>
                  )}
                  <svg className="w-3 h-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                  </svg>
                </button>
                {tagPickerOpen && (
                  <div className="absolute top-full left-0 mt-1.5 w-56 bg-white border border-gray-200 rounded-xl shadow-lg z-20 py-1 overflow-hidden">
                    <div className="px-3 py-2 border-b border-gray-100">
                      <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Filter by tag</p>
                    </div>
                    <div className="max-h-52 overflow-y-auto">
                      {allTags.map(tag => {
                        const [k, v] = tag.includes(':') ? tag.split(':', 2) : [tag, ''];
                        const active = tagFilters.has(tag);
                        return (
                          <button
                            key={tag}
                            onClick={() => toggleTag(tag)}
                            className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors ${active ? 'bg-slate-50' : 'hover:bg-gray-50'}`}
                          >
                            <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${active ? 'bg-slate-800 border-slate-800' : 'border-gray-300'}`}>
                              {active && <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>}
                            </div>
                            <span className="text-xs font-mono text-gray-700">
                              <span className="font-semibold">{k}</span>
                              {v && <><span className="text-gray-400">:</span>{v}</>}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    {tagFilters.size > 0 && (
                      <div className="border-t border-gray-100 px-3 py-2">
                        <button onClick={() => setTagFilters(new Set())} className="text-xs text-red-500 hover:text-red-700 font-medium">Clear tag filters</button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Type filter */}
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              className={`text-sm px-3 py-1.5 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-slate-400 ${
                typeFilter ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
              }`}
            >
              <option value="">All types</option>
              <option value="host">Host</option>
              <option value="k8s">Kubernetes</option>
            </select>

            {/* Mode filter */}
            <select
              value={modeFilter}
              onChange={e => setModeFilter(e.target.value)}
              className={`text-sm px-3 py-1.5 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-slate-400 ${
                modeFilter ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
              }`}
            >
              <option value="">All modes</option>
              <option value="wild">Wild</option>
              <option value="readonly">Read-only</option>
              <option value="approved">Approved</option>
            </select>

            {/* Access filter */}
            <select
              value={accessFilter}
              onChange={e => setAccessFilter(e.target.value)}
              className={`text-sm px-3 py-1.5 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-slate-400 ${
                accessFilter ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
              }`}
            >
              <option value="">All access levels</option>
              <option value="open">Open</option>
              <option value="elevated">Elevated</option>
              <option value="managed">Managed</option>
              <option value="restricted">Restricted</option>
            </select>

            {/* Fleet filter */}
            {fleets.length > 0 && (
              <select
                value={fleetFilter}
                onChange={e => setFleetFilter(e.target.value)}
                className={`text-sm px-3 py-1.5 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-slate-400 ${
                  fleetFilter ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                }`}
              >
                <option value="">All fleets</option>
                <option value="__none__">No fleet</option>
                {fleets.map(f => <option key={f.fleet_id} value={f.fleet_id}>{f.name}</option>)}
              </select>
            )}

            {/* Active filters summary */}
            {(draftFilterCount > 0 || anyApplied) && (
              <button onClick={clearFilters} className="text-xs text-red-500 hover:text-red-700 font-medium ml-1">Clear all</button>
            )}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <DataTable
              tableId="agents"
              columns={[
                { label: 'Hostname',     sortValue: a => a.hostname ?? a.agent_id, required: true },
                { label: 'Type',         sortValue: a => a.type ?? '' },
                { label: 'Fleet',        sortValue: a => fleetLabel(a.fleet_id) },
                { label: 'Status',       sortValue: a => a.status },
                { label: 'Mode',         sortValue: a => a.mode },
                { label: 'Access',       sortValue: a => a.access_level },
                { label: 'Docker',       sortValue: a => String(a.docker_detected ?? a.grant_docker ?? false) },
                { label: 'Service mgmt', sortValue: a => String(a.service_mgmt_detected ?? a.grant_service_mgmt ?? false) },
                { label: 'Cluster RBAC', sortValue: a => a.type === 'k8s' ? (a.k8s_permissions_drift ? '2' : a.k8s_permissions_reported ? '1' : '0') : '' },
                { label: 'Tags',         sortValue: a => (a.tags ?? []).join(',') },
                { label: 'Version',      sortValue: a => a.agent_version ?? '' },
                { label: 'Last seen',    sortValue: a => a.last_heartbeat_at ?? '' },
                { label: 'Agent ID',     sortValue: a => a.agent_id, defaultHidden: true },
                { label: 'Created',      sortValue: a => a.created_at ?? '', defaultHidden: true },
                ...(isOperator ? [{ label: '' }] : []),
              ]}
              rows={agents}
              fallback={
                <>
                  {anyApplied ? 'No agents match the current filters' : 'No agents registered'}
                  {isOperator && !anyApplied && (
                    <span> - <button onClick={() => setModal({ type: 'create' })} className="text-indigo-600 hover:underline">create one</button></span>
                  )}
                </>
              }
              renderRow={a => {
                const isActive = a.status === 'ACTIVE';
                const lastSeen = a.last_heartbeat_at ? Date.now() - new Date(a.last_heartbeat_at).getTime() : null;
                const stale = isActive && lastSeen !== null && lastSeen > 5 * 60 * 1000;
                return (
                <tr key={a.agent_id} className={`group border-l-2 transition-colors ${isActive ? 'border-l-emerald-400 hover:bg-emerald-50/30' : 'border-l-transparent hover:bg-gray-50'}`}>
                  <td className="px-4 py-3.5 cursor-pointer" onClick={() => setModal({ type: 'detail', agent: a })}>
                    <div className="flex items-center gap-2">
                      <svg className="w-3.5 h-3.5 text-gray-300 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0H3" />
                      </svg>
                      <span className="font-semibold text-gray-900 text-sm group-hover:text-indigo-600 transition-colors">
                        {a.hostname ?? <span className="text-gray-400 font-normal italic">unclaimed</span>}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {a.type ? <Badge value={a.type} /> : <span className="text-gray-400">-</span>}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {a.fleet_id
                      ? <span className="inline-flex items-center gap-1 text-xs font-medium text-violet-700 bg-violet-50 border border-violet-200 px-2 py-0.5 rounded-full">{fleetLabel(a.fleet_id)}</span>
                      : <span className="text-gray-400">-</span>}
                  </td>
                  <td className="px-4 py-3"><Badge value={a.status} /></td>
                  <td className="px-4 py-3"><Badge value={a.mode} /></td>
                  <td className="px-4 py-3"><Badge value={a.access_level} /></td>
                  <td className="px-4 py-3">
                    {a.type === 'k8s' ? (
                      <span className="text-gray-300 text-xs">n/a</span>
                    ) : (() => {
                      const fleet = a.fleet_id ? fleetById.get(a.fleet_id) : undefined;
                      const drift = a.status !== 'REVOKED' && fleet && !!a.grant_docker !== !!fleet.grant_docker && !memberMismatchAccepted(a, fleet);
                      return (
                        <CapabilityCell
                          granted={a.grant_docker}
                          detected={a.docker_detected}
                          onAcknowledge={isOperator ? () => handleAcknowledge(a, 'docker') : undefined}
                          fleetDrift={!!drift}
                          fleetWants={!!fleet?.grant_docker}
                        />
                      );
                    })()}
                  </td>
                  <td className="px-4 py-3">
                    {a.type === 'k8s' ? (
                      <span className="text-gray-300 text-xs">n/a</span>
                    ) : (() => {
                      const fleet = a.fleet_id ? fleetById.get(a.fleet_id) : undefined;
                      const drift = a.status !== 'REVOKED' && fleet && !!a.grant_service_mgmt !== !!fleet.grant_service_mgmt && !memberMismatchAccepted(a, fleet);
                      return (
                        <CapabilityCell
                          granted={a.grant_service_mgmt}
                          detected={a.service_mgmt_detected}
                          onAcknowledge={isOperator ? () => handleAcknowledge(a, 'service_mgmt') : undefined}
                          fleetDrift={!!drift}
                          fleetWants={!!fleet?.grant_service_mgmt}
                        />
                      );
                    })()}
                  </td>
                  <td className="px-4 py-3">
                    {a.type === 'k8s' ? (
                      <RbacCell
                        reported={a.k8s_permissions_reported}
                        drift={a.k8s_permissions_drift}
                        onOpen={() => setModal({ type: 'detail', agent: a })}
                      />
                    ) : (
                      <span className="text-gray-300 text-xs">n/a</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(a.tags ?? []).map(tag => {
                        const [k, v] = tag.includes(':') ? tag.split(':', 2) : [tag, ''];
                        return (
                          <span key={tag} className="inline-flex items-center gap-0.5 text-[10px] bg-slate-100 border border-slate-200 text-slate-600 px-1.5 py-0.5 rounded font-mono">
                            <span className="font-semibold">{k}</span>
                            {v && <><span className="text-slate-400">:</span><span>{v}</span></>}
                          </span>
                        );
                      })}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-400 font-mono text-xs">{a.agent_version ?? '-'}</td>
                  <td className={`px-4 py-3 text-xs whitespace-nowrap ${stale ? 'text-amber-600 font-medium' : 'text-gray-400'}`}>
                    {stale && <span className="mr-1">⚠</span>}{relTime(a.last_heartbeat_at)}
                  </td>
                  <td className="px-4 py-3 text-gray-400 font-mono text-xs">{a.agent_id}</td>
                  <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{a.created_at ? relTime(a.created_at) : '-'}</td>
                  {isOperator && (
                    <td className="px-4 py-3">
                      <AgentMenu agent={a} isOperator={isOperator} onAction={setModal} />
                    </td>
                  )}
                </tr>
                );
              }}
            />
            {total > PAGE && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 bg-gray-50/60 text-xs text-gray-600">
                <span>Showing {total === 0 ? 0 : offset + 1}–{Math.min(offset + agents.length, total)} of {total}</span>
                <div className="flex items-center gap-2">
                  <button disabled={offset === 0}
                    onClick={() => setOffset(o => Math.max(0, o - PAGE))}
                    className="px-2.5 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50">Prev</button>
                  <button disabled={offset + agents.length >= total}
                    onClick={() => setOffset(o => o + PAGE)}
                    className="px-2.5 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50">Next</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Modals */}
        {modal?.type === 'detail' && (
          <AgentDetailModal
            apiUrl={apiUrl}
            token={tenantToken}
            agent={modal.agent}
            isOperator={isOperator}
            backToFleet={modal.backFleetId && onBackToFleet
              ? { label: fleetLabel(modal.backFleetId), onBack: () => onBackToFleet(modal.backFleetId!) }
              : undefined}
            onClose={() => setModal(null)}
            onAction={setModal}
            onAcknowledge={isOperator ? cap => handleAcknowledge(modal.agent, cap) : undefined}
          />
        )}

        {modal?.type === 'create' && (
          <CreateAgentModal
            apiUrl={apiUrl}
            token={tenantToken}
            onClose={() => setModal(null)}
            onCreated={result => setModal({ type: 'install', agent: result as Agent & { install_token: string; commands: Record<string, string> } })}
          />
        )}

        {modal?.type === 'install' && (
          <InstallModal
            agent={modal.agent}
            onClose={closeAndReload}
          />
        )}

        {modal?.type === 'reissue' && (
          <ReissueModal
            apiUrl={apiUrl}
            token={tenantToken}
            agent={modal.agent}
            onClose={() => setModal(null)}
            onDone={result => setModal({ type: 'install', agent: { ...modal.agent, install_token: result.install_token, commands: result.commands } })}
          />
        )}

        {modal?.type === 'set-mode' && (
          <SetModeModal
            apiUrl={apiUrl}
            token={tenantToken}
            agent={modal.agent}
            onClose={() => setModal(null)}
            onDone={closeAndReload}
          />
        )}

        {modal?.type === 'set-tags' && (
          <SetTagsModal
            apiUrl={apiUrl}
            token={tenantToken}
            agent={modal.agent}
            onClose={() => setModal(null)}
            onDone={closeAndReload}
          />
        )}

        {modal?.type === 'run-agent' && (
          <RunCommandModal
            config={config}
            target={{ kind: 'agent', agent: modal.agent }}
            onClose={() => setModal(null)}
          />
        )}

        {modal?.type === 'confirm-revoke' && (
          <ConfirmModal
            title="Revoke agent"
            danger
            message={`Revoking will immediately disconnect "${modal.agent.hostname ?? modal.agent.agent_id}". The agent process will stop and cannot reconnect without a new install token.`}
            confirmLabel="Revoke"
            onClose={() => setModal(null)}
            onConfirm={async () => {
              await revokeTenantAgent(apiUrl, tenantToken, modal.agent.agent_id);
              closeAndReload();
            }}
          />
        )}

        {modal?.type === 'confirm-delete' && (
          <ConfirmModal
            title="Delete agent"
            danger
            message={`Soft-delete "${modal.agent.hostname ?? modal.agent.agent_id}"? The record is kept but the agent cannot reconnect. The agent must already be REVOKED.`}
            confirmLabel="Delete"
            onClose={() => setModal(null)}
            onConfirm={async () => {
              await deleteTenantAgent(apiUrl, tenantToken, modal.agent.agent_id);
              closeAndReload();
            }}
          />
        )}

        {modal?.type === 'confirm-remove' && (
          <ConfirmModal
            title="Remove agent"
            danger
            message={`Permanently remove "${modal.agent.hostname ?? modal.agent.agent_id}" from the database? This cannot be undone. The agent must already be DELETED.`}
            confirmLabel="Remove permanently"
            onClose={() => setModal(null)}
            onConfirm={async () => {
              await removeTenantAgent(apiUrl, tenantToken, modal.agent.agent_id);
              closeAndReload();
            }}
          />
        )}

        {modal?.type === 'rotate' && (
          <ConfirmModal
            title="Rotate auth token"
            message={`Request token rotation for "${modal.agent.hostname ?? modal.agent.agent_id}"? The agent will generate a new auth token on its next check-in.`}
            confirmLabel="Request rotation"
            onClose={() => setModal(null)}
            onConfirm={async () => {
              await requestAgentRotation(apiUrl, tenantToken, modal.agent.agent_id);
            }}
          />
        )}

        {modal?.type === 'detach-fleet' && (
          <ConfirmModal
            title="Remove from fleet"
            message={`Remove "${modal.agent.hostname ?? modal.agent.agent_id}" from its fleet? It becomes a standalone individual agent - it keeps running and regains individual controls (mode, tags, install token).`}
            confirmLabel="Remove from fleet"
            onClose={() => setModal(null)}
            onConfirm={async () => {
              if (modal.agent.fleet_id) await removeFleetMember(apiUrl, tenantToken, modal.agent.fleet_id, modal.agent.agent_id);
              closeAndReload();
            }}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent detail modal
// ---------------------------------------------------------------------------

function AgentDetailModal({
  apiUrl, token, agent, isOperator, backToFleet, onClose, onAction, onAcknowledge,
}: {
  apiUrl: string;
  token: string;
  agent: Agent;
  isOperator: boolean;
  backToFleet?: { label: string; onBack: () => void };
  onClose: () => void;
  onAction: (s: ModalState) => void;
  onAcknowledge?: (capability: 'docker' | 'service_mgmt' | 'k8s_permissions') => void;
}) {
  const [tab, setTab] = useState<'info' | 'history'>('info');
  const [history, setHistory] = useState<AgentHistory[]>([]);
  const [histLoading, setHistLoading] = useState(false);
  const [histError, setHistError] = useState('');
  const histLoaded = useRef(false);

  useEffect(() => {
    if (tab === 'history' && !histLoaded.current) {
      histLoaded.current = true;
      setHistLoading(true);
      setHistError('');
      listAgentHistory(apiUrl, token, agent.agent_id)
        .then(r => setHistory(r.history ?? []))
        .catch(() => setHistError('Failed to load history'))
        .finally(() => setHistLoading(false));
    }
  }, [tab, apiUrl, token, agent.agent_id]);

  const open = (ms: ModalState) => { onClose(); setTimeout(() => onAction(ms), 50); };
  const status = agent.status;
  const [rotateLoading, setRotateLoading] = useState(false);
  const [rotateDone, setRotateDone] = useState(false);
  const [rotateError, setRotateError] = useState('');

  const handleRequestRotation = async () => {
    setRotateLoading(true); setRotateError(''); setRotateDone(false);
    try {
      await requestAgentRotation(apiUrl, token, agent.agent_id);
      setRotateDone(true);
    } catch (e) {
      setRotateError((e as Error).message);
    } finally {
      setRotateLoading(false);
    }
  };

  return (
    <Modal
      wide
      title={
        <div className="flex items-baseline gap-2.5 flex-wrap">
          <span className="font-bold text-gray-900 text-base">{agent.hostname ?? '(unclaimed)'}</span>
          <span className="text-[11px] font-mono text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{agent.agent_id}</span>
        </div>
      }
      onClose={onClose}
    >
      <div className="space-y-4">
        {backToFleet && (
          <button onClick={backToFleet.onBack}
            className="inline-flex items-center gap-1 text-xs font-medium text-violet-700 hover:text-violet-900 -mt-1">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
            </svg>
            Back to fleet {backToFleet.label}
          </button>
        )}
        {/* Tabs */}
        <div className="flex gap-1 border-b border-gray-200 -mt-1">
          {(['info', 'history'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors capitalize ${
                tab === t ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {tab === 'info' && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2.5">
              <DetailField label="Status"><Badge value={agent.status} /></DetailField>
              <DetailField label="Type">
                {agent.type ? <Badge value={agent.type} /> : <span className="text-xs text-gray-400">-</span>}
              </DetailField>
              <DetailField label="Mode">
                <Badge value={agent.mode} />
              </DetailField>
              <DetailField label="Access level">
                <Badge value={agent.access_level} />
                {agent.type === 'k8s' && (
                  <p className="text-[10px] text-gray-400 mt-0.5 leading-tight">
                    Reflects policy mode; cluster RBAC is shown below.
                  </p>
                )}
              </DetailField>
              <DetailField label="Running as root">
                {agent.type === 'k8s' ? (
                  <span className="text-xs text-gray-400" title="Not applicable - the pod runs non-root; cluster access is governed by RBAC">n/a</span>
                ) : agent.running_as_root == null ? (
                  <span className="text-xs text-gray-400">-</span>
                ) : (
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${agent.running_as_root === 'true' ? 'bg-red-50 text-red-700 border border-red-200' : 'bg-emerald-50 text-emerald-700 border border-emerald-200'}`}>
                    {agent.running_as_root === 'true' ? 'Yes' : 'No'}
                  </span>
                )}
              </DetailField>
              <DetailField label="Agent version">
                <span className="text-xs font-mono text-gray-700">{agent.agent_version ?? '-'}</span>
              </DetailField>
              <DetailField label="Last heartbeat">
                <span className="text-xs text-gray-700">{relTime(agent.last_heartbeat_at)}</span>
              </DetailField>
              <DetailField label="Claimed at">
                <span className="text-xs text-gray-700">{fmtDate(agent.claimed_at)}</span>
              </DetailField>
              <DetailField label="Token issued">
                <span className="text-xs text-gray-700">{fmtDate(agent.token_issued_at)}</span>
              </DetailField>
            </div>

            {agent.type === 'k8s' && agent.k8s_permissions && (
              <K8sPermissionsView
                permissions={agent.k8s_permissions}
                acked={agent.k8s_permissions_acked}
                drift={agent.k8s_permissions_drift}
                onAcknowledge={onAcknowledge ? () => onAcknowledge('k8s_permissions') : undefined}
              />
            )}

            {(agent.tags ?? []).length > 0 && (
              <div className="bg-gray-50 rounded-lg px-3 py-2.5">
                <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2">Tags</p>
                <div className="flex flex-wrap gap-1.5">
                  {(agent.tags ?? []).map(tag => {
                    const [k, v] = tag.includes(':') ? tag.split(':', 2) : [tag, ''];
                    return (
                      <span key={tag} className="inline-flex items-center gap-1 bg-slate-100 border border-slate-200 text-slate-700 text-xs font-mono px-2 py-0.5 rounded-md">
                        <span className="font-semibold">{k}</span>
                        {v && <><span className="text-slate-400">:</span><span>{v}</span></>}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {tab === 'history' && (
          <div>
            {histLoading ? (
              <div className="flex justify-center py-10"><Spinner /></div>
            ) : histError ? (
              <p className="text-sm text-red-600 text-center py-6">{histError}</p>
            ) : history.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-10">No history recorded yet</p>
            ) : (
              <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                {history.map(h => (
                  <div key={h.history_id} className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg border border-gray-100">
                    <div className="flex items-center gap-1.5 shrink-0">
                      {h.from_status && (
                        <>
                          <span className="text-[11px] font-semibold text-gray-500 bg-gray-200 px-2 py-0.5 rounded">{h.from_status}</span>
                          <svg className="w-3 h-3 text-gray-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                          </svg>
                        </>
                      )}
                      <span className="text-[11px] font-semibold text-indigo-700 bg-indigo-100 px-2 py-0.5 rounded">{h.to_status}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      {h.note && <p className="text-xs text-gray-600 truncate">{h.note}</p>}
                      {h.triggered_by && <p className="text-[10px] text-gray-400">by {h.triggered_by}</p>}
                    </div>
                    <span className="text-[10px] text-gray-400 whitespace-nowrap shrink-0">{fmtDate(h.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Actions */}
        {isOperator && (
          <div className="flex flex-wrap gap-2 pt-3 border-t border-gray-100">
            {/* Fleet agents inherit mode/tags from the fleet and enroll via the
                fleet join token, so those individual controls are hidden. */}
            {!agent.fleet_id && (
              <>
                <button
                  onClick={() => open({ type: 'set-tags', agent })}
                  className="text-xs font-semibold text-gray-700 bg-gray-100 hover:bg-gray-200 px-3 py-1.5 rounded-lg transition-colors"
                >
                  Edit tags
                </button>
                <button
                  onClick={() => open({ type: 'set-mode', agent })}
                  className="text-xs font-semibold text-gray-700 bg-gray-100 hover:bg-gray-200 px-3 py-1.5 rounded-lg transition-colors"
                >
                  Set mode
                </button>
                <button
                  onClick={() => open({ type: 'reissue', agent })}
                  className="text-xs font-semibold text-gray-700 bg-gray-100 hover:bg-gray-200 px-3 py-1.5 rounded-lg transition-colors"
                >
                  Reissue token
                </button>
              </>
            )}
            {agent.fleet_id && (
              <button
                onClick={() => open({ type: 'detach-fleet', agent })}
                className="text-xs font-semibold text-amber-700 bg-amber-50 hover:bg-amber-100 border border-amber-200 px-3 py-1.5 rounded-lg transition-colors"
              >
                Remove from fleet
              </button>
            )}
            <button
              onClick={status === 'ACTIVE' ? handleRequestRotation : undefined}
              disabled={status !== 'ACTIVE' || rotateLoading || rotateDone}
              className={`text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                rotateDone
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100 border border-indigo-200'
              }`}
            >
              {rotateLoading ? 'Requesting…' : rotateDone ? 'Rotation requested ✓' : 'Rotate auth token'}
            </button>
            {rotateError && <p className="w-full text-xs text-red-600 mt-0.5">{rotateError}</p>}
            <div className="flex-1" />
            {status !== 'REVOKED' && status !== 'DELETED' && (
              <button
                onClick={() => open({ type: 'confirm-revoke', agent })}
                className="text-xs font-semibold text-red-600 bg-red-50 hover:bg-red-100 border border-red-200 px-3 py-1.5 rounded-lg transition-colors"
              >
                Revoke agent
              </button>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function DetailField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-50 rounded-lg px-3 py-2.5 border border-gray-100">
      <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{label}</p>
      <div>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent action menu
// ---------------------------------------------------------------------------

function AgentMenu({
  agent,
  isOperator,
  onAction,
}: {
  agent: Agent;
  isOperator: boolean;
  onAction: (s: ModalState) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const status = agent.status;

  const toggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom;
      // Open upward when the last rows don't have room below (and there's more
      // room above), so the menu isn't clipped off the bottom of the viewport.
      const openUp = spaceBelow < 300 && r.top > spaceBelow;
      setPos(openUp
        ? { bottom: window.innerHeight - r.top + 4, right: window.innerWidth - r.right }
        : { top: r.bottom + 4, right: window.innerWidth - r.right });
    }
    setOpen(v => !v);
  };

  return (
    <div className="flex justify-end">
      <button
        ref={btnRef}
        onClick={toggle}
        className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-700 transition-colors"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
          <path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM16 12a2 2 0 100-4 2 2 0 000 4z" />
        </svg>
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[170px] text-sm max-h-[80vh] overflow-y-auto"
            style={{ top: pos.top, bottom: pos.bottom, right: pos.right }}
          >
            <MenuItem onClick={() => { setOpen(false); onAction({ type: 'detail', agent }); }}>
              View details
            </MenuItem>
            {/* Run a command: only where the user has write access to this agent, and
                only when it's ACTIVE (a job can't dispatch otherwise). */}
            {agent.writable && status === 'ACTIVE' && (
              <MenuItem onClick={() => { setOpen(false); onAction({ type: 'run-agent', agent }); }}>
                Run command
              </MenuItem>
            )}
            {isOperator && (
              <>
                {/* Fleet agents inherit mode/tags from the fleet and enroll via the
                    fleet join token, so those individual controls don't apply. */}
                {!agent.fleet_id && (
                  <>
                    <MenuItem onClick={() => { setOpen(false); onAction({ type: 'set-tags', agent }); }}>
                      Edit tags
                    </MenuItem>
                    <MenuItem onClick={() => { setOpen(false); onAction({ type: 'set-mode', agent }); }}>
                      Set mode
                    </MenuItem>
                    <MenuItem onClick={() => { setOpen(false); onAction({ type: 'reissue', agent }); }}>
                      Reissue install token
                    </MenuItem>
                  </>
                )}
                <MenuItem
                  disabled={status !== 'ACTIVE'}
                  onClick={() => { setOpen(false); onAction({ type: 'rotate', agent }); }}
                >
                  Rotate auth token
                </MenuItem>
                {agent.fleet_id && (
                  <MenuItem onClick={() => { setOpen(false); onAction({ type: 'detach-fleet', agent }); }}>
                    Remove from fleet
                  </MenuItem>
                )}
                <div className="border-t border-gray-100 my-1" />
                {status !== 'REVOKED' && status !== 'DELETED' && (
                  <MenuItem danger onClick={() => { setOpen(false); onAction({ type: 'confirm-revoke', agent }); }}>
                    Revoke
                  </MenuItem>
                )}
                {status === 'REVOKED' && (
                  <MenuItem danger onClick={() => { setOpen(false); onAction({ type: 'confirm-delete', agent }); }}>
                    Delete
                  </MenuItem>
                )}
                {status === 'DELETED' && (
                  <MenuItem danger onClick={() => { setOpen(false); onAction({ type: 'confirm-remove', agent }); }}>
                    Remove permanently
                  </MenuItem>
                )}
              </>
            )}
          </div>
        </>,
        document.body,
      )}
    </div>
  );
}

function MenuItem({ children, onClick, danger, disabled }: { children: React.ReactNode; onClick: () => void; danger?: boolean; disabled?: boolean }) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={`w-full text-left px-4 py-2 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${danger ? 'text-red-600 hover:bg-red-50' : 'text-gray-700 hover:bg-gray-50'} ${disabled ? '' : ''}`}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Create agent modal
// ---------------------------------------------------------------------------

function CreateAgentModal({
  apiUrl, token, onClose, onCreated,
}: {
  apiUrl: string;
  token: string;
  onClose: () => void;
  onCreated: (result: unknown) => void;
}) {
  const [agentType, setAgentType] = useState<'host' | 'k8s'>('host');
  const [mode, setMode] = useState<Mode>('wild');
  const [grantSvc, setGrantSvc] = useState(false);
  const [grantDocker, setGrantDocker] = useState(false);
  // Installable versions for the picked type; '' means the default "Latest".
  const [version, setVersion] = useState('');
  const [versions, setVersions] = useState<string[]>([]);
  useEffect(() => {
    setVersion('');  // reset to Latest when the type changes
    listAgentVersions(apiUrl, token, agentType)
      .then(r => setVersions(r.versions ?? []))
      .catch(() => setVersions([]));
  }, [apiUrl, token, agentType]);
  const [tagPairs, setTagPairs] = useState<KVPair[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Restricted (agent-scoped) users who won't see the new agent unless granted.
  // Unrestricted users already have access, so they're excluded. Listing users is
  // admin-only; for non-admins the fetch fails and the section stays hidden.
  const [restrictedUsers, setRestrictedUsers] = useState<TenantUser[]>([]);
  // per-user grant level for the new agent: 'read' | 'write' (absent = no grant)
  const [grantCaps, setGrantCaps] = useState<Map<string, 'read' | 'write'>>(new Map());
  useEffect(() => {
    // Grantable = non-admin, non-revoked users who aren't already tenant-wide (so they
    // wouldn't otherwise see the new agent). Admins and unrestricted users are skipped.
    const needsGrant = (u: TenantUser) => {
      if (u.role === 'admin' || u.status === 'REVOKED') return false;
      if ((u.readwrite_agent_ids ?? []).includes('*')) return false;  // already read-write all
      const unrestricted = u.readwrite_agent_ids == null && u.readonly_agent_ids == null
        && u.readwrite_fleet_ids == null && u.readonly_fleet_ids == null;
      return !unrestricted;
    };
    listTenantUsers(apiUrl, token)
      .then(r => setRestrictedUsers((r.users ?? []).filter(needsGrant)))
      .catch(() => {/* non-admin or fetch failed: section stays hidden */});
  }, [apiUrl, token]);
  const setGrantCap = (id: string, cap: 'none' | 'read' | 'write') => setGrantCaps(prev => {
    const next = new Map(prev);
    cap === 'none' ? next.delete(id) : next.set(id, cap);
    return next;
  });

  const addTag = () => setTagPairs(p => [...p, { key: '', value: '' }]);
  const removeTag = (idx: number) => setTagPairs(p => p.filter((_, i) => i !== idx));
  const updateTag = (idx: number, field: 'key' | 'value', val: string) =>
    setTagPairs(p => p.map((pair, i) => i === idx ? { ...pair, [field]: val } : pair));

  const submit = async () => {
    setLoading(true); setError('');
    try {
      const isK8s = agentType === 'k8s';
      // Docker / service-mgmt grants are host-only; the backend ignores them for
      // k8s, but we omit them here too so intent is clear.
      const grantWrite = [...grantCaps].filter(([, c]) => c === 'write').map(([id]) => id);
      const grantRead = [...grantCaps].filter(([, c]) => c === 'read').map(([id]) => id);
      const result = await createTenantAgent(
        apiUrl, token, mode,
        isK8s ? undefined : grantSvc,
        isK8s ? undefined : grantDocker,
        agentType,
        grantWrite,
        version || undefined,
        grantRead,
      );
      const tags = serializePairs(tagPairs);
      if (tags.length > 0) {
        await setTenantAgentTags(apiUrl, token, (result as { agent_id: string }).agent_id, tags);
      }
      onCreated(result);
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  };

  return (
    <Modal wide title="New agent" onClose={onClose}>
      <div className="space-y-5">
        {/* Agent type */}
        <div>
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2.5">Agent type</p>
          <div className="grid grid-cols-2 gap-2.5">
            {([
              { t: 'host' as const, label: 'Host', desc: 'A machine / VM. Installs via curl + systemd or launchd.' },
              { t: 'k8s' as const, label: 'Kubernetes', desc: 'A cluster. Installs via Helm; access is controlled by RBAC.' },
            ]).map(({ t, label, desc }) => (
              <button
                key={t}
                type="button"
                onClick={() => setAgentType(t)}
                className={`flex flex-col items-start gap-1 p-4 rounded-xl border-2 transition-all text-left ${
                  agentType === t ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                <p className={`text-sm font-bold leading-tight ${agentType === t ? 'text-gray-900' : 'text-gray-700'}`}>{label}</p>
                <p className="text-[11px] text-gray-500 leading-tight">{desc}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Version */}
        <div>
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2.5">Version</p>
          <select
            value={version}
            onChange={e => setVersion(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          >
            <option value="">Latest{versions.length ? ` (${versions[0]})` : ''}</option>
            {versions.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <p className="text-[11px] text-gray-500 mt-1.5">
            {agentType === 'k8s'
              ? 'Pins the Helm chart version in the install command. Latest installs the newest published chart.'
              : 'Pins the agent binary version in the install command. Latest tracks the newest release.'}
          </p>
        </div>

        {/* Mode selection */}
        <div>
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2.5">Execution mode</p>
          <div className="grid grid-cols-3 gap-2.5">
            {([
              { m: 'wild' as Mode, icon: (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                </svg>
              ), color: 'text-amber-500', activeBg: 'border-amber-400 bg-amber-50' },
              { m: 'readonly' as Mode, icon: (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              ), color: 'text-sky-500', activeBg: 'border-sky-400 bg-sky-50' },
              { m: 'approved' as Mode, icon: (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                </svg>
              ), color: 'text-emerald-500', activeBg: 'border-emerald-400 bg-emerald-50' },
            ] as const).map(({ m, icon, color, activeBg }) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`flex flex-col items-start gap-2 p-4 rounded-xl border-2 transition-all text-left ${
                  mode === m ? activeBg : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                <span className={mode === m ? color : 'text-gray-400'}>{icon}</span>
                <div>
                  <p className={`text-sm font-bold leading-tight ${mode === m ? 'text-gray-900' : 'text-gray-700'}`}>
                    {MODE_LABEL[m]}
                  </p>
                  <p className="text-[11px] text-gray-500 leading-tight mt-0.5">{MODE_DESC[m]}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Permissions (host only - k8s access is controlled by RBAC) */}
        {agentType === 'k8s' ? (
          <div className="flex items-start gap-2.5 bg-sky-50 border border-sky-200 rounded-xl px-3.5 py-3 text-xs text-sky-800">
            <svg className="w-4 h-4 shrink-0 mt-0.5 text-sky-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>
              Access is controlled by <strong>Kubernetes RBAC</strong>, configured in the Helm
              chart (<code>clusterAccess</code>). Docker and service-management grants don't apply.
              You'll see the agent's effective permissions here once it connects.
            </span>
          </div>
        ) : (
        <div>
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2.5">Permissions</p>
          <div className="space-y-2">
            <label className={`flex items-center gap-3 p-3 rounded-xl border-2 cursor-pointer transition-all ${grantSvc ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'}`}>
              <input type="checkbox" checked={grantSvc} onChange={e => setGrantSvc(e.target.checked)} className="w-4 h-4" />
              <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${grantSvc ? 'bg-indigo-100' : 'bg-gray-100'}`}>
                <svg className={`w-4 h-4 ${grantSvc ? 'text-indigo-600' : 'text-gray-400'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3" />
                </svg>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-800">Service management</p>
                <p className="text-xs text-gray-500">Start/stop systemd or launchd services</p>
              </div>
            </label>
            <label className={`flex items-center gap-3 p-3 rounded-xl border-2 cursor-pointer transition-all ${grantDocker ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'}`}>
              <input type="checkbox" checked={grantDocker} onChange={e => setGrantDocker(e.target.checked)} className="w-4 h-4" />
              <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${grantDocker ? 'bg-indigo-100' : 'bg-gray-100'}`}>
                <svg className={`w-4 h-4 ${grantDocker ? 'text-indigo-600' : 'text-gray-400'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" />
                </svg>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-800">Docker access</p>
                <p className="text-xs text-gray-500">Manage containers and images</p>
              </div>
            </label>
          </div>
        </div>
        )}

        {/* Sudo notice (host install only) */}
        {agentType === 'host' && (
          <div className="flex items-start gap-2.5 bg-amber-50 border border-amber-200 rounded-xl px-3.5 py-3 text-xs text-amber-800">
            <svg className="w-4 h-4 shrink-0 mt-0.5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
            </svg>
            <span>
              The install command requires <strong>sudo</strong> - the agent installs to system directories.
              {(grantDocker || grantSvc) && ' Docker and service management grants also configure group membership and sudoers rules.'}
            </span>
          </div>
        )}

        {/* Tags */}
        <div>
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">Tags <span className="font-normal normal-case text-gray-400">(optional)</span></p>
          <div className="space-y-2">
            {tagPairs.map((pair, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  value={pair.key}
                  onChange={e => updateTag(idx, 'key', e.target.value)}
                  placeholder="key"
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
                <span className="text-gray-400 font-mono text-sm shrink-0">:</span>
                <input
                  value={pair.value}
                  onChange={e => updateTag(idx, 'value', e.target.value)}
                  placeholder="value"
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
                <button onClick={() => removeTag(idx)} className="w-7 h-7 flex items-center justify-center text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors shrink-0 text-sm">✕</button>
              </div>
            ))}
            <button onClick={addTag} className="flex items-center gap-1.5 text-sm text-indigo-600 hover:text-indigo-800 transition-colors">
              <span className="text-base leading-none">+</span> Add tag
            </button>
          </div>
        </div>

        {/* Grant access to restricted users. Unrestricted users already see every
            agent, so only agent-scoped users are listed here. */}
        {restrictedUsers.length > 0 && (
          <div>
            <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">
              Grant access <span className="font-normal normal-case text-gray-400">(optional)</span>
            </p>
            <p className="text-[11px] text-gray-500 mb-2.5">
              Every non-admin user is scoped and won't see this agent unless you grant it -
              as <span className="text-sky-600 font-medium">Read</span> or <span className="text-indigo-600 font-medium">R/W</span> (read-write). Admins are tenant-wide and already have access.
            </p>
            <div className="border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100 max-h-56 overflow-y-auto">
              {restrictedUsers.map(u => {
                const cap = grantCaps.get(u.user_id) ?? 'none';
                return (
                  <div key={u.user_id}
                    className={`flex items-center gap-3 px-3 py-2.5 transition-colors ${cap !== 'none' ? 'bg-indigo-50/60' : 'hover:bg-gray-50'}`}>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gray-800 leading-tight truncate">
                        {u.name || u.username} <span className="text-gray-400 font-mono text-xs">@{u.username}</span>
                      </p>
                      <p className="text-[11px] text-gray-500 leading-tight">
                        {u.role} · currently {u.readwrite_agent_ids?.length ?? 0} agent{(u.readwrite_agent_ids?.length ?? 0) !== 1 ? 's' : ''}
                      </p>
                    </div>
                    <div className="inline-flex rounded-md border border-gray-200 overflow-hidden text-[11px] shrink-0">
                      {(['none', 'read', 'write'] as const).map(opt => (
                        <button key={opt} type="button" onClick={() => setGrantCap(u.user_id, opt)}
                          className={`px-2 py-1 font-medium transition-colors ${cap === opt
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
            {grantCaps.size > 0 && (
              <p className="text-[11px] text-indigo-600 mt-1.5">{grantCaps.size} user{grantCaps.size !== 1 ? 's' : ''} will be granted access to this agent.</p>
            )}
          </div>
        )}

        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1 border-t border-gray-100">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-5 py-2 rounded-lg disabled:opacity-50 transition-colors shadow-sm"
          >
            {loading && <Spinner className="h-4 w-4" />}
            Create agent
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Install commands modal
// ---------------------------------------------------------------------------

function InstallModal({
  agent,
  onClose,
}: {
  agent: Agent & { install_token?: string; commands?: Record<string, string> };
  onClose: () => void;
}) {
  return (
    <Modal title="Install agent" onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
          <p className="text-sm text-amber-800 font-medium">Save the install token now</p>
          <p className="text-xs text-amber-700 mt-0.5">This token is shown only once and expires in 24 hours.</p>
        </div>
        {agent.install_token && (
          <TokenBox label="Install token" value={agent.install_token} />
        )}
        {(() => {
          const cmd = agent.commands?.helm ?? agent.commands?.agent;
          if (!cmd) return null;
          const isHelm = !!agent.commands?.helm;
          return (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                {isHelm ? 'Helm install command' : 'Install command'}
              </p>
              {isHelm && (
                <p className="text-[11px] text-gray-500 mb-1.5">
                  Runs in your cluster. Tune cluster access with <code>--set clusterAccess.*</code> (defaults to read-only).
                </p>
              )}
              <div className="relative bg-gray-900 rounded-lg p-3 pr-10">
                <code className="text-xs text-green-400 break-all whitespace-pre-wrap">{cmd}</code>
                <CopyButton text={cmd} className="absolute top-2 right-2" />
              </div>
            </div>
          );
        })()}
        <div className="flex justify-end pt-1">
          <button
            onClick={onClose}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Reissue install token modal
// ---------------------------------------------------------------------------

type ReissueResult = { install_token: string; commands: Record<string, string> };

function ReissueModal({
  apiUrl, token, agent, onClose, onDone,
}: {
  apiUrl: string;
  token: string;
  agent: Agent;
  onClose: () => void;
  onDone: (result: ReissueResult) => void;
}) {
  const [force, setForce] = useState(false);
  const [grantSvc, setGrantSvc] = useState(agent.grant_service_mgmt ?? false);
  const [grantDocker, setGrantDocker] = useState(agent.grant_docker ?? false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try {
      const result = await reissueTenantInstallToken(apiUrl, token, agent.agent_id, force || undefined, grantSvc, grantDocker);
      onDone(result);
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  };

  return (
    <Modal title="Reissue install token" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-600">
          A new install token will be generated for <strong>{agent.hostname ?? agent.agent_id}</strong>.
          The previous token will be invalidated.
        </p>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" checked={grantSvc} onChange={e => setGrantSvc(e.target.checked)} className="w-4 h-4" />
            <span>Grant systemctl / service management access</span>
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" checked={grantDocker} onChange={e => setGrantDocker(e.target.checked)} className="w-4 h-4" />
            <span>Grant Docker access</span>
          </label>
        </div>
        <div className="flex items-start gap-2.5 bg-amber-50 border border-amber-200 rounded-xl px-3.5 py-3 text-xs text-amber-800">
          <svg className="w-4 h-4 shrink-0 mt-0.5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
          </svg>
          <span>
            The install command requires <strong>sudo</strong> - the agent installs to system directories.
            {(grantDocker || grantSvc) && ' Docker and service management grants also configure group membership and sudoers rules.'}
          </span>
        </div>
        {agent.status === 'ACTIVE' && (
          <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} className="mt-0.5" />
            <span>Force reissue (agent is ACTIVE - this will disconnect it immediately)</span>
          </label>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading || (agent.status === 'ACTIVE' && !force)}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition-colors"
          >
            {loading && <Spinner className="h-4 w-4" />}
            Reissue
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Set mode modal
// ---------------------------------------------------------------------------

function SetModeModal({
  apiUrl, token, agent, onClose, onDone,
}: {
  apiUrl: string;
  token: string;
  agent: Agent;
  onClose: () => void;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<Mode>((agent.mode as Mode) ?? 'wild');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setLoading(true); setError('');
    try {
      await setTenantAgentMode(apiUrl, token, agent.agent_id, mode);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  };

  return (
    <Modal title="Set mode" onClose={onClose}>
      <div className="space-y-4">
        <div className="space-y-2">
          {MODES.map(m => (
            <label key={m} className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50 transition-colors">
              <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} className="mt-0.5" />
              <div>
                <p className="text-sm font-medium text-gray-800">{MODE_LABEL[m]}</p>
                <p className="text-xs text-gray-500">{MODE_DESC[m]}</p>
              </div>
            </label>
          ))}
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition-colors"
          >
            {loading && <Spinner className="h-4 w-4" />}
            Save
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Set tags modal - key-value pair editor
// ---------------------------------------------------------------------------

interface KVPair { key: string; value: string }

function parseTags(tags: string[]): KVPair[] {
  return tags.map(t => {
    const i = t.indexOf(':');
    return i >= 0 ? { key: t.slice(0, i), value: t.slice(i + 1) } : { key: t, value: '' };
  });
}

function serializePairs(pairs: KVPair[]): string[] {
  return pairs
    .filter(p => p.key.trim())
    .map(p => p.value.trim() ? `${p.key.trim()}:${p.value.trim()}` : p.key.trim());
}

function SetTagsModal({
  apiUrl, token, agent, onClose, onDone,
}: {
  apiUrl: string;
  token: string;
  agent: Agent;
  onClose: () => void;
  onDone: () => void;
}) {
  const [pairs, setPairs] = useState<KVPair[]>(
    agent.tags && agent.tags.length > 0 ? parseTags(agent.tags) : [{ key: '', value: '' }],
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const addPair = () => setPairs(p => [...p, { key: '', value: '' }]);
  const removePair = (idx: number) => setPairs(p => p.filter((_, i) => i !== idx));
  const updatePair = (idx: number, field: 'key' | 'value', val: string) =>
    setPairs(p => p.map((pair, i) => i === idx ? { ...pair, [field]: val } : pair));

  const submit = async () => {
    const tags = serializePairs(pairs);
    setLoading(true); setError('');
    try {
      await setTenantAgentTags(apiUrl, token, agent.agent_id, tags);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  };

  return (
    <Modal title="Edit tags" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-xs text-gray-500">Tags are key:value pairs used for filtering and grouping agents.</p>
        <div className="space-y-2">
          {pairs.map((pair, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                value={pair.key}
                onChange={e => updatePair(idx, 'key', e.target.value)}
                placeholder="key"
                autoFocus={idx === 0 && !pair.key}
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              <span className="text-gray-400 font-mono text-sm shrink-0">:</span>
              <input
                value={pair.value}
                onChange={e => updatePair(idx, 'value', e.target.value)}
                placeholder="value"
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              <button
                onClick={() => removePair(idx)}
                className="w-7 h-7 flex items-center justify-center text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors shrink-0 text-sm"
                title="Remove"
              >
                ✕
              </button>
            </div>
          ))}
          <button
            onClick={addPair}
            className="flex items-center gap-1.5 text-sm text-indigo-600 hover:text-indigo-800 transition-colors mt-1"
          >
            <span className="text-base leading-none">+</span> Add tag
          </button>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition-colors"
          >
            {loading && <Spinner className="h-4 w-4" />}
            Save tags
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Generic confirm modal
// ---------------------------------------------------------------------------

function ConfirmModal({
  title, message, confirmLabel, danger, onClose, onConfirm,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
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
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-600">{message}</p>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-2">Cancel</button>
          <button
            onClick={submit}
            disabled={loading}
            className={`flex items-center gap-2 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50 transition-colors ${
              danger ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'
            }`}
          >
            {loading && <Spinner className="h-4 w-4" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
