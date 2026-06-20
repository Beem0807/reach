import { useState, useEffect } from 'react';
import type { Agent, Approval, AuditLog, TenantConfig } from '../types';
import { listTenantAgents, listAllTenantApprovals, listTenantAuditLogs } from '../api';
import { Spinner } from '../components/Spinner';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function relTime(iso?: string | null) {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  const h = Math.floor(diff / 3600000);
  const d = Math.floor(diff / 86400000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  if (h < 24) return `${h}h ago`;
  return `${d}d ago`;
}

const ACTION_META: Record<string, { label: string; dot: string; text: string }> = {
  'user.login':            { label: 'login',           dot: 'bg-gray-400',    text: 'text-gray-600' },
  'user.created':          { label: 'user created',    dot: 'bg-indigo-500',  text: 'text-indigo-700' },
  'user.disabled':         { label: 'user disabled',   dot: 'bg-red-500',     text: 'text-red-700' },
  'user.password_changed': { label: 'password change', dot: 'bg-amber-400',   text: 'text-amber-700' },
  'user.password_reset':   { label: 'pwd reset',       dot: 'bg-amber-400',   text: 'text-amber-700' },
  'user.role_changed':     { label: 'role change',     dot: 'bg-purple-500',  text: 'text-purple-700' },
  'agent.created':         { label: 'agent created',   dot: 'bg-indigo-500',  text: 'text-indigo-700' },
  'agent.revoked':         { label: 'agent revoked',   dot: 'bg-red-500',     text: 'text-red-700' },
  'agent.deleted':         { label: 'agent deleted',   dot: 'bg-red-400',     text: 'text-red-700' },
  'agent.removed':         { label: 'agent removed',   dot: 'bg-gray-500',    text: 'text-gray-700' },
  'agent.unreachable':          { label: 'agent offline',      dot: 'bg-amber-400',   text: 'text-amber-700' },
  'agent.recovered':            { label: 'agent recovered',    dot: 'bg-emerald-500', text: 'text-emerald-700' },
  'agent.install_token_reissued': { label: 'token reissued',  dot: 'bg-amber-400',   text: 'text-amber-700' },
  'agent.tags_changed':         { label: 'tags changed',       dot: 'bg-purple-500',  text: 'text-purple-700' },
  'agent.rotation_requested':   { label: 'rotation requested', dot: 'bg-amber-400',   text: 'text-amber-700' },
  'agent.mode_changed':         { label: 'mode change',        dot: 'bg-purple-500',  text: 'text-purple-700' },
  'approval.requested':         { label: 'approval requested', dot: 'bg-amber-400',   text: 'text-amber-700' },
  'approval.approved':          { label: 'approved',           dot: 'bg-emerald-500', text: 'text-emerald-700' },
  'approval.denied':            { label: 'denied',             dot: 'bg-red-500',     text: 'text-red-700' },
  'approval.pre_approved':      { label: 'pre-approved',       dot: 'bg-emerald-400', text: 'text-emerald-700' },
  'approval.deleted':           { label: 'approval deleted',   dot: 'bg-gray-400',    text: 'text-gray-600' },
  'tenant.deleted':             { label: 'tenant deleted',     dot: 'bg-red-500',     text: 'text-red-700' },
  'user.name_changed':          { label: 'name changed',       dot: 'bg-purple-500',  text: 'text-purple-700' },
  'user.enabled':               { label: 'user enabled',       dot: 'bg-emerald-500', text: 'text-emerald-700' },
  'api_token.created':     { label: 'token created',   dot: 'bg-indigo-400',  text: 'text-indigo-700' },
  'api_token.revoked':     { label: 'token revoked',   dot: 'bg-red-400',     text: 'text-red-700' },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({ label, value, sub, icon, accent, alert = false }: {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.ReactNode;
  accent: string;
  alert?: boolean;
}) {
  return (
    <div className={`bg-white rounded-2xl border shadow-sm p-5 flex items-start gap-4 ${alert ? 'border-amber-300' : 'border-gray-200'}`}>
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${accent}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-widest">{label}</p>
        <p className="text-3xl font-bold text-gray-900 mt-0.5 leading-none">{value}</p>
        {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
      </div>
    </div>
  );
}

function AgentHealthBar({ agents }: { agents: Agent[] }) {
  const total = agents.length;
  if (total === 0) return null;
  const created  = agents.filter(a => a.status === 'CREATED').length;
  const active   = agents.filter(a => a.status === 'ACTIVE').length;
  const inactive = agents.filter(a => a.status === 'INACTIVE').length;
  const revoked  = agents.filter(a => a.status === 'REVOKED').length;
  const deleted  = agents.filter(a => a.status === 'DELETED').length;
  const pct = (n: number) => `${Math.round((n / total) * 100)}%`;
  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700">Agent health</h2>
        <span className="text-xs text-gray-400">{total} total</span>
      </div>
      <div className="flex h-3 rounded-full overflow-hidden gap-0.5">
        {active > 0   && <div className="bg-emerald-400 transition-all" style={{ width: pct(active) }}   title={`${active} active`} />}
        {inactive > 0 && <div className="bg-amber-300 transition-all"   style={{ width: pct(inactive) }} title={`${inactive} inactive`} />}
        {created > 0  && <div className="bg-blue-300 transition-all"    style={{ width: pct(created) }}  title={`${created} created`} />}
        {revoked > 0  && <div className="bg-red-400 transition-all"     style={{ width: pct(revoked) }}  title={`${revoked} revoked`} />}
        {deleted > 0  && <div className="bg-gray-300 transition-all"    style={{ width: pct(deleted) }}  title={`${deleted} deleted`} />}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2.5 text-xs">
        {active > 0   && <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0" />{active} active</span>}
        {inactive > 0 && <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-full bg-amber-300 shrink-0" />{inactive} inactive</span>}
        {created > 0  && <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-full bg-blue-300 shrink-0" />{created} created</span>}
        {revoked > 0  && <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-full bg-red-400 shrink-0" />{revoked} revoked</span>}
        {deleted > 0  && <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-full bg-gray-300 shrink-0" />{deleted} deleted</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function DashboardPage({ config }: { config: TenantConfig }) {
  const { apiUrl, tenantToken } = config;

  const [agents, setAgents]       = useState<Agent[]>([]);
  const [pending, setPending]     = useState<Approval[]>([]);
  const [recentLogs, setRecentLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading]     = useState(true);

  useEffect(() => {
    const sinceOneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    Promise.all([
      listTenantAgents(apiUrl, tenantToken).then(r => setAgents(r.agents ?? [])).catch(() => {}),
      listAllTenantApprovals(apiUrl, tenantToken, { status: 'pending' })
        .then(r => setPending(r.approvals ?? [])).catch(() => {}),
      listTenantAuditLogs(apiUrl, tenantToken, { since: sinceOneHourAgo, limit: '200' })
        .then(r => setRecentLogs(r.logs ?? [])).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, [apiUrl, tenantToken]);

  const activeAgents = agents.filter(a => a.status === 'ACTIVE').length;
  const inactiveAgents = agents.filter(a => a.status === 'INACTIVE').length;

  if (loading) {
    return (
      <div className="min-h-full bg-slate-50 flex items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="min-h-full bg-slate-50">
      {/* Header */}
      <div className="bg-gradient-to-r from-slate-800 to-slate-700 px-8 py-5">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/20 flex items-center justify-center shrink-0">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">Overview</h1>
            <p className="text-sm text-slate-300">{config.tenantName}</p>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">

        {/* Stat cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Active agents"
            value={activeAgents}
            sub={`of ${agents.length} registered`}
            accent="bg-emerald-100"
            icon={
              <svg className="w-5 h-5 text-emerald-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3" />
              </svg>
            }
          />
          <StatCard
            label="Pending approvals"
            value={pending.length}
            sub={pending.length === 0 ? 'All clear' : `${pending.length} need${pending.length === 1 ? 's' : ''} review`}
            accent={pending.length > 0 ? 'bg-amber-100' : 'bg-gray-100'}
            alert={pending.length > 0}
            icon={
              <svg className={`w-5 h-5 ${pending.length > 0 ? 'text-amber-600' : 'text-gray-400'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
            }
          />
          <StatCard
            label="Inactive agents"
            value={inactiveAgents}
            sub={inactiveAgents === 0 ? 'All agents online' : `${inactiveAgents} offline`}
            accent={inactiveAgents > 0 ? 'bg-red-100' : 'bg-gray-100'}
            alert={inactiveAgents > 0}
            icon={
              <svg className={`w-5 h-5 ${inactiveAgents > 0 ? 'text-red-600' : 'text-gray-400'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
              </svg>
            }
          />
          <StatCard
            label="Events (1h)"
            value={recentLogs.length >= 200 ? '200+' : recentLogs.length}
            accent="bg-indigo-100"
            icon={
              <svg className="w-5 h-5 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" />
              </svg>
            }
          />
        </div>

        {/* Agent health bar */}
        {agents.length > 0 && <AgentHealthBar agents={agents} />}

        {/* Bottom two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

          {/* Pending approvals panel */}
          <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
            <div className={`px-5 py-3.5 border-b flex items-center justify-between ${pending.length > 0 ? 'bg-amber-50/70 border-amber-200' : 'bg-gray-50/60 border-gray-100'}`}>
              <div className="flex items-center gap-2">
                {pending.length > 0 && <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse shrink-0" />}
                <h2 className="text-sm font-semibold text-gray-700">Pending approvals</h2>
              </div>
              {pending.length > 0 && (
                <span className="text-xs font-bold text-amber-700 bg-amber-100 border border-amber-200 px-2 py-0.5 rounded-full">
                  {pending.length}
                </span>
              )}
            </div>
            {pending.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center px-4">
                <div className="w-10 h-10 rounded-full bg-emerald-50 flex items-center justify-center mb-2">
                  <svg className="w-5 h-5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-gray-500">All clear</p>
                <p className="text-xs text-gray-400 mt-0.5">No pending approval requests</p>
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {pending.slice(0, 6).map(a => (
                  <li key={a.approval_id} className="px-5 py-3 flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <code className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded block truncate text-gray-800">
                        {a.command}
                      </code>
                      <p className="text-[11px] text-gray-400 mt-0.5 truncate">{a.agent_hostname ?? a.agent_id}</p>
                    </div>
                    <span className="text-[11px] text-gray-400 whitespace-nowrap shrink-0">{relTime(a.created_at)}</span>
                  </li>
                ))}
                {pending.length > 6 && (
                  <li className="px-5 py-2.5 text-center text-xs text-gray-400 bg-gray-50/60">
                    +{pending.length - 6} more pending
                  </li>
                )}
              </ul>
            )}
          </div>

          {/* Recent activity timeline */}
          <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-gray-100 bg-gray-50/60">
              <h2 className="text-sm font-semibold text-gray-700">Recent activity</h2>
            </div>
            {recentLogs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center px-4">
                <p className="text-sm text-gray-400">No recent audit events</p>
              </div>
            ) : (
              <div className="px-5 py-3 space-y-0">
                {recentLogs.map((l, idx) => {
                  const meta = ACTION_META[l.action] ?? { label: l.action, dot: 'bg-gray-300', text: 'text-gray-500' };
                  const isLast = idx === recentLogs.length - 1;
                  return (
                    <div key={l.log_id} className="flex gap-3 min-h-[2.5rem]">
                      {/* Timeline spine */}
                      <div className="flex flex-col items-center shrink-0 pt-1">
                        <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${meta.dot}`} />
                        {!isLast && <div className="w-px flex-1 bg-gray-100 mt-1" />}
                      </div>
                      {/* Content */}
                      <div className={`flex items-start justify-between gap-2 w-full ${isLast ? 'pb-0' : 'pb-3'}`}>
                        <div className="min-w-0">
                          <span className={`text-[11px] font-semibold uppercase tracking-wider ${meta.text}`}>{meta.label}</span>
                          {(l.actor_name ?? l.actor_id) && (
                            <span className="text-xs text-gray-500 ml-1.5">{l.actor_name ?? l.actor_id}</span>
                          )}
                          {l.resource_id && (
                            <p className="text-[10px] font-mono text-gray-400 mt-0.5 truncate">{l.resource_id}</p>
                          )}
                        </div>
                        <span className="text-[11px] text-gray-400 whitespace-nowrap shrink-0 mt-0.5">{fmtDate(l.created_at)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

        </div>
      </div>
    </div>
  );
}
