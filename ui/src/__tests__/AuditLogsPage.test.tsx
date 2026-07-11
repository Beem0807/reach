import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AuditLogsPage } from '../pages/AuditLogsPage';
import type { AuditLog } from '../types';
import * as api from '../api';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const LOG_LOGIN: AuditLog = {
  log_id: 'log_1',
  actor_name: 'alice',
  actor_role: 'admin',
  action: 'user.login',
  resource_type: 'user',
  resource_id: 'user_abc',
  ip_address: '1.2.3.4',
  created_at: '2024-01-15T10:00:00Z',
};

const LOG_TENANT: AuditLog = {
  log_id: 'log_2',
  actor_name: 'bob',
  action: 'tenant.created',
  resource_id: 'tenant_xyz',
  created_at: '2024-01-16T10:00:00Z',
};

function renderPlatform() {
  return render(<AuditLogsPage mode="platform" apiUrl="http://api" token="tok" />);
}

function renderTenant() {
  return render(<AuditLogsPage mode="tenant" apiUrl="http://api" token="tok" />);
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'listPlatformAuditLogs').mockResolvedValue({ logs: [LOG_LOGIN] });
  vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [LOG_LOGIN] });
  vi.spyOn(api, 'listTenants').mockResolvedValue({
    tenants: [{ tenant_id: 'tenant_acme', name: 'Acme Corp' }, { tenant_id: 'tenant_glob', name: 'Globex' }],
  });
});

// ---------------------------------------------------------------------------
// Initial load
// ---------------------------------------------------------------------------

describe('AuditLogsPage - initial load', () => {
  it('calls listPlatformAuditLogs on mount in platform mode', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    expect(api.listPlatformAuditLogs).toHaveBeenCalledWith(
      'http://api', 'tok', expect.objectContaining({ limit: '20' }),
    );
  });

  it('calls listTenantAuditLogs on mount in tenant mode', async () => {
    renderTenant();
    await waitFor(() => expect(api.listTenantAuditLogs).toHaveBeenCalledTimes(1));
    expect(api.listTenantAuditLogs).toHaveBeenCalledWith(
      'http://api', 'tok', expect.objectContaining({ limit: '20' }),
    );
  });

  it('does NOT call listTenantAuditLogs in platform mode', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    expect(api.listTenantAuditLogs).not.toHaveBeenCalled();
  });

  it('renders the action badge for each returned log', async () => {
    renderPlatform();
    // Wait for the log row to appear; actor name is unique (not in the action dropdown)
    await screen.findByText('alice');
    // Action badge also present - but multiple matches possible (badge + option), so use getAllByText
    expect(screen.getAllByText('user.login').length).toBeGreaterThanOrEqual(1);
  });

  it('renders actor name', async () => {
    renderPlatform();
    await screen.findByText('alice');
  });

  it('renders resource_id', async () => {
    renderPlatform();
    await screen.findByText('user_abc');
  });

  it('renders IP address', async () => {
    renderPlatform();
    await screen.findByText('1.2.3.4');
  });

  it('shows error message when API fails', async () => {
    vi.spyOn(api, 'listPlatformAuditLogs').mockRejectedValue(new Error('network'));
    renderPlatform();
    await screen.findByText(/failed to load audit logs/i);
  });

  it('initial request does not include filter params', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    const params = vi.mocked(api.listPlatformAuditLogs).mock.calls[0][2];
    expect(params).not.toHaveProperty('action');
    expect(params).not.toHaveProperty('actor');
    expect(params).not.toHaveProperty('resource');
    expect(params).not.toHaveProperty('ip');
  });
});

// ---------------------------------------------------------------------------
// Action dropdown
// ---------------------------------------------------------------------------

