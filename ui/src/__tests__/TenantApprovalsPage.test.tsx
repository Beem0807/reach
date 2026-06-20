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

function renderOperator(approvals: Approval[] = [APPROVAL_1, APPROVAL_2]) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
  vi.spyOn(api, 'listAllTenantApprovals').mockResolvedValue({ approvals });
  return render(<TenantApprovalsPage config={CONFIG} />);
}

beforeEach(() => { vi.restoreAllMocks(); });

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
    expect(await screen.findByText(/No pending approvals/i)).toBeInTheDocument();
  });

  it('shows per-row Approve/Deny action buttons in pending tab', async () => {
    renderOperator();
    await screen.findByText('docker restart app');
    const approveButtons = screen.getAllByRole('button', { name: /^Approve$/ });
    const denyButtons    = screen.getAllByRole('button', { name: /^Deny$/ });
    expect(approveButtons.length).toBe(2);
    expect(denyButtons.length).toBe(2);
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
    await screen.findByText(/No pending/i);
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
});
