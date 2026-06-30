import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DashboardPage } from '../pages/DashboardPage';
import type { Agent, Approval, AuditLog, TenantConfig } from '../types';
import * as api from '../api';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok_test',
  tenantId: 'tenant_1',
  tenantName: 'Acme Corp',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice',
  role: 'admin',
  mustResetPassword: false,
};

const BASE_AGENT: Agent = {
  agent_id: 'agent_1',
  tenant_id: 'tenant_1',
  status: 'ACTIVE',
  hostname: 'web-01.local',
  mode: 'wild',
  access_level: 'open',
  tags: [],
  grant_docker: false,
  grant_service_mgmt: false,
  last_heartbeat_at: new Date().toISOString(),
};

const INACTIVE_AGENT: Agent = { ...BASE_AGENT, agent_id: 'agent_2', hostname: 'db-01.local', status: 'INACTIVE' };
const REVOKED_AGENT:  Agent = { ...BASE_AGENT, agent_id: 'agent_3', hostname: 'old.local', status: 'REVOKED' };

const PENDING: Approval = {
  approval_id: 'appr_1',
  agent_id: 'agent_1',
  agent_hostname: 'web-01.local',
  tenant_id: 'tenant_1',
  command: 'docker restart app',
  status: 'pending',
  created_at: '2026-06-01T10:00:00Z',
};

const LOG: AuditLog = {
  log_id: 'log_1',
  actor_name: 'alice',
  actor_role: 'admin',
  action: 'user.login',
  resource_type: 'user',
  resource_id: 'user_abc',
  created_at: new Date().toISOString(),
};

function mockApis({
  agents = [BASE_AGENT],
  pending = [] as Approval[],
  logs = [] as AuditLog[],
} = {}) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents });
  vi.spyOn(api, 'listAllTenantApprovals').mockResolvedValue({ approvals: pending });
  vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs });
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe('loading state', () => {
  it('shows spinner before data loads', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listAllTenantApprovals').mockResolvedValue({ approvals: [] });
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [] });
    render(<DashboardPage config={CONFIG} />);
    expect(document.querySelector('[class*="animate"]')).toBeInTheDocument();
    // Flush the pending fetch so its state updates settle inside act(); otherwise
    // they fire after the test ends and React warns about updates outside act().
    await screen.findByText('Active agents');
  });
});

// ---------------------------------------------------------------------------
// Stat cards
// ---------------------------------------------------------------------------