describe('AuditLogsPage - action dropdown', () => {
  it('renders a select element with "All actions" default option', async () => {
    renderPlatform();
    const sel = await screen.findByRole('combobox', { name: 'Filter by action' });
    expect(sel).toHaveValue('');
    expect(screen.getByRole('option', { name: 'All actions' })).toBeInTheDocument();
  });

  it('includes known action types as options', async () => {
    renderPlatform();
    const sel = await screen.findByRole('combobox', { name: 'Filter by action' });
    const values = Array.from(sel.querySelectorAll('option')).map(o => (o as HTMLOptionElement).value);
    expect(values).toContain('user.login');
    expect(values).toContain('tenant.created');
    expect(values).toContain('agent.mode_changed');
    expect(values).toContain('user.agents_changed');
    expect(values).toContain('api_token.revoked');
  });

  it('selecting an action does not reload until Search is clicked', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));

    fireEvent.change(await screen.findByRole('combobox', { name: 'Filter by action' }), { target: { value: 'user.login' } });
    expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1); // staged, not applied

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(2));
  });

  it('Search passes the selected action as a query param', async () => {
    renderPlatform();
    fireEvent.change(await screen.findByRole('combobox', { name: 'Filter by action' }), { target: { value: 'tenant.created' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() =>
      expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
        'http://api', 'tok', expect.objectContaining({ action: 'tenant.created' }),
      ),
    );
  });

  it('selecting "All actions" then Search omits action from params', async () => {
    renderPlatform();
    const sel = await screen.findByRole('combobox', { name: 'Filter by action' });
    fireEvent.change(sel, { target: { value: 'user.login' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(2));

    fireEvent.change(sel, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(3));

    const lastParams = vi.mocked(api.listPlatformAuditLogs).mock.calls[2][2];
    expect(lastParams).not.toHaveProperty('action');
  });
});

// ---------------------------------------------------------------------------
// Text filters - explicit Search (no auto-apply)
// ---------------------------------------------------------------------------

describe('AuditLogsPage - text filters (explicit Search)', () => {
  it('typing does not query until Search is clicked', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByPlaceholderText('Actor…'), { target: { value: 'alice' } });
    expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1); // still just the initial load

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
      'http://api', 'tok', expect.objectContaining({ actor: 'alice' }),
    ));
  });

  it('Enter in a text box triggers the search', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    const input = screen.getByPlaceholderText('Resource…');
    fireEvent.change(input, { target: { value: 'user_abc' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
      'http://api', 'tok', expect.objectContaining({ resource: 'user_abc' }),
    ));
  });

  it('IP filter is sent on Search', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText('IP…'), { target: { value: '10.0.0.1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
      'http://api', 'tok', expect.objectContaining({ ip: '10.0.0.1' }),
    ));
  });

  it('multiple filters are sent together on one Search', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText('Actor…'),    { target: { value: 'alice' } });
    fireEvent.change(screen.getByPlaceholderText('Resource…'), { target: { value: 'user_abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
      'http://api', 'tok', expect.objectContaining({ actor: 'alice', resource: 'user_abc' }),
    ));
  });

  it('tenant filter dropdown is sent (by tenant_id) on Search in platform mode', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    // Wait for the dropdown to populate from listTenants, then pick a tenant by id.
    await screen.findByText('Globex');
    fireEvent.change(screen.getByDisplayValue('All tenants'), { target: { value: 'tenant_glob' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
      'http://api', 'tok', expect.objectContaining({ tenant: 'tenant_glob' }),
    ));
  });

  it('does not render the tenant filter or load tenants in tenant mode', async () => {
    renderTenant();
    await waitFor(() => expect(api.listTenantAuditLogs).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('All tenants')).not.toBeInTheDocument();
    expect(api.listTenants).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Clear filters
// ---------------------------------------------------------------------------

describe('AuditLogsPage - clear filters', () => {
  it('"Clear filters" button is not shown initially', async () => {
    renderPlatform();
    await screen.findByText('user.login');
    expect(screen.queryByText('Clear filters')).not.toBeInTheDocument();
  });

  it('"Clear filters" appears when action is selected', async () => {
    renderPlatform();
    fireEvent.change(await screen.findByRole('combobox', { name: 'Filter by action' }), { target: { value: 'user.login' } });
    await screen.findByText('Clear filters');
  });

  it('"Clear filters" resets action dropdown to All actions', async () => {
    renderPlatform();
    const sel = await screen.findByRole('combobox', { name: 'Filter by action' });
    fireEvent.change(sel, { target: { value: 'user.login' } });
    await screen.findByText('Clear filters');
    fireEvent.click(screen.getByText('Clear filters'));
    expect(sel).toHaveValue('');
  });

  it('"Clear filters" triggers a reload without filter params', async () => {
    renderPlatform();
    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(1));
    fireEvent.change(await screen.findByRole('combobox', { name: 'Filter by action' }), { target: { value: 'user.login' } });

    fireEvent.click(screen.getByText('Clear filters'));

    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(2));
    const lastParams = vi.mocked(api.listPlatformAuditLogs).mock.calls[1][2];
    expect(lastParams).not.toHaveProperty('action');
    expect(lastParams).not.toHaveProperty('actor');
  });

  it('"Clear filters" button disappears after clearing', async () => {
    renderPlatform();
    fireEvent.change(await screen.findByRole('combobox', { name: 'Filter by action' }), { target: { value: 'user.login' } });
    await screen.findByText('Clear filters');
    fireEvent.click(screen.getByText('Clear filters'));
    await waitFor(() => expect(screen.queryByText('Clear filters')).not.toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// Pagination - Load more
// ---------------------------------------------------------------------------

describe('AuditLogsPage - load more', () => {
  it('shows "Load more" button when next_cursor returned', async () => {
    vi.spyOn(api, 'listPlatformAuditLogs').mockResolvedValueOnce({
      logs: [LOG_LOGIN],
      next_cursor: '2024-01-15T10:00:00Z',
    });
    renderPlatform();
    await screen.findByText('Load more');
  });

  it('does not show "Load more" when no next_cursor', async () => {
    renderPlatform();
    await screen.findByText('user.login');
    expect(screen.queryByText('Load more')).not.toBeInTheDocument();
  });

  it('clicking "Load more" calls API with cursor param', async () => {
    vi.spyOn(api, 'listPlatformAuditLogs')
      .mockResolvedValueOnce({ logs: [LOG_LOGIN], next_cursor: 'cursor_abc' })
      .mockResolvedValueOnce({ logs: [LOG_TENANT] });

    renderPlatform();
    fireEvent.click(await screen.findByText('Load more'));

    await waitFor(() => expect(api.listPlatformAuditLogs).toHaveBeenCalledTimes(2));
    expect(api.listPlatformAuditLogs).toHaveBeenLastCalledWith(
      'http://api', 'tok', expect.objectContaining({ cursor: 'cursor_abc' }),
    );
  });

  it('"Load more" appends new logs to existing ones', async () => {
    vi.spyOn(api, 'listPlatformAuditLogs')
      .mockResolvedValueOnce({ logs: [LOG_LOGIN],  next_cursor: 'cursor_abc' })
      .mockResolvedValueOnce({ logs: [LOG_TENANT] });

    renderPlatform();
    // Use actor name (not action text, which also appears as a dropdown option)
    await screen.findByText('alice');
    fireEvent.click(screen.getByText('Load more'));
    await screen.findByText('bob'); // LOG_TENANT actor
    expect(screen.getByText('alice')).toBeInTheDocument(); // LOG_LOGIN still rendered
  });

  it('"Load more" disappears after last page loaded', async () => {
    vi.spyOn(api, 'listPlatformAuditLogs')
      .mockResolvedValueOnce({ logs: [LOG_LOGIN], next_cursor: 'cursor_abc' })
      .mockResolvedValueOnce({ logs: [LOG_TENANT] }); // no next_cursor

    renderPlatform();
    await screen.findByText('Load more');
    fireEvent.click(screen.getByText('Load more'));
    await waitFor(() => expect(screen.queryByText('Load more')).not.toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// Approval detail rendering
// ---------------------------------------------------------------------------

describe('AuditLogsPage - approval detail rendering', () => {
  it('renders command, agent, and "permanent" for approval.approved', async () => {
    const log: AuditLog = {
      log_id: 'log_appr', actor_name: 'carol', actor_role: 'operator',
      action: 'approval.approved', resource_type: 'approval', resource_id: 'appr_1',
      created_at: '2024-01-17T10:00:00Z',
      metadata: { command: 'docker restart api', agent_id: 'agent_7', expires_at: null },
    };
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [log] });
    renderTenant();
    await screen.findByText('docker restart api');
    expect(screen.getByText(/agent_7/)).toBeInTheDocument();
    expect(screen.getByText(/permanent/)).toBeInTheDocument();
  });

  it('renders a command count for a bulk approval.pre_approved', async () => {
    const log: AuditLog = {
      log_id: 'log_bulk', actor_name: 'carol', action: 'approval.pre_approved',
      resource_type: 'approval', resource_id: 'agent_7', created_at: '2024-01-17T10:00:00Z',
      metadata: { commands: ['a', 'b', 'c'], agent_id: 'agent_7', count: 3 },
    };
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [log] });
    renderTenant();
    await screen.findByText(/3 commands/);
  });

  it('renders the prior status for approval.deleted', async () => {
    const log: AuditLog = {
      log_id: 'log_del', actor_name: 'carol', action: 'approval.deleted',
      resource_type: 'approval', resource_id: 'appr_2', created_at: '2024-01-17T10:00:00Z',
      metadata: { command: 'rm -rf /tmp/x', agent_id: 'agent_7', status: 'approved' },
    };
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [log] });
    renderTenant();
    await screen.findByText('rm -rf /tmp/x');
    expect(screen.getByText(/was approved/)).toBeInTheDocument();
  });

  it('renders a fleet run.dispatched with command, target and fan-out width', async () => {
    const log: AuditLog = {
      log_id: 'log_run', actor_name: 'dave', action: 'run.dispatched',
      resource_type: 'run', resource_id: 'run_1', created_at: '2024-01-17T10:00:00Z',
      metadata: { scope: 'fleet', fleet_name: 'web-tier', command: 'systemctl restart nginx',
        dispatched: 4, wave_total: 2, is_write: true },
    };
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [log] });
    renderTenant();
    await screen.findByText('systemctl restart nginx');
    expect(screen.getByText('web-tier')).toBeInTheDocument();
    expect(screen.getByText(/4 agents/)).toBeInTheDocument();
    expect(screen.getByText(/2 waves/)).toBeInTheDocument();
    expect(screen.getByText('write')).toBeInTheDocument();
  });

  it('renders a job.dispatched with command and target host', async () => {
    const log: AuditLog = {
      log_id: 'log_job', actor_name: 'dan', action: 'job.dispatched',
      resource_type: 'job', resource_id: 'job_1', created_at: '2024-01-17T10:00:00Z',
      metadata: { agent_id: 'agent_1', hostname: 'web-01', command: 'systemctl restart nginx', mode: 'wild', is_write: true },
    };
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [log] });
    renderTenant();
    await screen.findByText('systemctl restart nginx');
    expect(screen.getByText('web-01')).toBeInTheDocument();
    expect(screen.getByText('write')).toBeInTheDocument();
  });

  it('renders a generic metadata summary for actions without a bespoke renderer', async () => {
    const log: AuditLog = {
      log_id: 'log_set', actor_name: 'erin', action: 'tenant.settings_updated',
      resource_type: 'tenant', resource_id: 'tenant_1', created_at: '2024-01-17T10:00:00Z',
      metadata: { keys: ['fanout_cap', 'wave_policy'] },
    };
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [log] });
    renderTenant();
    await screen.findByText('fanout_cap, wave_policy');
  });
});

describe('AuditLogsPage - tenant column', () => {
  const LOG_WITH_TENANT: AuditLog = {
    log_id: 'log_t', actor_name: 'sam', action: 'run.dispatched',
    tenant_id: 'tenant_acme', resource_type: 'run', resource_id: 'run_9',
    created_at: '2024-01-18T10:00:00Z', metadata: { scope: 'tag', tag: 'env:prod', command: 'uptime' },
  };

  it('shows a Tenant column with the tenant_id in platform mode', async () => {
    vi.spyOn(api, 'listPlatformAuditLogs').mockResolvedValue({ logs: [LOG_WITH_TENANT] });
    renderPlatform();
    expect(await screen.findByText('Tenant')).toBeInTheDocument();
    expect(screen.getByText('tenant_acme')).toBeInTheDocument();
  });

  it('does not show the Tenant column in tenant mode', async () => {
    vi.spyOn(api, 'listTenantAuditLogs').mockResolvedValue({ logs: [LOG_WITH_TENANT] });
    renderTenant();
    await screen.findByText('uptime');
    expect(screen.queryByText('Tenant')).not.toBeInTheDocument();
  });
});
