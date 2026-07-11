import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { TenantApprovalsPage } from '../pages/TenantApprovalsPage';
import type { Approval, TenantConfig } from '../types';
import * as api from '../api';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok_test',
  tenantId: 'tenant_1',
  tenantName: 'acme',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice',
  role: 'admin',
  mustResetPassword: false,
};

const DEV_CONFIG: TenantConfig = { ...CONFIG, role: 'developer' };

const APPROVAL_1: Approval = {
  approval_id: 'appr_1',
  agent_id: 'agent_abc',
  agent_hostname: 'myhost.local',
  tenant_id: 'tenant_1',
  command: 'docker restart app',
  status: 'pending',
  created_at: '2026-06-01T10:00:00Z',
};

const APPROVAL_2: Approval = {
  approval_id: 'appr_2',
  agent_id: 'agent_abc',
  agent_hostname: 'myhost.local',
  tenant_id: 'tenant_1',
  command: 'systemctl restart nginx',
  status: 'pending',
  created_at: '2026-06-01T11:00:00Z',
};

const K8S_APPROVAL: Approval = {
  approval_id: 'appr_k1',
  agent_id: 'agent_k8s',
  agent_hostname: 'cluster-1',
  tenant_id: 'tenant_1',
  command: 'kubectl delete pods -n team-a',
  k8s_rule: { verb: 'delete', resource: 'pods', namespace: 'team-a', name: '*' },
  status: 'pending',
  created_at: '2026-06-01T12:00:00Z',
};

function renderOperator(approvals: Approval[] = [APPROVAL_1, APPROVAL_2]) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
  vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
  vi.spyOn(api, 'listAllTenantApprovals').mockResolvedValue({ approvals });
  return render(<TenantApprovalsPage config={CONFIG} />);
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// Kubernetes/Host toggle - one kind at a time
// ---------------------------------------------------------------------------

// Server-side behavior: mock the API to honor status/type/q/limit/offset like
// the backend does, so we exercise the real request-driven UI.
function renderServer(all: Approval[]) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
  vi.spyOn(api, 'listAllTenantApprovals').mockImplementation((_u, _t, params: Record<string, string> = {}) => {
    let rows = all;
    if (params.status) rows = rows.filter(a => (a.status ?? 'pending') === params.status);
    if (params.type) rows = rows.filter(a => (a.k8s_rule ? 'k8s' : 'host') === params.type);
    if (params.q) {
      const q = params.q.toLowerCase();
      rows = rows.filter(a =>
        (a.command ?? '').toLowerCase().includes(q) ||
        (a.k8s_rule ? Object.values(a.k8s_rule).join(' ').toLowerCase().includes(q) : false));
    }
    const total = rows.length;
    const offset = parseInt(params.offset ?? '0', 10);
    const limit = parseInt(params.limit ?? '20', 10);
    return Promise.resolve({ approvals: rows.slice(offset, offset + limit), total });
  });
  return render(<TenantApprovalsPage config={CONFIG} />);
}

