import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { Agent, Fleet, FleetToken, FleetWavePolicy, TenantConfig } from '../types';
import { listFleets, listFleetAgents, createFleet, updateFleet, rotateFleetToken, revokeFleet, deleteFleet, removeFleetMember, revokeTenantAgent, deleteTenantAgent, reconcileFleetGrants, acceptFleetGrantMismatch } from '../api';
import { WavePolicyRW } from '../components/WavePolicyEditor';
import { Modal } from '../components/Modal';
import { RunCommandModal } from '../components/RunCommandModal';
import { Spinner } from '../components/Spinner';
import { RefreshButton } from '../components/RefreshButton';
import { CopyButton, TokenBox } from '../components/CopyButton';
import { Badge } from '../components/Badge';
import { relTime, memberMismatchFlagged } from '../utils';

const MODES = ['wild', 'readonly', 'approved'] as const;

type FleetUpdateBody = Partial<{
  mode: string; tags: string[]; reap_after_seconds: number | null; max_fanout: number | null;
  grant_service_mgmt: boolean; grant_docker: boolean; wave_policy: FleetWavePolicy | null;
}>;

// A fleet member is *flagged* when its grants mismatch the fleet AND the divergence
// hasn't been accepted - i.e. it still needs resolving (reconcile the host, or accept
// the exception). See ../utils. This is distinct from a capability/RBAC *acknowledge*
// (which accepts observed reality) - reconciling asserts a fix, verified against
// detection; accepting keeps the member's real grants but stops flagging it.
function grantsLabel(sm?: boolean, dk?: boolean): string {
  return [sm && 'service-mgmt', dk && 'docker'].filter(Boolean).join(', ') || 'none';
}

type ColDef = { key: string; label: string; required?: boolean };
const FLEET_COLS: ColDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'fleet_id', label: 'Fleet ID' },
  { key: 'mode', label: 'Mode' },
  { key: 'members', label: 'Members' },
  { key: 'grants', label: 'Grants' },
  { key: 'reap', label: 'Reap' },
  { key: 'max_fanout', label: 'Max fan-out' },
  { key: 'status', label: 'Status' },
  { key: 'created', label: 'Created' },
];
const MEMBER_COLS: ColDef[] = [
  { key: 'host', label: 'Host', required: true },
  { key: 'status', label: 'Status' },
  { key: 'grants', label: 'Grants' },
  { key: 'version', label: 'Version' },
  { key: 'last_seen', label: 'Last seen' },
  { key: 'agent_id', label: 'Agent ID' },
];

// Column order (drag) + visibility (picker), persisted per table in localStorage.
function useColumns(storageKey: string, cols: ColDef[]) {
  const allKeys = cols.map(c => c.key);
  const [order, setOrder] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey + '_order') || 'null');
      if (Array.isArray(saved)) {
        const merged = saved.filter((k: string) => allKeys.includes(k));
        for (const k of allKeys) if (!merged.includes(k)) merged.push(k);
        return merged;
      }
    } catch { /* ignore */ }
    return allKeys;
  });
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(storageKey + '_hidden') || '[]')); } catch { return new Set(); }
  });
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    try { return JSON.parse(localStorage.getItem(storageKey + '_widths') || '{}') || {}; } catch { return {}; }
  });
  const save = (o: string[], h: Set<string>, w?: Record<string, number>) => {
    try {
      localStorage.setItem(storageKey + '_order', JSON.stringify(o));
      localStorage.setItem(storageKey + '_hidden', JSON.stringify([...h]));
      if (w) localStorage.setItem(storageKey + '_widths', JSON.stringify(w));
    } catch { /* ignore */ }
  };
  const toggle = (k: string) => setHidden(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); save(order, n); return n; });
  const showAll = () => setHidden(() => { const n = new Set<string>(); save(order, n); return n; });
  const move = (from: string, to: string) => setOrder(prev => {
    if (from === to) return prev;
    const arr = prev.filter(k => k !== from);
    const ti = arr.indexOf(to);
    if (ti < 0) return prev;
    arr.splice(ti, 0, from);   // drop before the target column
    save(arr, hidden);
    return arr;
  });
  const resize = (k: string, w: number) => setWidths(prev => { const n = { ...prev, [k]: w }; save(order, hidden, n); return n; });
  const visible = order.filter(k => !hidden.has(k));
  return { cols, order, visible, hidden, widths, toggle, showAll, move, resize };
}
type Columns = ReturnType<typeof useColumns>;

