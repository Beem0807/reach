import { useState, type ReactNode } from 'react';
import { CopyButton } from './components/CopyButton';
import type { TenantConfig, TenantRole } from './types';
import { TenantUsersPage } from './pages/TenantUsersPage';
import { TenantApiTokensPage } from './pages/TenantApiTokensPage';
import { AuditLogsPage } from './pages/AuditLogsPage';
import { TenantAgentsPage } from './pages/TenantAgentsPage';
import { FleetsPage } from './pages/FleetsPage';
import { TenantJobsPage } from './pages/TenantJobsPage';
import { TenantApprovalsPage } from './pages/TenantApprovalsPage';
import { TenantSettingsPage } from './pages/TenantSettingsPage';
import { DashboardPage } from './pages/DashboardPage';

type TenantPage = 'dashboard' | 'users' | 'agents' | 'fleets' | 'jobs' | 'approvals' | 'api-tokens' | 'audit-logs' | 'settings';

type NavItem = {
  id: TenantPage;
  label: string;
  minRole: TenantRole; // minimum role that can see this item
  icon: ReactNode;
};

// Role hierarchy: admin > operator > developer
const ROLE_RANK: Record<TenantRole, number> = { admin: 3, operator: 2, developer: 1 };

function canSee(userRole: TenantRole, minRole: TenantRole): boolean {
  return ROLE_RANK[userRole] >= ROLE_RANK[minRole];
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    minRole: 'operator',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
      </svg>
    ),
  },
  {
    id: 'users',
    label: 'Users',
    minRole: 'admin',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
      </svg>
    ),
  },
  {
    id: 'agents',
    label: 'Agents',
    minRole: 'developer',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 17.25v-.228a4.5 4.5 0 00-.12-1.03l-2.268-9.64a3.375 3.375 0 00-3.285-2.602H7.923a3.375 3.375 0 00-3.285 2.602l-2.268 9.64a4.5 4.5 0 00-.12 1.03v.228m19.5 0a3 3 0 01-3 3H5.25a3 3 0 01-3-3m19.5 0a3 3 0 00-3-3H5.25a3 3 0 00-3 3m16.5 0h.008v.008h-.008v-.008zm-3 0h.008v.008h-.008v-.008z" />
      </svg>
    ),
  },
  {
    id: 'fleets',
    label: 'Fleets',
    // Developers can view the fleets they're granted (read-only); management actions
    // inside the page are gated to operator+.
    minRole: 'developer',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3m-19.5 0a4.5 4.5 0 01.9-2.7L5.737 5.1a3.375 3.375 0 012.7-1.35h7.126c1.062 0 2.062.5 2.7 1.35l2.587 3.45a4.5 4.5 0 01.9 2.7" />
      </svg>
    ),
  },
  {
    id: 'jobs',
    label: 'Jobs',
    minRole: 'developer',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" />
      </svg>
    ),
  },
  {
    id: 'approvals',
    label: 'Approvals',
    minRole: 'developer',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
  },
  {
    id: 'api-tokens',
    label: 'API Tokens',
    minRole: 'developer',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
      </svg>
    ),
  },
  {
    id: 'audit-logs',
    label: 'Audit Logs',
    minRole: 'admin',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V19.5a2.25 2.25 0 002.25 2.25h.75m0-3.375h3.75m-3.75 3.375h3.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    minRole: 'admin',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

const ROLE_LABEL: Record<TenantRole, string> = {
  admin: 'Admin',
  operator: 'Operator',
  developer: 'Developer',
};

function NavButton({ item, active, onClick, collapsed }: { item: NavItem; active: boolean; onClick: () => void; collapsed?: boolean }) {
  return (
    <button
      onClick={onClick}
      title={collapsed ? item.label : undefined}
      className={`w-full flex items-center py-2 rounded-md text-sm font-medium transition-all duration-150 text-left ${
        collapsed ? 'justify-center px-2' : 'gap-3 px-3'
      } ${
        active
          ? 'bg-indigo-600 text-white shadow-sm'
          : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100'
      }`}
    >
      {item.icon}
      {!collapsed && item.label}
    </button>
  );
}

function ChevronIcon({ dir }: { dir: 'left' | 'right' }) {
  return (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
      {dir === 'left'
        ? <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
        : <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />}
    </svg>
  );
}

function TenantSidebar({
  config,
  page,
  onNavigate,
  onSignOut,
}: {
  config: TenantConfig;
  page: TenantPage;
  onNavigate: (p: TenantPage) => void;
  onSignOut: () => void;
}) {
  const visibleNav = NAV_ITEMS.filter(n => canSee(config.role, n.minRole));
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('sidebar_collapsed') === 'true'; } catch { return false; }
  });

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try { localStorage.setItem('sidebar_collapsed', String(next)); } catch {}
  };

  return (
    <aside className={`${collapsed ? 'w-16' : 'w-56'} bg-slate-950 flex flex-col shrink-0 border-r border-slate-800 transition-all duration-200`}>
      {/* Logo + collapse toggle */}
      <div className={`${collapsed ? 'px-2 py-4' : 'px-4 py-4'} border-b border-slate-800/60`}>
        {collapsed ? (
          <div className="flex flex-col items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center shrink-0">
              <span className="text-white text-xs font-bold tracking-tight">R</span>
            </div>
            <button onClick={toggle} title="Expand sidebar" className="text-slate-500 hover:text-slate-300 p-1 rounded-md hover:bg-slate-800 transition-colors">
              <ChevronIcon dir="right" />
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center shrink-0">
                <span className="text-white text-xs font-bold tracking-tight">R</span>
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-white font-semibold text-sm leading-none">reach</p>
                <p className="text-slate-400 text-[11px] mt-0.5">Console</p>
              </div>
              <button onClick={toggle} title="Collapse sidebar" className="text-slate-500 hover:text-slate-300 p-1 rounded-md hover:bg-slate-800 transition-colors shrink-0">
                <ChevronIcon dir="left" />
              </button>
            </div>
            <div className="bg-slate-900 rounded-lg px-3 py-2.5 space-y-1.5">
              <div>
                <p className="text-[9px] font-bold text-slate-600 uppercase tracking-wider mb-0.5">Tenant</p>
                <p className="text-slate-200 text-xs font-medium truncate" title={config.tenantName}>{config.tenantName}</p>
              </div>
              <div>
                <p className="text-[9px] font-bold text-slate-600 uppercase tracking-wider mb-0.5">Tenant ID</p>
                <div className="flex items-center gap-1.5 group/tid">
                  <p className="text-slate-500 text-[10px] font-mono truncate flex-1" title={config.tenantId}>{config.tenantId}</p>
                  <CopyButton text={config.tenantId} className="opacity-0 group-hover/tid:opacity-100 transition-opacity shrink-0 !px-1 !py-0.5 border-slate-700 text-slate-500 hover:text-slate-300 hover:border-slate-500 hover:bg-slate-800" />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 overflow-y-auto">
        <div className="space-y-0.5">
          {visibleNav.map(item => (
            <NavButton key={item.id} item={item} active={page === item.id} onClick={() => onNavigate(item.id)} collapsed={collapsed} />
          ))}
        </div>
      </nav>

      {/* User info + sign out */}
      <div className="px-2 py-3 border-t border-slate-800/60 space-y-1">
        {!collapsed && (
          <div className="px-3 py-1.5">
            <p className="text-slate-300 text-xs font-medium truncate">{config.name || config.username}</p>
            <p className="text-slate-500 text-[11px] truncate">@{config.username}</p>
            <span className={`inline-block mt-1 text-[10px] font-semibold px-1.5 py-0.5 rounded ${
              config.role === 'admin'    ? 'bg-indigo-900/60 text-indigo-300' :
              config.role === 'operator' ? 'bg-amber-900/60 text-amber-300' :
                                           'bg-slate-800 text-slate-400'
            }`}>
              {ROLE_LABEL[config.role]}
            </span>
          </div>
        )}
        <button
          onClick={onSignOut}
          title={collapsed ? 'Sign out' : undefined}
          className={`w-full flex items-center py-2 rounded-md text-sm font-medium text-slate-400 hover:bg-slate-800 hover:text-slate-100 transition-all duration-100 ${collapsed ? 'justify-center px-2' : 'gap-3 px-3 text-left'}`}
        >
          <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15M12 9l-3 3m0 0l3 3m-3-3h12.75" />
          </svg>
          {!collapsed && 'Sign out'}
        </button>
      </div>
    </aside>
  );
}

function defaultPage(role: TenantRole): TenantPage {
  if (role === 'admin') return 'dashboard';
  if (role === 'operator') return 'dashboard';
  return 'jobs';
}

export function TenantApp({ config, onSignOut }: { config: TenantConfig; onSignOut: () => void }) {
  const [page, setPage] = useState<TenantPage>(() => defaultPage(config.role));
  // When a fleet member is clicked, jump to Agents with that agent's detail open,
  // so it's the exact same view (and actions) as the Agents page. We remember the
  // originating fleet so the agent modal can offer a "Back to fleet" link.
  const [focusAgentId, setFocusAgentId] = useState<string | null>(null);
  const [backFleetId, setBackFleetId] = useState<string | null>(null);
  const [focusFleetId, setFocusFleetId] = useState<string | null>(null);
  const openAgent = (id: string, fromFleetId?: string) => {
    setFocusAgentId(id); setBackFleetId(fromFleetId ?? null); setPage('agents');
  };
  const backToFleet = (fleetId: string) => {
    setFocusAgentId(null); setBackFleetId(null); setFocusFleetId(fleetId); setPage('fleets');
  };

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      <TenantSidebar config={config} page={page} onNavigate={setPage} onSignOut={onSignOut} />
      <main className="flex-1 overflow-y-auto">
        {page === 'dashboard'  && <DashboardPage        config={config} />}
        {page === 'users'      && <TenantUsersPage     config={config} />}
        {page === 'agents'     && <TenantAgentsPage    config={config} focusAgentId={focusAgentId} backFleetId={backFleetId} onBackToFleet={backToFleet} onFocusConsumed={() => setFocusAgentId(null)} />}
        {page === 'fleets'     && <FleetsPage          config={config} onOpenAgent={openAgent} focusFleetId={focusFleetId} onFocusFleetConsumed={() => setFocusFleetId(null)} />}
        {page === 'jobs'       && <TenantJobsPage      config={config} />}
        {page === 'approvals'  && <TenantApprovalsPage config={config} />}
        {page === 'api-tokens' && <TenantApiTokensPage config={config} />}
        {page === 'audit-logs' && (
          <AuditLogsPage mode="tenant" apiUrl={config.apiUrl} token={config.tenantToken} />
        )}
        {page === 'settings'   && <TenantSettingsPage config={config} />}
      </main>
    </div>
  );
}