describe('stat cards', () => {
  it('shows active agent count', async () => {
    mockApis({ agents: [BASE_AGENT, INACTIVE_AGENT] });
    render(<DashboardPage config={CONFIG} />);
    const heading = await screen.findByText('Active agents');
    const card = heading.closest('div')!.parentElement!;
    expect(card).toHaveTextContent('1');
  });

  it('shows pending approvals count', async () => {
    mockApis({ pending: [PENDING] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Active agents');
    // Sub text is unique: "1 needs review"
    expect(screen.getByText('1 needs review')).toBeInTheDocument();
  });

  it('shows 0 pending approvals when queue is empty', async () => {
    mockApis({ pending: [] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Active agents');
    // "Pending approvals" appears twice (stat card + panel), grab stat card label + check sibling value
    const [statLabel] = screen.getAllByText('Pending approvals');
    expect(statLabel.nextElementSibling?.textContent).toBe('0');
  });

  it('counts an offline agent under Needs attention', async () => {
    mockApis({ agents: [INACTIVE_AGENT] });
    render(<DashboardPage config={CONFIG} />);
    const heading = await screen.findByText('Needs attention');
    const card = heading.closest('div')!.parentElement!;
    expect(card).toHaveTextContent('1');
    expect(card).toHaveTextContent(/offline/i);
  });

  it('shows "All agents healthy" when nothing needs attention', async () => {
    mockApis({ agents: [BASE_AGENT] });
    render(<DashboardPage config={CONFIG} />);
    const heading = await screen.findByText('Needs attention');
    const card = heading.closest('div')!.parentElement!;
    expect(card).toHaveTextContent('0');
    expect(card).toHaveTextContent(/All agents healthy/i);
  });

  it('shows recent events count', async () => {
    mockApis({ logs: [LOG, { ...LOG, log_id: 'log_2' }] });
    render(<DashboardPage config={CONFIG} />);
    const heading = await screen.findByText('Events (1h)');
    const card = heading.closest('div')!.parentElement!;
    expect(card).toHaveTextContent('2');
  });

  it('shows tenant name in header', async () => {
    mockApis();
    render(<DashboardPage config={CONFIG} />);
    expect(await screen.findByText('Acme Corp')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Agent health bar
// ---------------------------------------------------------------------------

describe('agent health bar', () => {
  it('renders health bar when agents exist', async () => {
    mockApis({ agents: [BASE_AGENT, INACTIVE_AGENT, REVOKED_AGENT] });
    render(<DashboardPage config={CONFIG} />);
    expect(await screen.findByText('Agent health')).toBeInTheDocument();
  });

  it('shows correct breakdown labels', async () => {
    mockApis({ agents: [BASE_AGENT, INACTIVE_AGENT, REVOKED_AGENT] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Agent health');
    expect(screen.getByText('1 active')).toBeInTheDocument();
    expect(screen.getByText('1 inactive')).toBeInTheDocument();
    expect(screen.getByText('1 revoked')).toBeInTheDocument();
  });

  it('shows total count in health bar', async () => {
    mockApis({ agents: [BASE_AGENT, INACTIVE_AGENT] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Agent health');
    expect(screen.getByText('2 total')).toBeInTheDocument();
  });

  it('hides health bar when no agents', async () => {
    mockApis({ agents: [] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Active agents');
    expect(screen.queryByText('Agent health')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Pending approvals panel
// ---------------------------------------------------------------------------

describe('pending approvals panel', () => {
  it('shows empty state text when no pending', async () => {
    mockApis({ pending: [] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Active agents');
    // Panel empty state has unique sub-text
    expect(screen.getByText('No pending approval requests')).toBeInTheDocument();
  });

  it('shows command in pending list', async () => {
    mockApis({ pending: [PENDING] });
    render(<DashboardPage config={CONFIG} />);
    expect(await screen.findByText('docker restart app')).toBeInTheDocument();
  });

  it('shows agent hostname for pending approval', async () => {
    mockApis({ pending: [PENDING] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('docker restart app');
    expect(screen.getByText('web-01.local')).toBeInTheDocument();
  });

  it('shows "+N more" when more than 6 pending', async () => {
    const many = Array.from({ length: 8 }, (_, i) => ({
      ...PENDING,
      approval_id: `appr_${i}`,
      command: `cmd_${i}`,
    }));
    mockApis({ pending: many });
    render(<DashboardPage config={CONFIG} />);
    expect(await screen.findByText(/\+2 more pending/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Recent activity timeline
// ---------------------------------------------------------------------------

describe('recent activity timeline', () => {
  it('shows "No recent audit events" when empty', async () => {
    mockApis({ logs: [] });
    render(<DashboardPage config={CONFIG} />);
    expect(await screen.findByText('No recent audit events')).toBeInTheDocument();
  });

  it('shows actor name for log entry', async () => {
    mockApis({ logs: [LOG] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Recent activity');
    expect(screen.getByText('alice')).toBeInTheDocument();
  });

  it('shows formatted action label for known action type', async () => {
    mockApis({ logs: [LOG] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Recent activity');
    expect(screen.getByText('login')).toBeInTheDocument();
  });

  it('shows resource_id for log entry', async () => {
    mockApis({ logs: [LOG] });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('Recent activity');
    expect(screen.getByText('user_abc')).toBeInTheDocument();
  });

  it('shows multiple log entries', async () => {
    const logs: AuditLog[] = [
      LOG,
      { ...LOG, log_id: 'log_2', action: 'user.created', actor_name: 'bob', resource_id: 'user_new' },
    ];
    mockApis({ logs });
    render(<DashboardPage config={CONFIG} />);
    await screen.findByText('alice');
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.getByText('user created')).toBeInTheDocument();
  });
});