// A header cell that reorders columns left/right (drag) and resizes (drag the right edge).
function ReorderableTh({ colKey, dragKey, onDrop, onResize, width, className, children }: {
  colKey: string; dragKey: React.MutableRefObject<string | null>;
  onDrop: (from: string, to: string) => void; onResize: (key: string, w: number) => void;
  width?: number; className?: string; children: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const [resizing, setResizing] = useState(false);
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const th = (e.currentTarget as HTMLElement).closest('th') as HTMLElement;
    const startX = e.clientX;
    const startW = th.offsetWidth;
    setResizing(true);
    const onMove = (ev: MouseEvent) => onResize(colKey, Math.max(60, startW + ev.clientX - startX));
    const onUp = () => {
      setResizing(false);
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };
  return (
    <th
      draggable={!resizing}
      onDragStart={e => { dragKey.current = colKey; if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; }}
      onDragEnd={() => { dragKey.current = null; setOver(false); }}
      onDragOver={e => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={e => { e.preventDefault(); if (dragKey.current) onDrop(dragKey.current, colKey); dragKey.current = null; setOver(false); }}
      title="Drag to reorder - drag the right edge to resize"
      style={width ? { minWidth: width, width } : undefined}
      className={`relative cursor-move select-none whitespace-nowrap ${over ? 'bg-violet-100' : ''} ${className ?? ''}`}
    >
      {children}
      <div
        onMouseDown={startResize}
        onClick={e => e.stopPropagation()}
        className="absolute right-0 top-0 h-full w-1 cursor-col-resize hover:bg-violet-300 transition-colors"
      />
    </th>
  );
}

function formatReap(secs: number): string {
  if (secs % 3600 === 0) return `${secs / 3600}h`;
  if (secs % 60 === 0) return `${secs / 60}m`;
  return `${secs}s`;
}
function reapPhrase(secs: number): string {
  if (secs % 3600 === 0) { const h = secs / 3600; return `${h} hour${h !== 1 ? 's' : ''}`; }
  if (secs % 60 === 0) { const m = secs / 60; return `${m} minute${m !== 1 ? 's' : ''}`; }
  return `${secs} seconds`;
}

interface KVPair { key: string; value: string }
function parseTags(tags: string[] = []): KVPair[] {
  return tags.map(t => { const i = t.indexOf(':'); return i >= 0 ? { key: t.slice(0, i), value: t.slice(i + 1) } : { key: t, value: '' }; });
}
function serializePairs(pairs: KVPair[]): string[] {
  return pairs.filter(p => p.key.trim() && p.value.trim()).map(p => `${p.key.trim()}:${p.value.trim()}`);
}
function TagsEditor({ pairs, setPairs }: { pairs: KVPair[]; setPairs: (p: KVPair[]) => void }) {
  const upd = (i: number, f: 'key' | 'value', v: string) => setPairs(pairs.map((p, idx) => idx === i ? { ...p, [f]: v } : p));
  return (
    <div className="space-y-2">
      {pairs.map((pair, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <input value={pair.key} onChange={e => upd(idx, 'key', e.target.value)} placeholder="key"
            className="flex-1 border border-gray-300 rounded-md px-2.5 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-violet-500" />
          <span className="text-gray-400 font-mono text-sm">:</span>
          <input value={pair.value} onChange={e => upd(idx, 'value', e.target.value)} placeholder="value"
            className="flex-1 border border-gray-300 rounded-md px-2.5 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-violet-500" />
          <button type="button" onClick={() => setPairs(pairs.filter((_, i) => i !== idx))} className="w-7 h-7 flex items-center justify-center text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-md">✕</button>
        </div>
      ))}
      <button type="button" onClick={() => setPairs([...pairs, { key: '', value: '' }])} className="flex items-center gap-1.5 text-sm text-violet-600 hover:text-violet-800"><span className="text-base leading-none">+</span> Add tag</button>
    </div>
  );
}

type Modals =
  | { type: 'create' }
  | { type: 'token'; token: FleetToken; name: string; rotated: boolean }
  | { type: 'detail'; fleet: Fleet }
  | { type: 'edit'; fleet: Fleet }
  // pendingUpdate: a fleet edit (typically a grant change) to commit *before* rotating,
  // so it's only saved once the operator gets the new install command. Discarded if
  // they cancel the rotate step.
  | { type: 'rotate'; fleet: Fleet; pendingUpdate?: FleetUpdateBody }
  // agent set = acknowledge just that one member; omitted = every drifted member.
  | { type: 'acknowledge-grants'; fleet: Fleet; driftCount: number; agent?: Agent }
  | { type: 'revoke'; fleet: Fleet }
  | { type: 'delete'; fleet: Fleet }
  | { type: 'run'; fleet: Fleet }
  | { type: 'remove-member'; fleet: Fleet; agent: Agent }
  | { type: 'revoke-member'; fleet: Fleet; agent: Agent }
  | { type: 'delete-member'; fleet: Fleet; agent: Agent }
  | null;

// One page of a fleet's members, plus the total so the accordion can page through them.
type MemberPage = { agents: Agent[]; total: number; offset: number };
const MEMBER_PAGE = 20;

export function FleetsPage({ config, onOpenAgent, focusFleetId, onFocusFleetConsumed }: {
  config: TenantConfig;
  onOpenAgent?: (agentId: string, fromFleetId?: string) => void;
  focusFleetId?: string | null;
  onFocusFleetConsumed?: () => void;
}) {
  const { apiUrl, tenantToken } = config;
  // Developers get a read-only view (of the fleets they're granted); operators+ manage.
  const isOperator = config.role === 'admin' || config.role === 'operator';
  const [fleets, setFleets] = useState<Fleet[]>([]);
  // Members are loaded per-fleet, lazily on expand - the Fleets page never loads every
  // agent in the tenant (which doesn't scale to large autoscaling fleets). Collapsed
  // rows render from the fleet-list stat counts; the accordion loads its members one
  // page at a time (a fleet backing an autoscaling group can have thousands).
  const [membersByFleet, setMembersByFleet] = useState<Map<string, MemberPage>>(new Map());
  const [loadingMembers, setLoadingMembers] = useState<Set<string>>(new Set());
  const [defaultReap, setDefaultReap] = useState(1800);
  const [defaultMaxFanout, setDefaultMaxFanout] = useState(25);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [modal, setModal] = useState<Modals>(null);
  // Name/id search + pagination, both server-side. Search is applied on the button /
  // Enter (not while typing), matching the Agents & Jobs pages.
  const PAGE = 20;
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const applySearch = () => { setQuery(search.trim()); setOffset(0); };
  const fleetCols = useColumns('fleets_cols', FLEET_COLS);
  const memberCols = useColumns('fleet_members_cols', MEMBER_COLS);
  const dragKey = useRef<string | null>(null);

  const loadMembers = useCallback((fleetId: string, offset = 0) => {
    setLoadingMembers(prev => new Set(prev).add(fleetId));
    listFleetAgents(apiUrl, tenantToken, fleetId, { limit: String(MEMBER_PAGE), offset: String(offset) })
      .then(r => setMembersByFleet(prev => new Map(prev).set(fleetId, {
        agents: r.agents ?? [], total: r.total ?? (r.agents?.length ?? 0), offset,
      })))
      .catch(() => {})
      .finally(() => setLoadingMembers(prev => { const n = new Set(prev); n.delete(fleetId); return n; }));
  }, [apiUrl, tenantToken]);

  const load = useCallback(() => {
    setLoading(true);
    const params: Record<string, string> = { limit: String(PAGE), offset: String(offset) };
    if (query) params.q = query;
    listFleets(apiUrl, tenantToken, params)
      .then(r => {
        setFleets(r.fleets);
        setTotal(r.total ?? r.fleets.length);
        setDefaultReap(r.default_reap_after_seconds);
        setDefaultMaxFanout(r.default_max_fanout ?? 25);
      })
      .finally(() => setLoading(false));
    // Refresh members of any already-expanded fleets (e.g. after an action), keeping
    // each fleet on the member page it was viewing.
    setMembersByFleet(cur => {
      setExpanded(prev => { prev.forEach(id => loadMembers(id, cur.get(id)?.offset ?? 0)); return prev; });
      return cur;
    });
  }, [apiUrl, tenantToken, loadMembers, query, offset]);
  useEffect(() => { load(); }, [load]);

  // Returning from an agent opened out of a fleet's detail: reopen that fleet's detail.
  useEffect(() => {
    if (!focusFleetId) return;
    const target = fleets.find(f => f.fleet_id === focusFleetId);
    if (target) {
      setModal({ type: 'detail', fleet: target });
      onFocusFleetConsumed?.();
    }
  }, [focusFleetId, fleets, onFocusFleetConsumed]);

  const toggle = (id: string) => setExpanded(prev => {
    const n = new Set(prev);
    if (n.has(id)) { n.delete(id); }
    else { n.add(id); if (!membersByFleet.has(id) && !loadingMembers.has(id)) loadMembers(id); }
    return n;
  });

  const activeCount = fleets.filter(f => f.status === 'ACTIVE').length;

  const afterAction = () => { setModal(null); load(); };

  return (
    <div className="min-h-full bg-slate-50">
      <div className="bg-gradient-to-r from-violet-700 to-violet-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 17.25v-.228a4.5 4.5 0 00-.12-1.03l-2.268-9.64a3.375 3.375 0 00-3.285-2.602H7.923a3.375 3.375 0 00-3.285 2.602l-2.268 9.64a4.5 4.5 0 00-.12 1.03v.228m19.5 0a3 3 0 01-3 3H5.25a3 3 0 01-3-3m19.5 0a3 3 0 00-3-3H5.25a3 3 0 00-3 3m16.5 0h.008v.008h-.008v-.008zm-3 0h.008v.008h-.008v-.008z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Fleets</h1>
              <p className="text-sm text-violet-200">Reusable-join-token groups of host agents - for autoscaling groups</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') applySearch(); }}
                placeholder="Search fleets…"
                className="border border-white/20 bg-white/10 text-white placeholder-violet-200 rounded-lg px-3 py-1.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-white/40"
              />
              <button onClick={applySearch}
                className="text-sm text-violet-700 bg-white hover:bg-violet-50 rounded-lg px-3 py-1.5 font-semibold">Search</button>
              {query && (
                <button onClick={() => { setSearch(''); setQuery(''); }}
                  className="text-sm text-violet-200 hover:text-white" aria-label="Clear search">✕</button>
              )}
            </div>
            {!loading && activeCount > 0 && (
              <span className="inline-flex items-center gap-1.5 bg-emerald-500/20 border border-emerald-400/30 text-emerald-300 text-xs font-semibold px-3 py-1.5 rounded-lg">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" /> {activeCount} active
              </span>
            )}
            <RefreshButton onClick={load} loading={loading} />
            {isOperator && (
              <button onClick={() => setModal({ type: 'create' })}
                className="inline-flex items-center gap-1.5 bg-white text-violet-700 hover:bg-violet-50 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm">
                <span className="text-base leading-none">+</span> New fleet
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
        {loading && fleets.length === 0 ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : fleets.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-12 text-center text-gray-500">
            {query ? `No fleets match “${query}”.` : 'No fleets yet - create one to enroll an autoscaling group of hosts.'}
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="flex justify-end items-center px-3 py-2 border-b border-gray-100 bg-gray-50/60">
              <ColumnPicker cols={fleetCols} />
            </div>
            <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wider">
                  <th className="w-8" />
                  {fleetCols.visible.map(key => (
                    <ReorderableTh key={key} colKey={key} dragKey={dragKey} onDrop={fleetCols.move}
                      onResize={fleetCols.resize} width={fleetCols.widths[key]} className="px-3 py-2.5">
                      {FLEET_COLS.find(c => c.key === key)?.label}
                    </ReorderableTh>
                  ))}
                  <th className="px-3 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(() => {
                  // The member-table column config is shared across all fleets, so the
                  // Columns picker is rendered just once - in the first expanded fleet.
                  const firstOpenWithMembers = fleets.find(f =>
                    expanded.has(f.fleet_id) && (membersByFleet.get(f.fleet_id)?.agents.length ?? 0) > 0
                  )?.fleet_id;
                  return fleets.map(f => {
                    const page = membersByFleet.get(f.fleet_id);   // undefined until loaded
                    const isOpen = expanded.has(f.fleet_id);
                    const revoked = f.status === 'REVOKED';
                    return (
                      <FleetRow key={f.fleet_id}
                        fleet={f} memberPage={page} membersLoading={loadingMembers.has(f.fleet_id)}
                        isOpen={isOpen} revoked={revoked} defaultReap={defaultReap} defaultMaxFanout={defaultMaxFanout}
                        canManage={isOperator && f.writable !== false}
                        cols={fleetCols.visible} memberCols={memberCols} dragKey={dragKey}
                        showMemberColsPicker={f.fleet_id === firstOpenWithMembers}
                        memberPageSize={MEMBER_PAGE}
                        onToggle={() => toggle(f.fleet_id)}
                        onPageMembers={(o) => loadMembers(f.fleet_id, o)}
                        onAction={setModal} onOpenAgent={onOpenAgent}
                      />
                    );
                  });
                })()}
              </tbody>
            </table>
            </div>
            {total > PAGE && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 bg-gray-50/60 text-sm text-gray-600">
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
          </div>
        )}
      </div>

      {modal?.type === 'create' && (
        <CreateFleetModal apiUrl={apiUrl} tenantToken={tenantToken} defaultReap={defaultReap} defaultMaxFanout={defaultMaxFanout}
          onClose={() => setModal(null)}
          onCreated={r => { setModal({ type: 'token', token: r, name: r.name, rotated: false }); load(); }} />
      )}
      {modal?.type === 'token' && (
        <FleetTokenModal info={modal} onClose={() => setModal(null)} />
      )}
      {modal?.type === 'detail' && (
        <FleetDetailModal fleet={modal.fleet}
          defaultReap={defaultReap} defaultMaxFanout={defaultMaxFanout} canManage={isOperator && modal.fleet.writable !== false}
          onClose={() => setModal(null)} onAction={setModal} />
      )}
      {modal?.type === 'edit' && (
        <EditFleetModal apiUrl={apiUrl} tenantToken={tenantToken} defaultReap={defaultReap} defaultMaxFanout={defaultMaxFanout}
          fleet={modal.fleet} onClose={() => setModal(null)} onSaved={afterAction}
          onNeedsRotate={body => setModal({ type: 'rotate', fleet: (modal as { fleet: Fleet }).fleet, pendingUpdate: body })} />
      )}
      {modal?.type === 'rotate' && (
        <RotateFleetModal apiUrl={apiUrl} tenantToken={tenantToken} fleet={modal.fleet}
          pendingUpdate={modal.pendingUpdate}
          onClose={() => setModal(null)}
          onRotated={(t, name) => { setModal({ type: 'token', token: t, name, rotated: true }); load(); }} />
      )}
      {modal?.type === 'acknowledge-grants' && (
        <ReconcileGrantsModal apiUrl={apiUrl} tenantToken={tenantToken}
          fleet={modal.fleet} driftCount={modal.driftCount} agent={modal.agent}
          onClose={() => setModal(null)} onDone={afterAction} />
      )}
      {modal?.type === 'revoke' && (
        <RevokeFleetModal apiUrl={apiUrl} tenantToken={tenantToken}
          fleet={modal.fleet} memberCount={modal.fleet.member_count ?? 0}
          onClose={() => setModal(null)} onDone={afterAction} />
      )}
      {modal?.type === 'delete' && (
        <DeleteFleetModal apiUrl={apiUrl} tenantToken={tenantToken}
          fleet={modal.fleet} onClose={() => setModal(null)} onDone={afterAction} />
      )}
      {modal?.type === 'run' && (
        <RunCommandModal config={config} target={{ kind: 'fleet', fleet: modal.fleet }}
          onClose={() => setModal(null)} />
      )}
      {modal?.type === 'remove-member' && (
        <RemoveMemberModal apiUrl={apiUrl} tenantToken={tenantToken}
          fleet={modal.fleet} agent={modal.agent} onClose={() => setModal(null)} onDone={afterAction} />
      )}
      {modal?.type === 'revoke-member' && (
        <MemberActionModal kind="revoke" apiUrl={apiUrl} tenantToken={tenantToken}
          agent={modal.agent} onClose={() => setModal(null)} onDone={afterAction} />
      )}
      {modal?.type === 'delete-member' && (
        <MemberActionModal kind="delete" apiUrl={apiUrl} tenantToken={tenantToken}
          agent={modal.agent} onClose={() => setModal(null)} onDone={afterAction} />
      )}
    </div>
  );
}

function FleetRow({ fleet, memberPage, membersLoading, isOpen, revoked, defaultReap, defaultMaxFanout, canManage, cols, memberCols, dragKey, showMemberColsPicker, memberPageSize, onToggle, onPageMembers, onAction, onOpenAgent }: {
  fleet: Fleet; memberPage?: MemberPage; membersLoading: boolean; isOpen: boolean; revoked: boolean;
  defaultReap: number; defaultMaxFanout: number; canManage: boolean; cols: string[]; memberCols: Columns; dragKey: React.MutableRefObject<string | null>;
  showMemberColsPicker?: boolean; memberPageSize: number;
  onToggle: () => void; onPageMembers: (offset: number) => void; onAction: (m: Modals) => void; onOpenAgent?: (agentId: string, fromFleetId?: string) => void;
}) {
  const grants = grantsLabel(fleet.grant_service_mgmt, fleet.grant_docker);
  // Summary counts come from the fleet-list aggregation (cheap, no member load). The
  // accordion's per-member actions use the lazily-loaded member page.
  const memberCount = fleet.member_count ?? 0;
  const active = fleet.active_count ?? 0;
  const inactive = fleet.inactive_count ?? 0;
  const driftCount = fleet.mismatch_count ?? 0;
  const members = memberPage?.agents;
  const memberTotal = memberPage?.total ?? 0;
  const memberOffset = memberPage?.offset ?? 0;
  const loaded = members ?? [];
  // leading expand column + visible columns + trailing actions column
  const expandColSpan = 1 + cols.length + 1;
  const cell = (key: string) => {
    switch (key) {
      case 'name': return (
        <td key={key} className="px-3 py-3 font-medium text-gray-900 whitespace-nowrap">
          <button onClick={e => { e.stopPropagation(); onAction({ type: 'detail', fleet }); }}
            className="text-left hover:text-violet-700 hover:underline" title="View fleet details">
            {fleet.name}
          </button>
        </td>
      );
      case 'fleet_id': return <td key={key} className="px-3 py-3 font-mono text-xs text-gray-400 whitespace-nowrap">{fleet.fleet_id}</td>;
      case 'mode': return <td key={key} className="px-3 py-3"><Badge value={fleet.mode} /></td>;
      case 'members': return (
        <td key={key} className="px-3 py-3 whitespace-nowrap">
          <span className="font-mono text-gray-700">{memberCount}</span>
          {memberCount > 0 && (
            <span className="ml-2 text-[11px] text-gray-500">{active} active{inactive ? `, ${inactive} inactive` : ''}</span>
          )}
          {driftCount > 0 && (
            <span className="ml-2 inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded"
              title={`${driftCount} member(s) enrolled with grants that differ from the fleet`}>
              ⚠ {driftCount} grant mismatch
            </span>
          )}
        </td>
      );
      case 'grants': return <td key={key} className="px-3 py-3 text-xs text-gray-500 whitespace-nowrap">{grants}</td>;
      case 'reap': return <td key={key} className="px-3 py-3 text-xs text-gray-500 font-mono whitespace-nowrap">{formatReap(fleet.reap_after_seconds ?? defaultReap)}</td>;
      case 'max_fanout': return <td key={key} className="px-3 py-3 text-xs text-gray-500 font-mono whitespace-nowrap">{fleet.max_fanout ?? `${defaultMaxFanout} (default)`}</td>;
      case 'status': return <td key={key} className="px-3 py-3 whitespace-nowrap"><Badge value={fleet.status} /></td>;
      case 'created': return <td key={key} className="px-3 py-3 text-xs text-gray-500 whitespace-nowrap">{relTime(fleet.created_at)}</td>;
      default: return null;
    }
  };
  const memberCell = (key: string, a: Agent) => {
    switch (key) {
      case 'host': return (
        <td key={key} className="px-3 py-2 text-gray-800 whitespace-nowrap">
          <span className={onOpenAgent ? 'group-hover:text-violet-700 group-hover:underline' : ''}>
            {a.hostname ?? <span className="text-gray-400 italic">unclaimed</span>}
          </span>
        </td>
      );
      case 'status': return <td key={key} className="px-3 py-2"><Badge value={a.status} /></td>;
      case 'grants': {
        const drift = a.status !== 'REVOKED' && memberMismatchFlagged(a, fleet);
        return (
          <td key={key} className="px-3 py-2 whitespace-nowrap">
            <span className="text-xs text-gray-600">{grantsLabel(a.grant_service_mgmt, a.grant_docker)}</span>
            {drift && (
              <>
                <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded"
                  title={`Fleet wants: ${grantsLabel(fleet.grant_service_mgmt, fleet.grant_docker)}`}>
                  ⚠ mismatch
                </span>
                {canManage && (
                  <button onClick={e => { e.stopPropagation(); onAction({ type: 'acknowledge-grants', fleet, driftCount: 1, agent: a }); }}
                    className="ml-1.5 text-[10px] font-medium text-amber-700 hover:text-amber-900 underline underline-offset-2">
                    reconcile
                  </button>
                )}
              </>
            )}
          </td>
        );
      }
      case 'version': return <td key={key} className="px-3 py-2 font-mono text-xs text-gray-500 whitespace-nowrap">{a.agent_version ?? '-'}</td>;
      case 'last_seen': return <td key={key} className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{a.last_heartbeat_at ? relTime(a.last_heartbeat_at) : '-'}</td>;
      case 'agent_id': return <td key={key} className="px-3 py-2 font-mono text-xs text-gray-400 whitespace-nowrap">{a.agent_id}</td>;
      default: return null;
    }
  };
  return (
    <>
      <tr className={`hover:bg-gray-50/70 cursor-pointer ${revoked ? 'text-gray-400' : ''}`} onClick={onToggle}>
        <td className="pl-3">
          <svg className={`w-4 h-4 text-gray-400 transition-transform ${isOpen ? 'rotate-90' : ''}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        </td>
        {cols.map(cell)}
        <td className="px-3 py-3 text-right" onClick={e => e.stopPropagation()}>
          <FleetMenu fleet={fleet} revoked={revoked} canManage={canManage} onAction={onAction} />
        </td>
      </tr>
      {isOpen && (
        <tr className="bg-gray-50/60">
          <td />
          <td colSpan={expandColSpan} className="px-3 py-3">
            {!revoked && driftCount > 0 && canManage && (
              <div className="mb-3 flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5">
                <span className="text-amber-500 text-lg leading-none mt-0.5">⚠</span>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-amber-800">{driftCount} member{driftCount !== 1 ? 's' : ''} mismatch the fleet grants</p>
                  <p className="text-[11px] text-amber-700 mt-0.5">
                    They enrolled with different host grants (<span className="font-mono">{grantsLabel(fleet.grant_service_mgmt, fleet.grant_docker)}</span> wanted). Re-provision the hosts, then reconcile - reconcile is <strong>verified against detection</strong>, so a host that doesn't yet report the capability is skipped. New instances need the updated launch-template command - <button onClick={e => { e.stopPropagation(); onAction({ type: 'rotate', fleet }); }} className="underline hover:text-amber-900 font-medium">rotate the join token</button>.
                  </p>
                </div>
                <button onClick={e => { e.stopPropagation(); onAction({ type: 'acknowledge-grants', fleet, driftCount }); }}
                  className="shrink-0 text-xs font-semibold text-amber-800 bg-white hover:bg-amber-100 border border-amber-300 px-3 py-1.5 rounded-lg transition-colors"
                  title="Reconcile every mismatched member (or reconcile one from its row below)">
                  Reconcile all ({driftCount})
                </button>
              </div>
            )}
            {members === undefined || membersLoading ? (
              <div className="flex justify-center py-6"><Spinner /></div>
            ) : loaded.length === 0 ? (
              <p className="text-xs text-gray-500 italic py-2">No members yet - install a host with this fleet's join token to enroll it.</p>
            ) : (
              <div className="rounded-lg border border-gray-200 overflow-hidden bg-white">
                {showMemberColsPicker && (
                  <div className="flex justify-end items-center px-3 py-1.5 border-b border-gray-100 bg-gray-50/60">
                    <ColumnPicker cols={memberCols} />
                  </div>
                )}
                <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                      {memberCols.visible.map(key => (
                        <ReorderableTh key={key} colKey={key} dragKey={dragKey} onDrop={memberCols.move}
                          onResize={memberCols.resize} width={memberCols.widths[key]} className="px-3 py-2">
                          {MEMBER_COLS.find(c => c.key === key)?.label}
                        </ReorderableTh>
                      ))}
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {loaded.map(a => (
                      <tr key={a.agent_id}
                        className={onOpenAgent ? 'group cursor-pointer hover:bg-violet-50/50' : ''}
                        onClick={onOpenAgent ? () => onOpenAgent(a.agent_id, fleet.fleet_id) : undefined}>
                        {memberCols.visible.map(key => memberCell(key, a))}
                        <td className="px-3 py-2 text-right" onClick={e => e.stopPropagation()}>
                          {canManage && <MemberMenu fleet={fleet} agent={a} onAction={onAction} />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
                {memberTotal > memberPageSize && (
                  <div className="flex items-center justify-between px-3 py-2 border-t border-gray-100 bg-gray-50/60 text-xs text-gray-600">
                    <span>Showing {memberOffset + 1}–{Math.min(memberOffset + memberPageSize, memberTotal)} of {memberTotal}</span>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={e => { e.stopPropagation(); onPageMembers(Math.max(0, memberOffset - memberPageSize)); }}
                        disabled={memberOffset === 0 || membersLoading}
                        className="px-2.5 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50"
                      >Prev</button>
                      <button
                        onClick={e => { e.stopPropagation(); onPageMembers(memberOffset + memberPageSize); }}
                        disabled={memberOffset + memberPageSize >= memberTotal || membersLoading}
                        className="px-2.5 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50"
                      >Next</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function ColumnPicker({ cols }: { cols: Columns }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const total = cols.cols.length;
  const visibleCount = total - cols.hidden.size;
  return (
    <div className="relative">
      <button
        ref={btnRef}
        onClick={() => {
          if (!open && btnRef.current) {
            const r = btnRef.current.getBoundingClientRect();
            setPos({ top: r.bottom + 6, right: window.innerWidth - r.right });
          }
          setOpen(v => !v);
        }}
        className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-colors ${
          open
            ? 'bg-gray-800 text-white border-gray-800'
            : 'text-gray-500 border-gray-200 bg-white hover:bg-gray-50 hover:text-gray-700 hover:border-gray-300'
        }`}
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 4.5v15m6-15v15M3.75 9h16.5M3.75 15h16.5" />
        </svg>
        Columns
        {cols.hidden.size > 0 && (
          <span className={`text-[10px] font-bold px-1 rounded ${open ? 'bg-white/20' : 'bg-gray-200 text-gray-600'}`}>
            {visibleCount}/{total}
          </span>
        )}
      </button>

      {open && pos && createPortal(
        <>
          <div className="fixed inset-0 z-[9998]" onClick={() => setOpen(false)} />
          <div
            style={{ position: 'fixed', top: pos.top, right: pos.right, zIndex: 9999 }}
            className="bg-white border border-gray-200 rounded-xl shadow-xl py-2 min-w-[180px]"
          >
            <div className="px-3 pb-2 mb-1 border-b border-gray-100 flex items-center justify-between">
              <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Columns</span>
              {cols.hidden.size > 0 && (
                <button onClick={() => cols.showAll()} className="text-[10px] text-indigo-600 hover:text-indigo-800">Show all</button>
              )}
            </div>
            {cols.order.map(key => {
              const c = cols.cols.find(x => x.key === key);
              if (!c) return null;
              return (
                <label key={key}
                  className={`flex items-center gap-2.5 px-3 py-1.5 ${c.required ? 'opacity-50 cursor-not-allowed' : 'hover:bg-gray-50 cursor-pointer'}`}>
                  <input type="checkbox" checked={!cols.hidden.has(key)} disabled={c.required}
                    onChange={() => !c.required && cols.toggle(key)}
                    className="w-3.5 h-3.5 rounded disabled:cursor-not-allowed" />
                  <span className="text-sm text-gray-700">{c.label}</span>
                  {c.required && <span className="ml-auto text-[10px] text-gray-400">required</span>}
                </label>
              );
            })}
          </div>
        </>,
        document.body
      )}
    </div>
  );
}

function FleetMenu({ fleet, revoked, canManage, onAction }: {
  fleet: Fleet; revoked: boolean; canManage: boolean; onAction: (m: Modals) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);

  const toggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < 240 && r.top > spaceBelow;
      setPos(openUp
        ? { bottom: window.innerHeight - r.top + 4, right: window.innerWidth - r.right }
        : { top: r.bottom + 4, right: window.innerWidth - r.right });
    }
    setOpen(v => !v);
  };

  return (
    <div className="flex justify-end">
      <button ref={btnRef} onClick={toggle} className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-700 transition-colors">
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM16 12a2 2 0 100-4 2 2 0 000 4z" /></svg>
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[160px] text-sm max-h-[80vh] overflow-y-auto"
            style={{ top: pos.top, bottom: pos.bottom, right: pos.right }}>
            <FMItem onClick={() => { setOpen(false); onAction({ type: 'detail', fleet }); }}>View details</FMItem>
            {/* Run a fan-out: only with write access to the fleet (canManage) and while active. */}
            {canManage && !revoked && (
              <FMItem onClick={() => { setOpen(false); onAction({ type: 'run', fleet }); }}>Run command</FMItem>
            )}
            {canManage && !revoked && (
              <>
                <FMItem onClick={() => { setOpen(false); onAction({ type: 'rotate', fleet }); }}>Rotate token</FMItem>
                <FMItem onClick={() => { setOpen(false); onAction({ type: 'edit', fleet }); }}>Edit</FMItem>
              </>
            )}
            {canManage && (
              <>
                <div className="border-t border-gray-100 my-1" />
                {!revoked && (
                  <FMItem danger onClick={() => { setOpen(false); onAction({ type: 'revoke', fleet }); }}>Revoke</FMItem>
                )}
                {revoked && (
                  <FMItem danger onClick={() => { setOpen(false); onAction({ type: 'delete', fleet }); }}>Delete</FMItem>
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

function FMItem({ children, onClick, danger, disabled }: { children: React.ReactNode; onClick: () => void; danger?: boolean; disabled?: boolean }) {
  return (
    <button onClick={disabled ? undefined : onClick} disabled={disabled}
      className={`w-full text-left px-3 py-2 transition-colors ${disabled ? 'text-gray-300 cursor-not-allowed' : danger ? 'text-red-600 hover:bg-red-50' : 'text-gray-700 hover:bg-gray-50'}`}>
      {children}
    </button>
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

// A read-only detail view of a fleet, mirroring the agent "View details" modal. Members
// are loaded on open (per-fleet, not from a tenant-wide agent list).
function FleetDetailModal({ fleet, defaultReap, defaultMaxFanout, canManage, onClose, onAction }: {
  fleet: Fleet; defaultReap: number; defaultMaxFanout: number; canManage: boolean;
  onClose: () => void; onAction: (m: Modals) => void; onOpenAgent?: (agentId: string, fromFleetId?: string) => void;
}) {
  // Members aren't listed here - they're browsed via the fleet row's accordion (and can
  // number in the thousands). The detail view uses the cheap fleet-list aggregate counts.
  const revoked = fleet.status === 'REVOKED';
  const active = fleet.active_count ?? 0;
  const inactive = fleet.inactive_count ?? 0;
  const memberCount = fleet.member_count ?? 0;
  const grants = grantsLabel(fleet.grant_service_mgmt, fleet.grant_docker);
  const driftCount = fleet.mismatch_count ?? 0;
  // Close this modal first, then open the next one on the page (avoids stacked modals).
  const open = (m: Modals) => { onClose(); setTimeout(() => onAction(m), 50); };
  return (
    <Modal
      wide
      title={
        <div className="flex items-baseline gap-2.5 flex-wrap">
          <span className="font-bold text-gray-900 text-base">{fleet.name}</span>
          <span className="text-[11px] font-mono text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{fleet.fleet_id}</span>
        </div>
      }
      onClose={onClose}
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2.5">
          <DetailField label="Status"><Badge value={fleet.status} /></DetailField>
          <DetailField label="Type"><Badge value="host" /></DetailField>
          <DetailField label="Mode"><Badge value={fleet.mode} /></DetailField>
          <DetailField label="Grants">
            <span className="text-xs text-gray-700">{grants}</span>
            {driftCount > 0 && (
              <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded"
                title={`${driftCount} member(s) enrolled with different grants`}>⚠ {driftCount} mismatch</span>
            )}
          </DetailField>
          <DetailField label="Members">
            <span className="text-xs text-gray-700"><span className="font-mono">{memberCount}</span>{memberCount > 0 && ` - ${active} active${inactive ? `, ${inactive} inactive` : ''}`}</span>
          </DetailField>
          <DetailField label="Reap after">
            <span className="text-xs text-gray-700 font-mono">{formatReap(fleet.reap_after_seconds ?? defaultReap)}</span>
            {fleet.reap_after_seconds == null && <span className="ml-1 text-[10px] text-gray-400">(default)</span>}
          </DetailField>
          <DetailField label="Max fan-out">
            <span className="text-xs text-gray-700 font-mono">{fleet.max_fanout ?? defaultMaxFanout}</span>
            <span className="ml-1 text-[10px] text-gray-400">member(s)/run{fleet.max_fanout == null ? ' (default)' : ''}</span>
          </DetailField>
          <DetailField label="Created">
            <span className="text-xs text-gray-700">{relTime(fleet.created_at)}</span>
          </DetailField>
        </div>

        {(fleet.tags ?? []).length > 0 && (
          <div className="bg-gray-50 rounded-lg px-3 py-2.5">
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2">Tags <span className="normal-case font-normal text-gray-400">(inherited by every member)</span></p>
            <div className="flex flex-wrap gap-1.5">
              {(fleet.tags ?? []).map(tag => {
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

        <p className="text-[11px] text-gray-400">Members are listed under the fleet's row on the Fleets page (expand it).</p>

        {/* Actions - same state machine as the fleet kebab menu (operators+ only;
            developers get a read-only view of the fleets they're granted). */}
        {canManage && (
          <div className="flex flex-wrap gap-2 pt-3 border-t border-gray-100">
            {!revoked && (
              <>
                <button onClick={() => open({ type: 'rotate', fleet })}
                  className="text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 px-3 py-1.5 rounded-lg transition-colors">
                  Rotate token
                </button>
                <button onClick={() => open({ type: 'edit', fleet })}
                  className="text-xs font-semibold text-gray-700 bg-gray-100 hover:bg-gray-200 px-3 py-1.5 rounded-lg transition-colors">
                  Edit
                </button>
                {driftCount > 0 && (
                  <button onClick={() => open({ type: 'acknowledge-grants', fleet, driftCount })}
                    className="text-xs font-semibold text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-300 px-3 py-1.5 rounded-lg transition-colors">
                    Reconcile mismatch ({driftCount})
                  </button>
                )}
              </>
            )}
            <div className="flex-1" />
            {!revoked ? (
              <button onClick={() => open({ type: 'revoke', fleet })}
                className="text-xs font-semibold text-red-600 bg-red-50 hover:bg-red-100 border border-red-200 px-3 py-1.5 rounded-lg transition-colors">
                Revoke fleet
              </button>
            ) : (
              <button onClick={() => open({ type: 'delete', fleet })}
                className="text-xs font-semibold text-red-600 bg-red-50 hover:bg-red-100 border border-red-200 px-3 py-1.5 rounded-lg transition-colors">
                Delete fleet
              </button>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
// Per-member "..." menu inside the accordion: detach, or teardown via the agent
// state machine (revoke -> delete), matching the Agents page.
function MemberMenu({ fleet, agent, onAction }: { fleet: Fleet; agent: Agent; onAction: (m: Modals) => void }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const status = agent.status;
  const toggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < 180 && r.top > spaceBelow;
      setPos(openUp
        ? { bottom: window.innerHeight - r.top + 4, right: window.innerWidth - r.right }
        : { top: r.bottom + 4, right: window.innerWidth - r.right });
    }
    setOpen(v => !v);
  };
  return (
    <div className="flex justify-end">
      <button ref={btnRef} onClick={toggle} className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-700 transition-colors">
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM16 12a2 2 0 100-4 2 2 0 000 4z" /></svg>
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[160px] text-sm max-h-[80vh] overflow-y-auto"
            style={{ top: pos.top, bottom: pos.bottom, right: pos.right }}>
            <FMItem onClick={() => { setOpen(false); onAction({ type: 'remove-member', fleet, agent }); }}>Remove from fleet</FMItem>
            <div className="border-t border-gray-100 my-1" />
            {(status === 'ACTIVE' || status === 'INACTIVE') && (
              <FMItem danger onClick={() => { setOpen(false); onAction({ type: 'revoke-member', fleet, agent }); }}>Revoke</FMItem>
            )}
            {status === 'REVOKED' && (
              <FMItem danger onClick={() => { setOpen(false); onAction({ type: 'delete-member', fleet, agent }); }}>Delete</FMItem>
            )}
          </div>
        </>,
        document.body,
      )}
    </div>
  );
}

function CreateFleetModal({ apiUrl, tenantToken, defaultReap, defaultMaxFanout, onClose, onCreated }: {
  apiUrl: string; tenantToken: string; defaultReap: number; defaultMaxFanout: number;
  onClose: () => void; onCreated: (r: Fleet & FleetToken) => void;
}) {
  const [name, setName] = useState('');
  const [mode, setMode] = useState<typeof MODES[number]>('readonly');
  const [grantSvc, setGrantSvc] = useState(false);
  const [grantDocker, setGrantDocker] = useState(false);
  const [tagPairs, setTagPairs] = useState<KVPair[]>([]);
  const [reapMin, setReapMin] = useState('');
  const [maxFanout, setMaxFanout] = useState('');
  const [wavePolicy, setWavePolicy] = useState<FleetWavePolicy>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError('Name is required.'); return; }
    setLoading(true); setError('');
    try {
      const r = await createFleet(apiUrl, tenantToken, {
        name: name.trim(), mode, grant_service_mgmt: grantSvc, grant_docker: grantDocker,
        tags: serializePairs(tagPairs),
        reap_after_seconds: reapMin.trim() ? Math.round(Number(reapMin) * 60) : null,
        max_fanout: maxFanout.trim() ? Number(maxFanout) : null,
        wave_policy: Object.keys(wavePolicy).length ? wavePolicy : null,
      });
      onCreated(r);
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title="New fleet" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
          <input value={name} onChange={e => setName(e.target.value)} autoFocus placeholder="e.g. web-asg, worker-pool"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Execution mode <span className="font-normal text-gray-400">(inherited by every member)</span></label>
          <div className="grid grid-cols-3 gap-2">
            {MODES.map(m => (
              <button key={m} type="button" onClick={() => setMode(m)}
                className={`px-3 py-2 rounded-lg border-2 text-sm font-semibold capitalize transition-all ${mode === m ? 'border-violet-400 bg-violet-50 text-violet-800' : 'border-gray-200 text-gray-600 hover:border-gray-300'}`}>{m}</button>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Host grants</label>
          <div className="space-y-1.5">
            <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={grantSvc} onChange={e => setGrantSvc(e.target.checked)} className="w-4 h-4" /> Service management (systemctl)</label>
            <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={grantDocker} onChange={e => setGrantDocker(e.target.checked)} className="w-4 h-4" /> Docker access</label>
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Tags <span className="font-normal text-gray-400">(inherited by every member)</span></label>
          <TagsEditor pairs={tagPairs} setPairs={setTagPairs} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Reap members after <span className="font-normal text-gray-400">(minutes of no heartbeat; blank = default {reapPhrase(defaultReap)})</span></label>
          <input value={reapMin} onChange={e => setReapMin(e.target.value.replace(/[^0-9.]/g, ''))} placeholder={String(Math.round(defaultReap / 60))}
            className="w-40 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500" />
          <p className="mt-1 text-xs text-gray-400">Terminated ASG instances go inactive after ~45s; reaping then removes their records. Default: {reapPhrase(defaultReap)}.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Max fan-out <span className="font-normal text-gray-400">(per-wave cap; blank = tenant default of {defaultMaxFanout})</span></label>
          <input value={maxFanout} onChange={e => setMaxFanout(e.target.value.replace(/[^0-9]/g, ''))} placeholder={String(defaultMaxFanout)}
            className="w-40 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500" />
          <p className="mt-1 text-xs text-gray-400">The most members a fan-out runs at once (the wave size); with more members it proceeds in waves. Can't exceed the tenant's fan-out cap ({defaultMaxFanout}).</p>
        </div>
        <details className="border border-gray-100 rounded-lg px-3 py-2">
          <summary className="text-sm font-medium text-gray-600 cursor-pointer">Advanced: staged-rollout override</summary>
          <p className="mt-2 mb-2 text-xs text-gray-400">Override the tenant's fleet-run wave policy for this fleet, per read/write. Waves run in batches of the fan-out cap. Leave both Off to inherit the tenant default.</p>
          <WavePolicyRW value={wavePolicy} onChange={setWavePolicy} />
        </details>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button type="submit" disabled={loading} className="flex items-center gap-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Create fleet
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FleetTokenModal({ info, onClose }: { info: { token: FleetToken; name: string; rotated: boolean }; onClose: () => void }) {
  const { token, name, rotated } = info;
  return (
    <Modal wide title={rotated ? `Join token rotated - ${name}` : `Fleet created - ${name}`} onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
          <p className="text-sm text-amber-800 font-medium">Save the join token now</p>
          <p className="text-xs text-amber-700 mt-0.5">Shown only once. Bake the command below into your ASG launch template's user-data - every instance that scales in enrolls into this fleet.</p>
          {rotated && token.previous_token_valid_until && (
            <p className="text-xs text-amber-700 mt-1">The previous token keeps working until <strong>{relTime(token.previous_token_valid_until)}</strong> - update your launch template before then.</p>
          )}
        </div>
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Launch template user-data</p>
          <div className="relative bg-gray-900 rounded-lg p-3 pr-10">
            <code className="text-xs text-green-400 break-all whitespace-pre-wrap">{token.install}</code>
            <CopyButton text={token.install} className="absolute top-2 right-2" />
          </div>
        </div>
        <TokenBox label="Join token" value={token.join_token} />
        <div className="flex justify-end pt-1">
          <button onClick={onClose} className="bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium px-4 py-2 rounded-md">Done</button>
        </div>
      </div>
    </Modal>
  );
}

function EditFleetModal({ apiUrl, tenantToken, defaultReap, defaultMaxFanout, fleet, onClose, onSaved, onNeedsRotate }: {
  apiUrl: string; tenantToken: string; defaultReap: number; defaultMaxFanout: number; fleet: Fleet;
  onClose: () => void; onSaved: () => void;
  // Called instead of saving when grants changed: the edit is handed off to the
  // rotate-token step and only committed there (so the operator can't change grants
  // without getting the new install command).
  onNeedsRotate: (body: FleetUpdateBody) => void;
}) {
  const [mode, setMode] = useState<typeof MODES[number]>(fleet.mode);
  const [tagPairs, setTagPairs] = useState<KVPair[]>(parseTags(fleet.tags));
  const [reapMin, setReapMin] = useState(fleet.reap_after_seconds ? String(fleet.reap_after_seconds / 60) : '');
  const [maxFanout, setMaxFanout] = useState(fleet.max_fanout ? String(fleet.max_fanout) : '');
  const [wavePolicy, setWavePolicy] = useState<FleetWavePolicy>(fleet.wave_policy || {});
  const [grantSvc, setGrantSvc] = useState(!!fleet.grant_service_mgmt);
  const [grantDocker, setGrantDocker] = useState(!!fleet.grant_docker);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const grantsChanged = grantSvc !== !!fleet.grant_service_mgmt || grantDocker !== !!fleet.grant_docker;

  const body = (): FleetUpdateBody => ({
    mode, tags: serializePairs(tagPairs),
    grant_service_mgmt: grantSvc, grant_docker: grantDocker,
    reap_after_seconds: reapMin.trim() ? Math.round(Number(reapMin) * 60) : null,
    max_fanout: maxFanout.trim() ? Number(maxFanout) : null,
    wave_policy: Object.keys(wavePolicy).length ? wavePolicy : null,
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    // A grant change isn't saved here - it's committed at the rotate-token step, so
    // the operator always leaves with the new launch-template install command.
    if (grantsChanged) { onNeedsRotate(body()); return; }
    setLoading(true); setError('');
    try {
      await updateFleet(apiUrl, tenantToken, fleet.fleet_id, body());
      onSaved();
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={`Edit fleet - ${fleet.name}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Execution mode</label>
          <div className="grid grid-cols-3 gap-2">
            {MODES.map(m => (
              <button key={m} type="button" onClick={() => setMode(m)}
                className={`px-3 py-2 rounded-lg border-2 text-sm font-semibold capitalize transition-all ${mode === m ? 'border-violet-400 bg-violet-50 text-violet-800' : 'border-gray-200 text-gray-600 hover:border-gray-300'}`}>{m}</button>
            ))}
          </div>
          <p className="mt-1 text-xs text-amber-600">Changing this updates the mode of <strong>all current members</strong> too, and new members inherit it.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Tags <span className="font-normal text-gray-400">(inherited - applied to all members on save)</span></label>
          <TagsEditor pairs={tagPairs} setPairs={setTagPairs} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Host grants</label>
          <div className="space-y-1.5">
            <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={grantSvc} onChange={e => setGrantSvc(e.target.checked)} className="w-4 h-4" /> Service management (systemctl)</label>
            <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={grantDocker} onChange={e => setGrantDocker(e.target.checked)} className="w-4 h-4" /> Docker access</label>
          </div>
          {grantsChanged && (
            <p className="mt-1.5 text-xs text-amber-600">
              Grants are baked into the host at install, so this can't be flipped on running members remotely. Saving takes you to <strong>rotate the join token</strong> - the grant change is committed there, together with the new launch-template command. Existing members will show a <strong>grant mismatch</strong> until you re-provision them and <strong>reconcile</strong>.
            </p>
          )}
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Reap members after <span className="font-normal text-gray-400">(minutes; blank = default {reapPhrase(defaultReap)})</span></label>
          <input value={reapMin} onChange={e => setReapMin(e.target.value.replace(/[^0-9.]/g, ''))}
            className="w-40 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Max fan-out <span className="font-normal text-gray-400">(per-wave cap; blank = tenant default of {defaultMaxFanout})</span></label>
          <input value={maxFanout} onChange={e => setMaxFanout(e.target.value.replace(/[^0-9]/g, ''))} placeholder={String(defaultMaxFanout)}
            className="w-40 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500" />
          <p className="mt-1 text-xs text-gray-400">The wave size for a fan-out; can't exceed the tenant's fan-out cap ({defaultMaxFanout}).</p>
        </div>
        <details className="border border-gray-100 rounded-lg px-3 py-2" open={!!fleet.wave_policy}>
          <summary className="text-sm font-medium text-gray-600 cursor-pointer">Advanced: staged-rollout override</summary>
          <p className="mt-2 mb-2 text-xs text-gray-400">Override the tenant's fleet-run wave policy for this fleet, per read/write. Waves run in batches of the fan-out cap. Leave both Off to inherit the tenant default.</p>
          <WavePolicyRW value={wavePolicy} onChange={setWavePolicy} />
        </details>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button type="submit" disabled={loading} className="flex items-center gap-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} {grantsChanged ? 'Next: rotate token →' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

const GRACE_OPTIONS: { label: string; secs: number }[] = [
  { label: 'Immediately (invalidate old token now)', secs: 0 },
  { label: '1 hour', secs: 3600 },
  { label: '6 hours', secs: 6 * 3600 },
  { label: '24 hours', secs: 24 * 3600 },
  { label: '72 hours', secs: 72 * 3600 },
  { label: '7 days', secs: 7 * 24 * 3600 },
];

function RotateFleetModal({ apiUrl, tenantToken, fleet, pendingUpdate, onClose, onRotated }: {
  apiUrl: string; tenantToken: string; fleet: Fleet; pendingUpdate?: FleetUpdateBody;
  onClose: () => void; onRotated: (token: FleetToken, name: string) => void;
}) {
  const [grace, setGrace] = useState(24 * 3600);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const savingGrants = !!pendingUpdate;

  const confirm = async () => {
    setLoading(true); setError('');
    try {
      // When arriving from a grant edit, commit the fleet update first so the new
      // token's install command bakes in the new grants; then rotate. If either
      // fails, nothing partial is shown to the operator - they see the error and retry.
      if (pendingUpdate) await updateFleet(apiUrl, tenantToken, fleet.fleet_id, pendingUpdate);
      const t = await rotateFleetToken(apiUrl, tenantToken, fleet.fleet_id, grace);
      onRotated(t, fleet.name);
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={savingGrants ? `Save grants & rotate token - ${fleet.name}` : `Rotate join token - ${fleet.name}`} onClose={onClose}>
      <div className="space-y-4">
        {savingGrants && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5 text-xs text-amber-800">
            Your <strong>grant change</strong> is applied when you rotate - the new token's install command carries the updated grants. Cancel and it is <strong>not saved</strong>. Existing members keep their old grants (grant mismatch) until re-provisioned and reconciled.
          </div>
        )}
        <p className="text-sm text-gray-700">A new join token is issued. Choose how long the <strong>current</strong> token stays valid so you can update your ASG launch template before it stops working.</p>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Keep old token valid for</label>
          <select value={grace} onChange={e => setGrace(Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-500">
            {GRACE_OPTIONS.map(o => <option key={o.secs} value={o.secs}>{o.label}</option>)}
          </select>
          {grace === 0 && <p className="mt-1 text-xs text-amber-600">Instances still launching with the old token will fail to enroll until the launch template is updated.</p>}
        </div>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button onClick={confirm} disabled={loading} className="flex items-center gap-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} {savingGrants ? 'Save & rotate token' : 'Rotate token'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function ReconcileGrantsModal({ apiUrl, tenantToken, fleet, driftCount, agent, onClose, onDone }: {
  apiUrl: string; tenantToken: string; fleet: Fleet; driftCount: number; agent?: Agent;
  onClose: () => void; onDone: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Hosts the backend declined to reconcile because they don't yet report the granted
  // capability (verification against detection - see handle_acknowledge_fleet_grants).
  const [blocked, setBlocked] = useState<{ agent_id: string; hostname?: string; reason: string }[]>([]);
  const one = !!agent;   // single-member vs whole-fleet reconcile
  const who = agent ? (agent.hostname ?? agent.agent_id) : `${driftCount} member${driftCount !== 1 ? 's' : ''}`;
  const confirm = async () => {
    setLoading(true); setError('');
    try {
      const r = await reconcileFleetGrants(apiUrl, tenantToken, fleet.fleet_id, agent?.agent_id);
      // If nothing could be reconciled (all blocked), keep the modal open and show why -
      // the operator can then choose "Accept as-is" instead.
      if (r.reconciled === 0 && (r.blocked?.length ?? 0) > 0) { setBlocked(r.blocked); setLoading(false); return; }
      onDone();
    }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };
  const accept = async () => {
    setLoading(true); setError('');
    try { await acceptFleetGrantMismatch(apiUrl, tenantToken, fleet.fleet_id, agent?.agent_id); onDone(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };
  return (
    <Modal title={`Resolve grant mismatch - ${fleet.name}`} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-700">
          <strong>{who}</strong> enrolled with host grants that differ from the fleet's current grants (<span className="font-mono">{grantsLabel(fleet.grant_service_mgmt, fleet.grant_docker)}</span>). Resolve by <strong>reconciling</strong> (you fixed the host) or <strong>accepting</strong> the divergence.
        </p>
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5 text-xs text-amber-800 space-y-1">
          <p><strong>Reconcile</strong> - you re-provisioned the host to match the fleet. <strong>Verified against detection:</strong> {one ? 'the member is' : 'a member is'} only reconciled once the host actually reports the granted capability; hosts that don't are <strong>skipped</strong>, so this can't clear a mismatch on a host that wasn't fixed.</p>
          <p><strong>Accept as-is</strong> - you're OK with {one ? 'this member' : 'these members'} running with different grants. Their real grants are kept (nothing is falsified); they just stop being flagged. The acceptance is scoped to this exact divergence: it <strong>re-flags automatically</strong> if the fleet grants change or the member's own grants change to a new mismatch, and it's <strong>dropped once the member matches the fleet</strong> - so a later return to the same divergence must be accepted again.</p>
        </div>
        {blocked.length > 0 && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2.5 text-xs text-red-700 space-y-1">
            <p className="font-semibold">Not reconciled - the host doesn't report the granted capability yet. Re-provision it, or <strong>Accept as-is</strong> if the divergence is intentional:</p>
            <ul className="list-disc list-inside space-y-0.5">
              {blocked.map(b => <li key={b.agent_id}><span className="font-mono">{b.hostname ?? b.agent_id}</span> - {b.reason}</li>)}
            </ul>
          </div>
        )}
        <p className="text-xs text-gray-500">
          New instances need the updated install command - use <strong>Rotate token</strong> to regenerate the launch-template line with the current grants baked in.
        </p>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        <div className="flex items-center justify-between gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <div className="flex gap-2">
            <button onClick={accept} disabled={loading} className="text-sm font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-300 px-4 py-2 rounded-md disabled:opacity-60">
              Accept as-is
            </button>
            <button onClick={confirm} disabled={loading} className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
              {loading && <Spinner className="h-4 w-4" />} Reconcile {who}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function RevokeFleetModal({ apiUrl, tenantToken, fleet, memberCount, onClose, onDone }: {
  apiUrl: string; tenantToken: string; fleet: Fleet; memberCount: number; onClose: () => void; onDone: () => void;
}) {
  const [members, setMembers] = useState<'keep' | 'remove'>('keep');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const confirm = async () => {
    setLoading(true); setError('');
    try { await revokeFleet(apiUrl, tenantToken, fleet.fleet_id, members); onDone(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  return (
    <Modal title={`Revoke join token - ${fleet.name}`} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-700">New instances can no longer enroll. This fleet has <strong>{memberCount}</strong> member{memberCount !== 1 ? 's' : ''} - what should happen to them?</p>
        <div className="space-y-2">
          <label className={`flex items-start gap-3 p-3 rounded-lg border-2 cursor-pointer ${members === 'keep' ? 'border-violet-300 bg-violet-50' : 'border-gray-200 hover:border-gray-300'}`}>
            <input type="radio" name="members" checked={members === 'keep'} onChange={() => setMembers('keep')} className="mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-gray-800">Keep as individual agents</p>
              <p className="text-xs text-gray-500">Members are detached from the fleet and keep running as standalone agents.</p>
            </div>
          </label>
          <label className={`flex items-start gap-3 p-3 rounded-lg border-2 cursor-pointer ${members === 'remove' ? 'border-red-300 bg-red-50' : 'border-gray-200 hover:border-gray-300'}`}>
            <input type="radio" name="members" checked={members === 'remove'} onChange={() => setMembers('remove')} className="mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-gray-800">Remove them</p>
              <p className="text-xs text-gray-500">Delete every member agent record. The running agents will be disconnected.</p>
            </div>
          </label>
        </div>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button onClick={confirm} disabled={loading}
            className={`flex items-center gap-2 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60 ${members === 'remove' ? 'bg-red-600 hover:bg-red-700' : 'bg-amber-600 hover:bg-amber-700'}`}>
            {loading && <Spinner className="h-4 w-4" />} Revoke ({members === 'keep' ? 'keep members' : 'remove members'})
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DeleteFleetModal({ apiUrl, tenantToken, fleet, onClose, onDone }: {
  apiUrl: string; tenantToken: string; fleet: Fleet; onClose: () => void; onDone: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const confirm = async () => {
    setLoading(true); setError('');
    try { await deleteFleet(apiUrl, tenantToken, fleet.fleet_id); onDone(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };
  return (
    <Modal title="Delete fleet" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-700">Permanently delete fleet <strong>{fleet.name}</strong>.</p>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button onClick={confirm} disabled={loading} className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Delete
          </button>
        </div>
      </div>
    </Modal>
  );
}

function RemoveMemberModal({ apiUrl, tenantToken, fleet, agent, onClose, onDone }: {
  apiUrl: string; tenantToken: string; fleet: Fleet; agent: Agent; onClose: () => void; onDone: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const confirm = async () => {
    setLoading(true); setError('');
    try { await removeFleetMember(apiUrl, tenantToken, fleet.fleet_id, agent.agent_id); onDone(); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };
  return (
    <Modal title="Remove from fleet" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-700">
          Remove <strong>{agent.hostname ?? agent.agent_id}</strong> from fleet <strong>{fleet.name}</strong>. It becomes a <strong>standalone individual agent</strong> - it keeps running and regains individual controls (mode, tags, install token). To fully delete it instead, use <strong>Revoke</strong> then <strong>Delete</strong> from its menu.
        </p>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button onClick={confirm} disabled={loading} className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} Remove from fleet
          </button>
        </div>
      </div>
    </Modal>
  );
}

function MemberActionModal({ kind, apiUrl, tenantToken, agent, onClose, onDone }: {
  kind: 'revoke' | 'delete'; apiUrl: string; tenantToken: string; agent: Agent; onClose: () => void; onDone: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const isDelete = kind === 'delete';
  const confirm = async () => {
    setLoading(true); setError('');
    try {
      if (isDelete) await deleteTenantAgent(apiUrl, tenantToken, agent.agent_id);
      else await revokeTenantAgent(apiUrl, tenantToken, agent.agent_id);
      onDone();
    } catch (e) { setError((e as Error).message); setLoading(false); }
  };
  const who = agent.hostname ?? agent.agent_id;
  return (
    <Modal title={isDelete ? 'Delete agent' : 'Revoke agent'} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-700">
          {isDelete
            ? <>Delete <strong>{who}</strong>? The record is soft-deleted and drops out of the fleet's active members.</>
            : <>Revoke <strong>{who}</strong>? It's immediately disconnected and its process stops. You can delete it afterwards.</>}
        </p>
        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="text-sm text-gray-600">Cancel</button>
          <button onClick={confirm} disabled={loading} className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded-md disabled:opacity-60">
            {loading && <Spinner className="h-4 w-4" />} {isDelete ? 'Delete' : 'Revoke'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
