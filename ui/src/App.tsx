import { useState, useEffect, useRef, type ReactNode } from 'react';
import { useConfig } from './hooks/useConfig';
import { useTenantConfig } from './hooks/useTenantConfig';
import { setUnauthorizedHandler } from './api';
import { LoginPage } from './pages/LoginPage';
import { TenantLoginPage } from './pages/TenantLoginPage';
import { PasswordResetPage } from './pages/PasswordResetPage';
import { TenantsPage } from './pages/TenantsPage';
import { UsersPage } from './pages/UsersPage';
import { AuditLogsPage } from './pages/AuditLogsPage';
import { TenantApp } from './TenantApp';

type Page = 'tenants' | 'users' | 'audit-logs';

type NavItem = { id: Page; label: string; icon: ReactNode };

const ADMIN_NAV: NavItem[] = [
  {
    id: 'tenants',
    label: 'Tenants',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21" />
      </svg>
    ),
  },
  {
    id: 'users',
    label: 'Users',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
      </svg>
    ),
  },
  {
    id: 'audit-logs',
    label: 'Audit Logs',
    icon: (
      <svg className="w-[18px] h-[18px] shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V19.5a2.25 2.25 0 002.25 2.25h.75m0-3.375h3.75m-3.75 3.375h3.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
];

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

function Sidebar({
  page, onNavigate, onSignOut,
}: {
  page: Page;
  onNavigate: (p: Page) => void;
  onSignOut: () => void;
}) {
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
      <div className={`${collapsed ? 'px-2 py-4 flex-col gap-2' : 'px-5 py-5'} border-b border-slate-800/60 flex items-center`}>
        {collapsed ? (
          <div className="flex flex-col items-center gap-2 w-full">
            <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center">
              <span className="text-white text-xs font-bold tracking-tight">R</span>
            </div>
            <button onClick={toggle} title="Expand sidebar" className="text-slate-500 hover:text-slate-300 p-1 rounded-md hover:bg-slate-800 transition-colors">
              <ChevronIcon dir="right" />
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2.5 flex-1 min-w-0">
              <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center shrink-0">
                <span className="text-white text-xs font-bold tracking-tight">R</span>
              </div>
              <div>
                <p className="text-white font-semibold text-sm leading-none">reach</p>
                <p className="text-slate-500 text-[11px] mt-0.5">Console</p>
              </div>
            </div>
            <button onClick={toggle} title="Collapse sidebar" className="ml-2 text-slate-500 hover:text-slate-300 p-1 rounded-md hover:bg-slate-800 transition-colors shrink-0">
              <ChevronIcon dir="left" />
            </button>
          </>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 overflow-y-auto">
        <div className="space-y-0.5">
          {ADMIN_NAV.map(item => (
            <NavButton key={item.id} item={item} active={page === item.id} onClick={() => onNavigate(item.id)} collapsed={collapsed} />
          ))}
        </div>
      </nav>

      {/* Sign out */}
      <div className="px-2 py-3 border-t border-slate-800/60">
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

type ConsoleMode = 'chooser' | 'platform' | 'tenant';

function ConsoleChooser({ onPlatform, onTenant }: { onPlatform: () => void; onTenant: () => void }) {
  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-8">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500 flex items-center justify-center shadow-lg">
              <span className="text-white font-bold text-lg">R</span>
            </div>
            <div>
              <p className="text-white font-semibold text-xl leading-none">reach</p>
              <p className="text-slate-400 text-xs mt-0.5">Select a console</p>
            </div>
          </div>
        </div>
        <div className="space-y-3">
          <button
            onClick={onTenant}
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl px-6 py-4 text-left transition-colors shadow-md"
          >
            <p className="text-sm font-semibold">Tenant Console</p>
            <p className="text-indigo-200 text-xs mt-0.5">Sign in with your team credentials</p>
          </button>
          <button
            onClick={onPlatform}
            className="w-full bg-slate-800 hover:bg-slate-700 text-white font-semibold rounded-xl px-6 py-4 text-left transition-colors border border-slate-700"
          >
            <p className="text-sm font-semibold">Platform Admin</p>
            <p className="text-slate-400 text-xs mt-0.5">Sign in with admin password</p>
          </button>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const { config: platformConfig, setConfig: setPlatformConfig, clearConfig: clearPlatformConfig } = useConfig();
  const { config: tenantConfig, setConfig: setTenantConfig, updateConfig: updateTenantConfig, clearConfig: clearTenantConfig } = useTenantConfig();
  const tenantConfigRef = useRef(tenantConfig);
  useEffect(() => { tenantConfigRef.current = tenantConfig; }, [tenantConfig]);
  const [mode, setMode] = useState<ConsoleMode>(() => {
    if (tenantConfig) return 'tenant';
    if (platformConfig) return 'platform';
    return 'chooser';
  });
  const [page, setPage] = useState<Page>('tenants');

  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (tenantConfigRef.current) {
        clearTenantConfig();
      } else {
        clearPlatformConfig();
        setMode('chooser');
      }
    });
  }, [clearTenantConfig, clearPlatformConfig]);

  // --- Tenant flow ---
  if (mode === 'tenant' || tenantConfig) {
    if (!tenantConfig) {
      return (
        <TenantLoginPage
          onLogin={c => { setTenantConfig(c); setMode('tenant'); }}
          onSwitchToPlatform={() => setMode('platform')}
        />
      );
    }
    if (tenantConfig.mustResetPassword) {
      return (
        <PasswordResetPage
          config={tenantConfig}
          onComplete={() => updateTenantConfig({ mustResetPassword: false })}
        />
      );
    }
    return <TenantApp config={tenantConfig} onSignOut={clearTenantConfig} />;
  }

  // --- Platform admin flow ---
  if (mode === 'platform') {
    if (!platformConfig) {
      return (
        <LoginPage
          onLogin={c => { setPlatformConfig(c); setMode('platform'); }}
          onSwitchToTenant={() => setMode('tenant')}
        />
      );
    }
    return (
      <div className="flex h-screen bg-gray-50 overflow-hidden">
        <Sidebar page={page} onNavigate={setPage} onSignOut={() => { clearPlatformConfig(); setMode('chooser'); }} />
        <main className="flex-1 overflow-y-auto">
          {page === 'tenants'    && <TenantsPage   config={platformConfig} />}
          {page === 'users'      && <UsersPage     config={platformConfig} />}
          {page === 'audit-logs' && <AuditLogsPage mode="platform" apiUrl={platformConfig.apiUrl} token={platformConfig.adminToken} />}
        </main>
      </div>
    );
  }

  // --- Chooser (no token stored) ---
  return (
    <ConsoleChooser
      onPlatform={() => setMode('platform')}
      onTenant={() => setMode('tenant')}
    />
  );
}