describe('host/k8s separation (server-driven)', () => {
  it('defaults to Host and hides k8s rules until toggled', async () => {
    renderServer([APPROVAL_1, K8S_APPROVAL]);
    expect(await screen.findByText('docker restart app')).toBeInTheDocument();
    expect(screen.queryByText('team-a')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Kubernetes' }));
    expect(await screen.findByText('team-a')).toBeInTheDocument();
    expect(screen.queryByText('docker restart app')).not.toBeInTheDocument();
  });

  it('paginates each kind independently (10 per page)', async () => {
    const hosts = Array.from({ length: 12 }, (_, i) => ({
      ...APPROVAL_1, approval_id: `h_${i}`, command: `host-cmd-${i}`,
    }));
    const k8ss = Array.from({ length: 12 }, (_, i) => ({
      ...K8S_APPROVAL, approval_id: `k_${i}`,
      k8s_rule: { verb: 'delete', resource: 'pods', namespace: `ns-${i}`, name: '*' },
    }));
    renderServer([...hosts, ...k8ss]);

    expect(await screen.findByText('host-cmd-0')).toBeInTheDocument();
    expect(screen.queryByText('host-cmd-10')).not.toBeInTheDocument();
    expect(screen.getByText(/Showing 1–10 of 12/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('host-cmd-10')).toBeInTheDocument();
    expect(screen.getByText(/Showing 11–12 of 12/)).toBeInTheDocument();

    // Kubernetes keeps its own page 1, independent of host's page 2.
    fireEvent.click(screen.getByRole('button', { name: 'Kubernetes' }));
    expect(await screen.findByText('ns-0')).toBeInTheDocument();
    expect(screen.getByText(/Showing 1–10 of 12/)).toBeInTheDocument();
  });

  it('default view is the recent page; Search fetches matches across all pages', async () => {
    const hosts = Array.from({ length: 12 }, (_, i) => ({
      ...APPROVAL_1, approval_id: `h_${i}`, command: `host-cmd-${i}`,
    }));
    renderServer(hosts);
    await screen.findByText('host-cmd-0');
    expect(screen.queryByText('host-cmd-11')).not.toBeInTheDocument(); // page 2

    // Type, then click Search (not live) - matches surface regardless of page.
    fireEvent.change(screen.getByPlaceholderText(/Search command/i), { target: { value: 'cmd-11' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('host-cmd-11')).toBeInTheDocument();
    expect(screen.queryByText('host-cmd-0')).not.toBeInTheDocument();
  });

  it('k8s Search matches on rule fields like namespace', async () => {
    const k8ss = Array.from({ length: 3 }, (_, i) => ({
      ...K8S_APPROVAL, approval_id: `k_${i}`,
      k8s_rule: { verb: 'delete', resource: 'pods', namespace: `ns-${i}`, name: '*' },
    }));
    renderServer(k8ss);
    fireEvent.click(await screen.findByRole('button', { name: 'Kubernetes' }));
    fireEvent.change(screen.getByPlaceholderText(/Search verb, resource/i), { target: { value: 'ns-2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('ns-2')).toBeInTheDocument();
    expect(screen.queryByText('ns-0')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe('operator view rendering', () => {
  it('shows pending commands after load', async () => {
    renderOperator();
    expect(await screen.findByText('docker restart app')).toBeInTheDocument();
    expect(screen.getByText('systemctl restart nginx')).toBeInTheDocument();
  });

  it('shows empty state when no approvals', async () => {
    renderOperator([]);
    expect(await screen.findByText(/No host pending approvals/i)).toBeInTheDocument();
  });

  it('shows per-row Approve/Deny action buttons in pending tab', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const approveButtons = screen.getAllByRole('button', { name: /^Approve$/ });
    const denyButtons    = screen.getAllByRole('button', { name: /^Deny$/ });
    expect(approveButtons.length).toBe(2);
    expect(denyButtons.length).toBe(2);
  });

  it('switches to fleet scope: queries with scope=fleet and shows the fleet picker', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listFleets').mockResolvedValue({
      fleets: [{ fleet_id: 'fleet_a', name: 'web-asg', tenant_id: 'tenant_1', status: 'ACTIVE', mode: 'approved', created_at: '2026-06-01T00:00:00Z' } as unknown as import('../types').Fleet],
      default_reap_after_seconds: 1800,
    });
    const listSpy = vi.spyOn(api, 'listAllTenantApprovals').mockResolvedValue({ approvals: [] });
    render(<TenantApprovalsPage config={CONFIG} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Fleets' }));

    await waitFor(() => {
      expect(listSpy.mock.calls.some(c => (c[2] as Record<string, string>)?.scope === 'fleet')).toBe(true);
    });
    expect(await screen.findByText('All fleets')).toBeInTheDocument();
  });

  it('renders a fleet-scoped approval with the fleet name and a fleet badge', async () => {
    const fleetApproval: Approval = {
      approval_id: 'appr_f1',
      agent_id: null,
      fleet_id: 'fleet_a',
      fleet_name: 'web-asg',
      scope: 'fleet',
      tenant_id: 'tenant_1',
      command: 'docker restart app',
      status: 'pending',
      created_at: '2026-06-01T10:00:00Z',
    };
    renderOperator([fleetApproval]);
    expect(await screen.findByText('web-asg')).toBeInTheDocument();
    expect(screen.getByText('fleet')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Checkboxes
// ---------------------------------------------------------------------------

describe('per-row checkboxes in pending tab', () => {
  it('shows a checkbox per row plus a select-all checkbox', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const checkboxes = screen.getAllByRole('checkbox');
    // 1 select-all + 2 row checkboxes
    expect(checkboxes).toHaveLength(3);
  });

  it('all checkboxes start unchecked', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const checkboxes = screen.getAllByRole('checkbox');
    checkboxes.forEach(cb => expect((cb as HTMLInputElement).checked).toBe(false));
  });

  it('checking a row checkbox shows Approve (1) and Deny (1) buttons', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const [, firstRowCb] = screen.getAllByRole('checkbox');
    fireEvent.click(firstRowCb);
    expect(screen.getByRole('button', { name: /Approve \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Deny \(1\)/ })).toBeInTheDocument();
  });

  it('checking two rows shows Approve (2) and Deny (2) buttons', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const [, firstCb, secondCb] = screen.getAllByRole('checkbox');
    fireEvent.click(firstCb);
    fireEvent.click(secondCb);
    expect(screen.getByRole('button', { name: /Approve \(2\)/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Deny \(2\)/ })).toBeInTheDocument();
  });

  it('unchecking the last selected row hides bulk buttons', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const [, firstCb] = screen.getAllByRole('checkbox');
    fireEvent.click(firstCb);
    expect(screen.getByRole('button', { name: /Approve \(1\)/ })).toBeInTheDocument();
    fireEvent.click(firstCb);
    // Per-row "Approve" buttons remain (opacity-0); only the bulk "(N)" button disappears
    expect(screen.queryByRole('button', { name: /Approve \(/ })).not.toBeInTheDocument();
  });

  it('select-all checkbox selects all rows', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const [selectAll] = screen.getAllByRole('checkbox');
    fireEvent.click(selectAll);
    expect(screen.getByRole('button', { name: /Approve \(2\)/ })).toBeInTheDocument();
  });

  it('select-all when all selected deselects all', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const [selectAll] = screen.getAllByRole('checkbox');
    fireEvent.click(selectAll);
    expect(screen.getByRole('button', { name: /Approve \(2\)/ })).toBeInTheDocument();
    fireEvent.click(selectAll);
    expect(screen.queryByRole('button', { name: /Approve \(/ })).not.toBeInTheDocument();
  });

  it('no checkboxes shown in approved tab', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listAllTenantApprovals').mockImplementation((_u, _t, params) =>
      Promise.resolve({ approvals: params?.status === 'approved' ? [{ ...APPROVAL_1, status: 'approved' as const }] : [] }),
    );
    render(<TenantApprovalsPage config={CONFIG} />);
    await screen.findByText(/No host pending/i);
    fireEvent.click(screen.getByRole('button', { name: /^approved$/i }));
    await screen.findByText('docker restart app');
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('selection clears after switching tabs', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listAllTenantApprovals').mockResolvedValue({ approvals: [APPROVAL_1] });
    render(<TenantApprovalsPage config={CONFIG} />);
    await screen.findByText('docker restart app');
    const [, rowCb] = screen.getAllByRole('checkbox');
    fireEvent.click(rowCb);
    expect(screen.getByRole('button', { name: /Approve \(1\)/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^approved$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^pending$/i }));
    await screen.findByText('docker restart app');
    expect(screen.queryByRole('button', { name: /Approve \(/ })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Bulk confirm modal
// ---------------------------------------------------------------------------

describe('bulk confirm modal', () => {
  it('clicking Approve (1) opens confirmation modal', async () => {
    renderOperator([APPROVAL_1]);
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByRole('button', { name: /Approve \(1\)/ }));
    expect(screen.getByText(/Approve 1 selected request\?/i)).toBeInTheDocument();
  });

  it('clicking Deny (1) opens confirmation modal', async () => {
    renderOperator([APPROVAL_1]);
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByRole('button', { name: /Deny \(1\)/ }));
    expect(screen.getByText(/Deny 1 selected request\?/i)).toBeInTheDocument();
  });

  it('confirmation modal uses plural for multiple items', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[0]); // select all
    fireEvent.click(screen.getByRole('button', { name: /Approve \(2\)/ }));
    expect(screen.getByText(/Approve 2 selected requests\?/i)).toBeInTheDocument();
  });

  it('cancel in bulk modal does not call API', async () => {
    const approveSpy = vi.spyOn(api, 'approveTenantApproval').mockResolvedValue({} as never);
    renderOperator([APPROVAL_1]);
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByRole('button', { name: /Approve \(1\)/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(approveSpy).not.toHaveBeenCalled();
    expect(screen.queryByText(/Approve 1 selected/i)).not.toBeInTheDocument();
  });

  it('confirming bulk approve calls approveTenantApproval for each selected ID', async () => {
    const approveSpy = vi.spyOn(api, 'approveTenantApproval').mockResolvedValue({} as never);
    renderOperator();
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[0]); // select all
    fireEvent.click(screen.getByRole('button', { name: /Approve \(2\)/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Approve 2$/ }));
    await waitFor(() => expect(approveSpy).toHaveBeenCalledTimes(2));
    expect(approveSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'appr_1');
    expect(approveSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'appr_2');
  });

  it('confirming bulk deny calls denyTenantApproval for each selected ID', async () => {
    const denySpy = vi.spyOn(api, 'denyTenantApproval').mockResolvedValue({} as never);
    renderOperator();
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[0]); // select all
    fireEvent.click(screen.getByRole('button', { name: /Deny \(2\)/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Deny 2$/ }));
    await waitFor(() => expect(denySpy).toHaveBeenCalledTimes(2));
    expect(denySpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'appr_1');
    expect(denySpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'appr_2');
  });

  it('selection is cleared after confirming bulk approve', async () => {
    vi.spyOn(api, 'approveTenantApproval').mockResolvedValue({} as never);
    renderOperator([APPROVAL_1]);
    await screen.findByText('docker restart app');
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByRole('button', { name: /Approve \(1\)/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Approve 1$/ }));
    await waitFor(() => expect(screen.queryByRole('button', { name: /Approve \(/ })).not.toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// Individual row approve/deny still works
// ---------------------------------------------------------------------------

describe('individual row approve / deny', () => {
  it('clicking per-row Approve button opens the approve modal', async () => {
    renderOperator([APPROVAL_1]);
    await screen.findByText('docker restart app');
    const approveBtn = within(document.querySelector('table')!).getAllByRole('button', { name: /^Approve$/ })[0];
    fireEvent.click(approveBtn);
    expect(await screen.findByRole('heading', { name: /Approve command/i })).toBeInTheDocument();
  });

  it('clicking per-row Deny button opens the deny modal', async () => {
    renderOperator([APPROVAL_1]);
    await screen.findByText('docker restart app');
    const denyBtn = within(document.querySelector('table')!).getAllByRole('button', { name: /^Deny$/ })[0];
    fireEvent.click(denyBtn);
    expect(await screen.findByRole('heading', { name: /Deny request/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Developer view
// ---------------------------------------------------------------------------

describe('developer view', () => {
  it('shows no checkboxes in developer view', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listTenantApprovals').mockResolvedValue({ approvals: [APPROVAL_1] });
    render(<TenantApprovalsPage config={DEV_CONFIG} />);
    await screen.findByText('docker restart app');
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('shows Request approval button for developers', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listTenantApprovals').mockResolvedValue({ approvals: [] });
    render(<TenantApprovalsPage config={DEV_CONFIG} />);
    expect(await screen.findByRole('button', { name: /Request approval/i })).toBeInTheDocument();
  });

  // Server-driven: mock /approvals/pending to honor type/q/limit/offset.
  function renderDevServer(all: Approval[]) {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listTenantApprovals').mockImplementation((_u, _t, params: Record<string, string> = {}) => {
      let rows = all;
      if (params.type) rows = rows.filter(a => (a.k8s_rule ? 'k8s' : 'host') === params.type);
      if (params.q) {
        const q = params.q.toLowerCase();
        rows = rows.filter(a =>
          (a.command ?? '').toLowerCase().includes(q) ||
          (a.k8s_rule ? Object.values(a.k8s_rule).join(' ').toLowerCase().includes(q) : false));
      }
      const total = rows.length;
      const offset = parseInt(params.offset ?? '0', 10);
      const limit = parseInt(params.limit ?? '20', 10);
      return Promise.resolve({ approvals: rows.slice(offset, offset + limit), total });
    });
    return render(<TenantApprovalsPage config={DEV_CONFIG} />);
  }

  it('developer view separates host/k8s server-side via the toggle', async () => {
    renderDevServer([APPROVAL_1, K8S_APPROVAL]);
    expect(await screen.findByText('docker restart app')).toBeInTheDocument();
    expect(screen.queryByText('team-a')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Kubernetes' }));
    expect(await screen.findByText('team-a')).toBeInTheDocument();
    expect(screen.queryByText('docker restart app')).not.toBeInTheDocument();
  });

  it('developer Search fetches matches across all pages', async () => {
    const hosts = Array.from({ length: 12 }, (_, i) => ({
      ...APPROVAL_1, approval_id: `h_${i}`, command: `host-cmd-${i}`,
    }));
    renderDevServer(hosts);
    await screen.findByText('host-cmd-0');
    expect(screen.queryByText('host-cmd-11')).not.toBeInTheDocument(); // page 2
    fireEvent.change(screen.getByPlaceholderText(/Search command/i), { target: { value: 'cmd-11' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('host-cmd-11')).toBeInTheDocument();
    expect(screen.queryByText('host-cmd-0')).not.toBeInTheDocument();
  });
});
