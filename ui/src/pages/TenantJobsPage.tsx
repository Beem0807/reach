import { useState, useEffect, useCallback } from 'react';
import type { TenantConfig, Job, Agent, Fleet, FleetRun, RunStatus } from '../types';
import { listTenantJobs, listTenantAgents, listFleets, listFleetRuns, listTagRuns, getRun, pauseRun, resumeRun, cancelRun } from '../api';
import { Badge } from '../components/Badge';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { AgentInfo } from '../components/AgentInfo';
import { Modal } from '../components/Modal';
import { RunCommandModal } from '../components/RunCommandModal';
import type { RunTarget } from '../components/RunCommandModal';
import { CopyButton } from '../components/CopyButton';
import { RefreshButton } from '../components/RefreshButton';

function fmtDate(iso?: string) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function duration(started?: string, completed?: string) {
  if (!started || !completed) return '-';
  const ms = new Date(completed).getTime() - new Date(started).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

type Scope = 'jobs' | 'fleet-runs' | 'tag-runs';

// Sentinel fleet-filter value for "jobs on standalone agents only" (no fleet).
const STANDALONE = '__standalone__';

export function TenantJobsPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;
  const [scope, setScope] = useState<Scope>('jobs');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [runs, setRuns] = useState<FleetRun[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [fleets, setFleets] = useState<Fleet[]>([]);
  const [launcher, setLauncher] = useState(false);   // "Create job" / "New run" modal open
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Job | null>(null);
  const [openRun, setOpenRun] = useState<FleetRun | null>(null);
  // When a member is opened from a run, remember the run so the job detail can go back
  // to the wave view. Null when a job was opened directly from the Jobs list.
  const [jobBackRun, setJobBackRun] = useState<FleetRun | null>(null);
  // Filters: an agent or a fleet for jobs; runs are always fleet-scoped.
  const [agentFilter, setAgentFilter] = useState('');
  const [fleetFilter, setFleetFilter] = useState('');
  // Command search (jobs scope) - explicit, applied on the Search button / Enter.
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const applySearch = () => setQuery(search.trim());
  // Cursor-based paging (jobs are ordered by created_at, no cheap total). `pageCursors`
  // is the stack of the cursor that opened each page; `pageIdx` is the current page;
  // `nextCursor` is the server's cursor for the following page (absent → last page).
  const [pageCursors, setPageCursors] = useState<(string | undefined)[]>([undefined]);
  const [pageIdx, setPageIdx] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | undefined>(undefined);

  const loadDropdowns = useCallback(() => {
    listTenantAgents(apiUrl, tenantToken).then(r => setAgents(r.agents ?? [])).catch(() => {});
    listFleets(apiUrl, tenantToken).then(r => setFleets(r.fleets ?? [])).catch(() => {});
  }, [apiUrl, tenantToken]);
  useEffect(() => { loadDropdowns(); }, [loadDropdowns]);

  // load(cursor) fetches a specific page. It does NOT depend on the cursor, so changing
  // a filter/scope/search re-creates it and resets to page 1 (via the effect below),
  // while the pager calls it imperatively with the next/prev cursor.
  const load = useCallback((cursor?: string) => {
    setLoading(true);
    setError('');
    if (scope === 'fleet-runs') {
      if (!fleetFilter) { setRuns([]); setNextCursor(undefined); setLoading(false); return; }
      listFleetRuns(apiUrl, tenantToken, fleetFilter, cursor ? { cursor } : {})
        .then(r => { setRuns(r.runs ?? []); setNextCursor(r.next_cursor); })
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
      return;
    }
    if (scope === 'tag-runs') {
      listTagRuns(apiUrl, tenantToken, cursor ? { cursor } : {})
        .then(r => { setRuns(r.runs ?? []); setNextCursor(r.next_cursor); })
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
      return;
    }
    const params: Record<string, string> = {};
    if (fleetFilter && fleetFilter !== STANDALONE) params.fleet_id = fleetFilter;
    else if (agentFilter) params.agent_id = agentFilter;
    if (query) params.q = query;
    if (cursor) params.cursor = cursor;
    listTenantJobs(apiUrl, tenantToken, params)
      .then(r => {
        // "Standalone only": the endpoint has no such filter, so drop fleet-member jobs here.
        const js = r.jobs ?? [];
        setJobs(fleetFilter === STANDALONE ? js.filter(j => !j.agent_fleet_id) : js);
        setNextCursor(r.next_cursor);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken, scope, agentFilter, fleetFilter, query]);

  // Any filter/scope/search change re-creates `load` → reset to page 1 and fetch it.
  useEffect(() => { setPageCursors([undefined]); setPageIdx(0); load(undefined); }, [load]);

  const goNext = () => {
    if (!nextCursor) return;
    setPageCursors(pc => [...pc.slice(0, pageIdx + 1), nextCursor]);
    setPageIdx(i => i + 1);
    load(nextCursor);
  };
  const goPrev = () => {
    if (pageIdx === 0) return;
    const c = pageCursors[pageIdx - 1];
    setPageIdx(i => i - 1);
    load(c);
  };

  const refresh = () => { load(pageCursors[pageIdx]); loadDropdowns(); };
  const fleetName = (id?: string | null) => fleets.find(f => f.fleet_id === id)?.name ?? id ?? '-';

  // Cursor pager, shared by the Jobs and the fleet/tag Runs tabs.
  const pager = (pageIdx > 0 || nextCursor) ? (
    <div className="flex items-center justify-between text-sm text-gray-600 mt-3">
      <span>Page {pageIdx + 1}</span>
      <div className="flex items-center gap-2">
        <button onClick={goPrev} disabled={pageIdx === 0 || loading}
          className="px-3 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50">Prev</button>
        <button onClick={goNext} disabled={!nextCursor || loading}
          className="px-3 py-1 rounded-md border border-gray-300 bg-white disabled:opacity-40 hover:bg-gray-50">Next</button>
      </div>
    </div>
  ) : null;

  const runningCount   = jobs.filter(j => j.status === 'RUNNING').length;
  const completedCount = jobs.filter(j => j.status === 'SUCCEEDED').length;
  const failedCount    = jobs.filter(j => j.status === 'FAILED').length;

  // Top-level "Create job" / "New run" launcher, scoped to the active tab and gated on
  // write access (the modal only lists writable, active targets).
  // Tags for tag fan-outs: derived from the (fully-loaded) standalone agents, since tag
  // runs target non-fleet agents. The agents list is unpaginated here, so this is complete.
  const allTags = [...new Set(agents.filter(a => !a.fleet_id).flatMap(a => a.tags ?? []))].sort();
  // The launcher button is always shown. The picker lists every writable agent - inactive
  // ones appear disabled (for reference) rather than being hidden - and the Run/Preview
  // action stays disabled until a runnable target is chosen.
  const writableAgents = agents.filter(a => a.writable);
  const eligibleFleets = fleets.filter(f => f.writable !== false && f.status === 'ACTIVE');
  const launcherTarget: RunTarget =
    scope === 'fleet-runs' ? { kind: 'fleet-pick', fleets: eligibleFleets }
    : scope === 'tag-runs' ? { kind: 'tag', tags: allTags }
    : { kind: 'agent-pick', agents: writableAgents };
  const launcherLabel = scope === 'jobs' ? 'Create job' : 'New run';

  return (
    <div className="min-h-full bg-slate-50">
      <div className="bg-gradient-to-r from-emerald-700 to-emerald-600 px-8 py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Jobs</h1>
              <p className="text-sm text-emerald-200">Command execution history for your tenant</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {scope === 'jobs' && !loading && jobs.length > 0 && (
              <>
                {runningCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-blue-500/20 border border-blue-400/30 text-blue-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse shrink-0" />{runningCount} running
                  </span>
                )}
                {completedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-emerald-500/20 border border-emerald-400/30 text-emerald-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-300 shrink-0" />{completedCount} completed
                  </span>
                )}
                {failedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-red-500/20 border border-red-400/30 text-red-200 text-xs font-semibold px-3 py-1.5 rounded-lg">{failedCount} failed</span>
                )}
              </>
            )}
            <RefreshButton onClick={refresh} loading={loading} />
            <button
              onClick={() => setLauncher(true)}
              className="inline-flex items-center gap-1.5 bg-white text-slate-800 hover:bg-slate-100 text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
            >
              <span className="text-base leading-none">+</span> {launcherLabel}
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-4">
        {/* Scope toggle: individual jobs vs fan-out runs (runs are fleet-only). */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="inline-flex rounded-lg border border-gray-300 bg-white shadow-sm overflow-hidden">
            {([['jobs', 'Jobs'], ['fleet-runs', 'Fleet runs'], ['tag-runs', 'Tag runs']] as const).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setScope(k)}
                className={`px-4 py-1.5 text-sm font-medium transition-colors ${
                  scope === k ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Fleet filter (jobs + fleet-runs; tag-runs are tenant-wide standalone) */}
          {scope !== 'tag-runs' && (
            <select
              value={fleetFilter}
              onChange={e => { setFleetFilter(e.target.value); if (e.target.value) setAgentFilter(''); }}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">{scope === 'fleet-runs' ? 'Select a fleet…' : 'All jobs'}</option>
              {scope === 'jobs' && <option value={STANDALONE}>Standalone (no fleet)</option>}
              {fleets.map(f => <option key={f.fleet_id} value={f.fleet_id}>{f.name ?? f.fleet_id}</option>)}
            </select>
          )}

          {/* Agent filter (jobs scope only, when no fleet/standalone selected) */}
          {scope === 'jobs' && !fleetFilter && (
            <select
              value={agentFilter}
              onChange={e => setAgentFilter(e.target.value)}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">All agents</option>
              {agents.filter(a => !a.fleet_id).map(a => (
                <option key={a.agent_id} value={a.agent_id}>{a.hostname ?? a.agent_id}</option>
              ))}
            </select>
          )}
          {(agentFilter || fleetFilter) && (
            <button onClick={() => { setAgentFilter(''); setFleetFilter(''); }} className="text-sm text-indigo-600 hover:text-indigo-800">Clear</button>
          )}

          {/* Command search (jobs scope only). Explicit: fires only on the button / Enter. */}
          {scope === 'jobs' && (
            <div className="flex items-center gap-2 ml-auto">
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') applySearch(); }}
                placeholder="Search command…"
                className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <button onClick={applySearch} className="text-sm text-white bg-slate-800 hover:bg-slate-700 rounded-lg px-3 py-1.5">Search</button>
              {query && (
                <button
                  onClick={() => { setSearch(''); setQuery(''); }}
                  className="text-sm text-indigo-600 hover:text-indigo-800"
                  aria-label="Clear search"
                >✕</button>
              )}
            </div>
          )}
        </div>
        {query && (
          <div className="text-xs text-gray-500">Filtered by command “{query}”</div>
        )}

        {error && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>
        )}

        {selected && (
          <JobDetailModal
            job={selected} fleetName={fleetName(selected.agent_fleet_id)}
            onClose={() => { setSelected(null); setJobBackRun(null); }}
            onBack={jobBackRun ? () => { setOpenRun(jobBackRun); setJobBackRun(null); setSelected(null); } : undefined}
          />
        )}
        {openRun && (
          <RunDetailModal
            run={openRun} config={config}
            scopeLabel={scope === 'tag-runs'
              ? (openRun.tag ? `tag: ${openRun.tag}` : 'standalone (tag fan-out)')
              : `fleet: ${fleetName(fleetFilter)}`}
            onClose={() => setOpenRun(null)}
            onOpenJob={j => { setJobBackRun(openRun); setOpenRun(null); setSelected(j); }}
          />
        )}

        {launcher && (
          <RunCommandModal
            config={config}
            target={launcherTarget}
            onClose={() => { setLauncher(false); refresh(); }}
          />
        )}

        {loading && jobs.length === 0 && runs.length === 0 ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : scope !== 'jobs' ? (
          <>
            <RunsTable runs={runs} loading={loading}
                       needFleet={scope === 'fleet-runs' && !fleetFilter}
                       showTag={scope === 'tag-runs'}
                       emptyText={scope === 'tag-runs'
                         ? 'No tag fan-out runs yet.'
                         : 'No fan-out runs for this fleet yet.'}
                       onOpen={setOpenRun} />
            {pager}
          </>
        ) : (
          <>
            <JobsTable jobs={jobs} loading={loading} fleetName={fleetName}
                       scopedToFleet={!!fleetFilter} onSelect={setSelected} />
            {pager}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function JobsTable({ jobs, loading, fleetName, scopedToFleet, onSelect }: {
  jobs: Job[]; loading: boolean; fleetName: (id?: string | null) => string;
  scopedToFleet: boolean; onSelect: (j: Job) => void;
}) {
  return (
    <div className={`bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden relative ${loading ? 'opacity-60 pointer-events-none' : ''}`}>
      {loading && <div className="absolute inset-0 flex items-center justify-center z-10"><Spinner /></div>}
      <DataTable
        tableId="jobs"
        columns={[
          { label: 'Command',    sortValue: j => j.command, required: true },
          { label: 'Status',     sortValue: j => j.status, required: true },
          { label: 'Agent',      sortValue: j => j.agent_hostname ?? j.agent_id },
          { label: 'Fleet',      sortValue: j => j.agent_fleet_id ?? '' },
          { label: 'Run',        sortValue: j => j.run_id ?? '', defaultHidden: !scopedToFleet },
          { label: 'Mode',       sortValue: j => j.agent_mode ?? '' },
          { label: 'Created by', sortValue: j => j.created_by ?? '' },
          { label: 'Started',    sortValue: j => j.created_at ?? '' },
          { label: 'Job ID',     sortValue: j => j.job_id, defaultHidden: true },
        ]}
        rows={jobs}
        fallback={
          <div className="flex flex-col items-center py-16">
            <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mb-3">
              <svg className="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" />
              </svg>
            </div>
            <p className="text-sm font-medium text-gray-500">No jobs found</p>
            <p className="text-xs text-gray-400 mt-1">Jobs appear here after agents run commands.</p>
          </div>
        }
        renderRow={j => (
          <tr key={j.job_id} className="hover:bg-slate-50/80 cursor-pointer transition-colors group" onClick={() => onSelect(j)}>
            <td className="px-4 py-3.5 font-mono text-sm text-gray-800 max-w-xs">
              <span className="bg-gray-100 group-hover:bg-gray-200 transition-colors px-2 py-0.5 rounded block truncate">{j.command}</span>
            </td>
            <td className="px-4 py-3.5"><Badge value={j.status} /></td>
            <td className="px-4 py-3.5 text-sm text-gray-700"><AgentInfo agentId={j.agent_id} hostname={j.agent_hostname} /></td>
            <td className="px-4 py-3.5 text-sm">
              {j.agent_fleet_id ? <span className="text-[11px] font-medium text-violet-700 bg-violet-50 px-2 py-0.5 rounded">{fleetName(j.agent_fleet_id)}</span> : <span className="text-gray-300">-</span>}
            </td>
            <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{j.run_id ?? '-'}</td>
            <td className="px-4 py-3.5 text-sm text-gray-600 capitalize">{j.agent_mode ?? '-'}</td>
            <td className="px-4 py-3.5 text-sm text-gray-600">{j.created_by ?? '-'}</td>
            <td className="px-4 py-3.5 text-sm text-gray-500 whitespace-nowrap">{fmtDate(j.created_at)}</td>
            <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{j.job_id}</td>
          </tr>
        )}
      />
    </div>
  );
}

function RunsTable({ runs, loading, needFleet, showTag, emptyText, onOpen }: {
  runs: FleetRun[]; loading: boolean; needFleet: boolean; showTag?: boolean; emptyText: string; onOpen: (r: FleetRun) => void;
}) {
  if (needFleet) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm py-16 text-center">
        <p className="text-sm font-medium text-gray-500">Pick a fleet to see its runs</p>
        <p className="text-xs text-gray-400 mt-1">A run is one <code className="text-[11px]">fleets exec</code> fan-out, grouping the jobs it created.</p>
      </div>
    );
  }
  return (
    <div className={`bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden relative ${loading ? 'opacity-60 pointer-events-none' : ''}`}>
      {loading && <div className="absolute inset-0 flex items-center justify-center z-10"><Spinner /></div>}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200">
            {[...(showTag ? ['Tag'] : []), 'Command', 'State', 'When', 'By', 'Members', 'OK', 'Failed', 'Pending', 'Run ID'].map(h => (
              <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.length === 0 ? (
            <tr><td colSpan={showTag ? 10 : 9} className="text-center py-14 text-sm text-gray-400">{emptyText}</td></tr>
          ) : runs.map(r => (
            <tr key={r.run_id} className="hover:bg-slate-50/80 cursor-pointer transition-colors group border-b border-gray-50" onClick={() => onOpen(r)}>
              {showTag && (
                <td className="px-4 py-3.5 whitespace-nowrap">
                  {r.tag ? <span className="text-[11px] font-mono font-medium text-sky-700 bg-sky-50 border border-sky-200 px-2 py-0.5 rounded">{r.tag}</span> : <span className="text-gray-300">-</span>}
                </td>
              )}
              <td className="px-4 py-3.5 font-mono text-sm text-gray-800 max-w-xs">
                <span className="bg-gray-100 group-hover:bg-gray-200 transition-colors px-2 py-0.5 rounded block truncate">{r.command}</span>
              </td>
              <td className="px-4 py-3.5 whitespace-nowrap">{r.state ? <Badge value={r.state} /> : <span className="text-gray-300">-</span>}</td>
              <td className="px-4 py-3.5 text-sm text-gray-500 whitespace-nowrap">{fmtDate(r.created_at)}</td>
              <td className="px-4 py-3.5 text-sm text-gray-600 whitespace-nowrap">{r.created_by ?? <span className="text-gray-300">-</span>}</td>
              <td className="px-4 py-3.5 text-sm text-gray-700">{r.members}</td>
              <td className="px-4 py-3.5 text-sm font-semibold text-emerald-700">{r.ok}</td>
              <td className="px-4 py-3.5 text-sm font-semibold">{r.failed ? <span className="text-red-600">{r.failed}</span> : <span className="text-gray-400">0</span>}</td>
              <td className="px-4 py-3.5 text-sm font-semibold">{r.pending ? <span className="text-amber-600">{r.pending}</span> : <span className="text-gray-400">0</span>}</td>
              <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{r.run_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// A run's per-member jobs (by run_id) plus the run's status - including *why* members
// were skipped or capped, so it's clear which hosts didn't run and why.
// A labeled value in the run's wave-info bar (Wave size / Strategy / On failure / ...).
function WaveInfo({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-indigo-400">{label}</span>
      <span className="font-semibold text-indigo-800">{value}</span>
    </span>
  );
}

// Summarize a wave's member jobs into a status pill for the per-wave breakdown.
function waveState(waveJobs: Job[]): { key: string; label: string; cls: string } {
  if (waveJobs.every(j => j.status === 'HELD')) return { key: 'held', label: 'held', cls: 'bg-slate-100 text-slate-500' };
  if (waveJobs.every(j => j.status === 'CANCELED')) return { key: 'canceled', label: 'canceled', cls: 'bg-gray-100 text-gray-500' };
  const active = waveJobs.some(j => j.status === 'PENDING' || j.status === 'RUNNING' || j.status === 'HELD');
  if (active) return { key: 'running', label: 'running', cls: 'bg-blue-50 text-blue-700' };
  const failed = waveJobs.some(j => ['FAILED', 'REJECTED', 'EXPIRED'].includes(j.status) || (j.status === 'SUCCEEDED' && (j.exit_code ?? 0) !== 0));
  return failed ? { key: 'issues', label: 'done · issues', cls: 'bg-amber-50 text-amber-700' }
                : { key: 'done', label: 'done', cls: 'bg-emerald-50 text-emerald-700' };
}

function RunDetailModal({ run, config, scopeLabel, onClose, onOpenJob }: {
  run: FleetRun; config: TenantConfig; scopeLabel: string;
  onClose: () => void; onOpenJob: (j: Job) => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const load = useCallback(() => {
    return Promise.all([
      listTenantJobs(config.apiUrl, config.tenantToken, { run_id: run.run_id }).then(r => r.jobs ?? []).catch(() => []),
      getRun(config.apiUrl, config.tenantToken, run.run_id).catch(() => null),
    ]).then(([js, st]) => { setJobs(js); setStatus(st); });
  }, [config.apiUrl, config.tenantToken, run.run_id]);

  useEffect(() => { load().finally(() => setLoading(false)); }, [load]);

  const skipped = status?.skipped ?? [];

  // Every run is wave-based. Surface its wave size + advancement strategy + failure policy.
  const waves = status?.rollout?.waves ?? [];
  const waveSize = waves.length ? Math.max(...waves) : (status?.total ?? 0);
  const waveMode = status?.rollout?.mode ?? 'auto';        // auto | manual
  const onFailure = status?.rollout?.on_failure ?? 'stop'; // stop | continue
  const curWave = status ? Math.min((status.current_wave ?? 0) + 1, status.wave_total) : 1;

  // A multi-wave run (later waves still held) can be paused/resumed/cancelled; a terminal
  // or single-wave run has nothing left to control.
  const staged = (status?.wave_total ?? 1) > 1;
  const state = status?.state ?? run.state ?? '';
  const controllable = staged && !status?.terminal && state !== 'canceled';

  const act = async (fn: (u: string, t: string, id: string) => Promise<unknown>) => {
    setActing(true); setActionErr(null);
    try {
      await fn(config.apiUrl, config.tenantToken, run.run_id);
      await load();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setActing(false);
    }
  };

  return (
    <Modal wide title={<span className="font-semibold">Run · <code className="font-mono text-sm">{run.command}</code></span>} onClose={onClose}>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="bg-violet-50 text-violet-700 px-2 py-1 rounded">{scopeLabel}</span>
          {state && <Badge value={state} />}
          <span className="bg-gray-100 text-gray-600 px-2 py-1 rounded font-mono">{run.run_id}</span>
          <span className="text-gray-500 px-1 py-1">{fmtDate(run.created_at)}</span>
          <span className="text-emerald-700 px-2 py-1">{run.ok} ok</span>
          {run.failed > 0 && <span className="text-red-600 px-2 py-1">{run.failed} failed</span>}
          {run.pending > 0 && <span className="text-amber-600 px-2 py-1">{run.pending} pending</span>}
          {skipped.length > 0 && <span className="text-gray-500 px-2 py-1">{status?.skipped_count} skipped</span>}
        </div>

        {/* Wave info (every run is wave-based) + rollout progress + controls. */}
        {status && (
          <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 px-3 py-2.5 space-y-2">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
              <WaveInfo label="Wave size" value={`${waveSize} host${waveSize !== 1 ? 's' : ''}`} />
              <WaveInfo label="Strategy" value={waveMode === 'manual' ? 'MANUAL' : 'AUTO'} />
              <WaveInfo label="On failure" value={onFailure === 'continue' ? 'CONTINUE' : 'STOP'} />
              <WaveInfo label="Progress" value={`Wave ${curWave} of ${status.wave_total}`} />
              {status.staged > 0 && <span className="text-indigo-600 font-medium">{status.staged} held</span>}
            </div>
            {controllable && (
              <div className="flex items-center gap-1.5 pt-0.5">
                {state === 'paused' ? (
                  <button disabled={acting} onClick={() => act(resumeRun)}
                    className="text-xs font-medium px-2.5 py-1 rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50">Resume next wave</button>
                ) : (
                  <button disabled={acting} onClick={() => act(pauseRun)}
                    className="text-xs font-medium px-2.5 py-1 rounded bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50">Pause</button>
                )}
                <button disabled={acting} onClick={() => act(cancelRun)}
                  className="text-xs font-medium px-2.5 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50">Cancel</button>
              </div>
            )}
          </div>
        )}
        {actionErr && <p className="text-xs text-red-600">{actionErr}</p>}
        {loading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : (
          <>
            {status ? (
              // Every run is wave-based: one table per wave (a small run is just "wave 1
              // of 1"), so it's always clear which hosts are in each wave and what happened.
              // Later waves sit HELD until released.
              <div className="space-y-3">
                {Array.from({ length: status.wave_total }, (_, w) => {
                  const wj = jobs.filter(j => (j.wave ?? 0) === w);
                  if (!wj.length) return null;
                  const ws = waveState(wj);
                  const isCurrent = (status.current_wave ?? 0) === w && ws.key !== 'held';
                  const ok = wj.filter(j => j.status === 'SUCCEEDED' && (j.exit_code ?? 0) === 0).length;
                  const failed = wj.filter(j => ['FAILED', 'REJECTED', 'EXPIRED', 'CANCELED'].includes(j.status)
                    || (j.status === 'SUCCEEDED' && (j.exit_code ?? 0) !== 0)).length;
                  return (
                    <div key={w} className="border border-gray-100 rounded-lg overflow-hidden">
                      <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-100">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold text-gray-700">Wave {w + 1} <span className="font-normal text-gray-400">of {status.wave_total}</span></span>
                          <span className={`text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${ws.cls}`}>{ws.label}</span>
                          {isCurrent && <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600">current</span>}
                        </div>
                        <div className="flex items-center gap-2.5 text-[11px]">
                          <span className="text-gray-400">{wj.length} host{wj.length !== 1 ? 's' : ''}</span>
                          {ok > 0 && <span className="text-emerald-600">{ok} ok</span>}
                          {failed > 0 && <span className="text-red-600">{failed} failed</span>}
                        </div>
                      </div>
                      <table className="w-full text-sm">
                        <tbody>
                          {wj.map(j => {
                            const held = j.status === 'HELD';
                            return (
                              <tr key={j.job_id} className={`border-b border-gray-50 last:border-0 ${held ? 'opacity-60' : 'hover:bg-slate-50 cursor-pointer'}`}
                                  onClick={() => { if (!held) onOpenJob(j); }}>
                                <td className="px-3 py-2 text-gray-700">{j.agent_hostname ?? j.agent_id}</td>
                                <td className="px-3 py-2"><Badge value={j.status} /></td>
                                <td className="px-3 py-2 font-mono text-xs text-gray-600 text-right w-16">{j.exit_code ?? '-'}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  );
                })}
                <p className="text-[11px] text-gray-400">
                  Grouped by rollout wave.{status.wave_total > 1 ? ' Held waves release as earlier waves finish (or when you resume).' : ''} Click a member to see its full output.
                </p>
              </div>
            ) : (
              <>
                <div className="border border-gray-100 rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead><tr className="bg-gray-50 border-b border-gray-100">
                      {['Member', 'Status', 'Exit', 'Duration'].map(h => <th key={h} className="text-left px-3 py-2 text-[11px] font-semibold text-gray-500 uppercase">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {jobs.map(j => (
                        <tr key={j.job_id} className="hover:bg-slate-50 cursor-pointer border-b border-gray-50" onClick={() => onOpenJob(j)}>
                          <td className="px-3 py-2 text-gray-700">{j.agent_hostname ?? j.agent_id}</td>
                          <td className="px-3 py-2"><Badge value={j.status} /></td>
                          <td className="px-3 py-2 font-mono text-xs text-gray-600">{j.exit_code ?? '-'}</td>
                          <td className="px-3 py-2 text-xs text-gray-500">{duration(j.started_at, j.completed_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[11px] text-gray-400">Click a member to see its full output.</p>
              </>
            )}

            {/* Why members didn't run - skipped (inactive / read-only / unapproved). */}
            {skipped.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-500 mb-1">Skipped ({status?.skipped_count})</p>
                <div className="border border-amber-100 bg-amber-50/40 rounded-lg divide-y divide-amber-100/60">
                  {skipped.map(s => (
                    <div key={s.agent_id} className="flex items-center justify-between px-3 py-1.5 text-xs">
                      <span className="text-gray-700">{s.hostname ?? s.agent_id}</span>
                      <span className="text-amber-700">{s.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function IdRow({ label, id }: { label: string; id: string }) {
  return (
    <div className="flex items-center justify-between gap-2 group/idr">
      <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider shrink-0">{label}</span>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="text-[11px] font-mono text-gray-500 truncate" title={id}>{id}</span>
        <CopyButton text={id} className="opacity-0 group-hover/idr:opacity-100 transition-opacity shrink-0 !px-1.5 !py-0.5 text-[10px]" />
      </div>
    </div>
  );
}

function JobDetailModal({ job, fleetName, onClose, onBack }: {
  job: Job; fleetName?: string; onClose: () => void; onBack?: () => void;
}) {
  // When opened from a run, `onBack` returns to that run's wave view.
  const title = (
    <span className="flex items-center gap-2 min-w-0">
      {onBack && (
        <button onClick={onBack} title="Back to wave view"
          className="flex items-center justify-center w-6 h-6 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors shrink-0">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
          </svg>
        </button>
      )}
      <code className="font-mono text-sm font-semibold truncate">{job.command}</code>
    </span>
  );
  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-gray-50 rounded-lg px-3 py-2.5 space-y-1.5 border border-gray-100">
          <IdRow label="Job ID" id={job.job_id} />
          <IdRow label="User ID" id={job.created_by} />
          <IdRow label="Agent ID" id={job.agent_id} />
          {job.run_id && <IdRow label="Run ID" id={job.run_id} />}
        </div>

        <div className="grid grid-cols-2 gap-3">
          {[
            { label: 'Status',    value: <Badge value={job.status} /> },
            { label: 'Exit code', value: <span className="text-sm font-mono">{job.exit_code ?? '-'}</span> },
            { label: 'Duration',  value: <span className="text-sm">{duration(job.started_at, job.completed_at)}</span> },
            { label: 'Agent',     value: <span className="text-sm text-gray-700">{job.agent_hostname ?? job.agent_id}</span> },
            ...(job.agent_fleet_id ? [{ label: 'Fleet', value: <span className="text-sm text-violet-700">{fleetName ?? job.agent_fleet_id}</span> }] : []),
            { label: 'Created',   value: <span className="text-sm text-gray-500">{fmtDate(job.created_at)}</span> },
            { label: 'Completed', value: <span className="text-sm text-gray-500">{fmtDate(job.completed_at)}</span> },
          ].map(({ label, value }) => (
            <div key={label} className="bg-gray-50 rounded-lg px-3 py-2.5">
              <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">{label}</p>
              {value}
            </div>
          ))}
        </div>

        {job.stdout && (
          <div>
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1.5">stdout</p>
            <pre className="text-xs bg-gray-950 text-emerald-400 rounded-xl px-4 py-3 overflow-auto whitespace-pre-wrap max-h-56">{job.stdout}</pre>
          </div>
        )}
        {job.stderr && (
          <div>
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1.5">stderr</p>
            <pre className="text-xs bg-gray-950 text-red-400 rounded-xl px-4 py-3 overflow-auto whitespace-pre-wrap max-h-56">{job.stderr}</pre>
          </div>
        )}
        {!job.stdout && !job.stderr && (
          <p className="text-sm text-gray-400 text-center py-4">No output captured</p>
        )}

        <div className="flex justify-end pt-1">
          <button onClick={onClose} className="bg-gray-800 hover:bg-gray-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">Close</button>
        </div>
      </div>
    </Modal>
  );
}
