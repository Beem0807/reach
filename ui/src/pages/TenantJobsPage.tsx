import { useState, useEffect, useCallback } from 'react';
import type { TenantConfig, Job, Agent } from '../types';
import { listTenantJobs, listTenantAgents } from '../api';
import { Badge } from '../components/Badge';
import { Spinner } from '../components/Spinner';
import { DataTable } from '../components/DataTable';
import { AgentInfo } from '../components/AgentInfo';
import { Modal } from '../components/Modal';
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

export function TenantJobsPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;
  const [jobs, setJobs] = useState<Job[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Job | null>(null);
  const [agentFilter, setAgentFilter] = useState('');
  const [searchedAgentId, setSearchedAgentId] = useState('');

  // Load agents for the dropdown
  const loadAgents = useCallback(() => {
    listTenantAgents(apiUrl, tenantToken)
      .then(r => setAgents(r.agents ?? []))
      .catch(() => {});
  }, [apiUrl, tenantToken]);
  useEffect(() => { loadAgents(); }, [loadAgents]);

  // Load jobs for the current agent filter
  const loadJobs = useCallback(() => {
    setLoading(true);
    setError('');
    const params: Record<string, string> = {};
    if (searchedAgentId) params.agent_id = searchedAgentId;
    listTenantJobs(apiUrl, tenantToken, params)
      .then(r => setJobs(r.jobs ?? []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiUrl, tenantToken, searchedAgentId]);
  useEffect(() => { loadJobs(); }, [loadJobs]);

  const refresh = () => { loadJobs(); loadAgents(); };

  const handleSearch = () => setSearchedAgentId(agentFilter);
  const handleClear = () => { setAgentFilter(''); setSearchedAgentId(''); };

  const runningCount   = jobs.filter(j => j.status === 'RUNNING').length;
  const completedCount = jobs.filter(j => j.status === 'SUCCEEDED').length;
  const failedCount    = jobs.filter(j => j.status === 'FAILED').length;

  return (
    <div className="min-h-full bg-slate-50">
      {/* Page header */}
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
            {!loading && jobs.length > 0 && (
              <>
                {runningCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-blue-500/20 border border-blue-400/30 text-blue-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse shrink-0" />
                    {runningCount} running
                  </span>
                )}
                {completedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-emerald-500/20 border border-emerald-400/30 text-emerald-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-300 shrink-0" />
                    {completedCount} completed
                  </span>
                )}
                {failedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 bg-red-500/20 border border-red-400/30 text-red-200 text-xs font-semibold px-3 py-1.5 rounded-lg">
                    {failedCount} failed
                  </span>
                )}
              </>
            )}
            <RefreshButton onClick={refresh} loading={loading} />
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-4">
        {/* Agent filter bar */}
        <div className="bg-white border border-gray-200 rounded-xl px-4 py-3 shadow-sm flex items-center gap-3">
          <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider shrink-0">Agent</label>
          <select
            value={agentFilter}
            onChange={e => setAgentFilter(e.target.value)}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white"
          >
            <option value="">All agents</option>
            {agents.map(a => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.hostname ?? a.agent_id}
              </option>
            ))}
          </select>
          <button
            onClick={handleSearch}
            disabled={loading}
            className="inline-flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-semibold px-4 py-1.5 rounded-lg transition-colors"
          >
            {loading ? <Spinner className="h-4 w-4" /> : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
              </svg>
            )}
            Search
          </button>
          {searchedAgentId && (
            <button
              onClick={handleClear}
              className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
            >
              Clear
            </button>
          )}
        </div>

        {error && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>
        )}

        {selected && (
          <JobDetailModal job={selected} onClose={() => setSelected(null)} />
        )}

        {loading && jobs.length === 0 ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : (
          <div className={`bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden relative ${loading ? 'opacity-60 pointer-events-none' : ''}`}>
            {loading && <div className="absolute inset-0 flex items-center justify-center z-10"><Spinner /></div>}
            <DataTable
              tableId="jobs"
              columns={[
                { label: 'Command',    sortValue: j => j.command, required: true },
                { label: 'Status',     sortValue: j => j.status, required: true },
                { label: 'Agent',      sortValue: j => j.agent_hostname ?? j.agent_id },
                { label: 'Mode',       sortValue: j => j.agent_mode ?? '' },
                { label: 'Created by', sortValue: j => j.created_by ?? '' },
                { label: 'Started',    sortValue: j => j.created_at ?? '' },
                { label: 'Job ID',     sortValue: j => j.job_id, defaultHidden: true },
                { label: 'User ID',    sortValue: j => j.created_by ?? '', defaultHidden: true },
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
                  <p className="text-xs text-gray-400 mt-1">
                    {searchedAgentId ? 'No jobs for this agent - try a different filter.' : 'Jobs appear here after agents run commands.'}
                  </p>
                </div>
              }
              renderRow={j => (
                <tr
                  key={j.job_id}
                  className="hover:bg-slate-50/80 cursor-pointer transition-colors group"
                  onClick={() => setSelected(prev => prev?.job_id === j.job_id ? null : j)}
                >
                  <td className="px-4 py-3.5 font-mono text-sm text-gray-800 max-w-xs">
                    <span className="bg-gray-100 group-hover:bg-gray-200 transition-colors px-2 py-0.5 rounded block truncate">{j.command}</span>
                  </td>
                  <td className="px-4 py-3.5"><Badge value={j.status} /></td>
                  <td className="px-4 py-3.5 text-sm text-gray-700"><AgentInfo agentId={j.agent_id} hostname={j.agent_hostname} /></td>
                  <td className="px-4 py-3.5 text-sm text-gray-600 capitalize">{j.agent_mode ?? '-'}</td>
                  <td className="px-4 py-3.5 text-sm text-gray-600">{j.created_by ?? '-'}</td>
                  <td className="px-4 py-3.5 text-sm text-gray-500 whitespace-nowrap">{fmtDate(j.created_at)}</td>
                  <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{j.job_id}</td>
                  <td className="px-4 py-3.5 font-mono text-xs text-gray-400">{j.created_by ?? '-'}</td>
                </tr>
              )}
            />
          </div>
        )}
      </div>
    </div>
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

function JobDetailModal({ job, onClose }: { job: Job; onClose: () => void }) {
  return (
    <Modal title={<code className="font-mono text-sm font-semibold">{job.command}</code>} onClose={onClose}>
      <div className="space-y-4">
        {/* IDs */}
        <div className="bg-gray-50 rounded-lg px-3 py-2.5 space-y-1.5 border border-gray-100">
          <IdRow label="Job ID" id={job.job_id} />
          <IdRow label="User ID" id={job.created_by} />
          <IdRow label="Agent ID" id={job.agent_id} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          {[
            { label: 'Status',    value: <Badge value={job.status} /> },
            { label: 'Exit code', value: <span className="text-sm font-mono">{job.exit_code ?? '-'}</span> },
            { label: 'Duration',  value: <span className="text-sm">{duration(job.started_at, job.completed_at)}</span> },
            { label: 'Agent',     value: <span className="text-sm text-gray-700">{job.agent_hostname ?? job.agent_id}</span> },
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
