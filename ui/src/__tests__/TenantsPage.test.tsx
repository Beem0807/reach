import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TenantsPage } from '../pages/TenantsPage';
import type { Config, Tenant } from '../types';
import * as api from '../api';

const CONFIG: Config = { apiUrl: 'https://api.example.com', adminToken: 'admin_tok' };

const ACME: Tenant = {
  tenant_id: 'tenant_acme',
  name: 'Acme Corp',
  status: 'ACTIVE',
  created_at: '2026-01-01T00:00:00Z',
};
const DISABLED: Tenant = { ...ACME, tenant_id: 'tenant_old', name: 'Old Inc', status: 'DISABLED' };

function mockApis({ tenants = [ACME], users = 2, agents = 3 } = {}) {
  vi.spyOn(api, 'listTenants').mockResolvedValue({ tenants });
  vi.spyOn(api, 'listUsers').mockResolvedValue({ users: Array(users).fill({}) } as never);
  vi.spyOn(api, 'listAgentsAdmin').mockResolvedValue({ agents: Array(agents).fill({}) } as never);
}

const SETTINGS = {
  settings: { approval_retention_days: 30, job_retention_days: 30, run_retention_days: 30,
    audit_retention_days: 90, agent_history_retention_days: 30, fanout_cap: 50 },
  overrides: {},
  defaults: { approval_retention_days: 30, job_retention_days: 30, run_retention_days: 30,
    audit_retention_days: 90, agent_history_retention_days: 30, fanout_cap: 50 },
  bounds: { approval_retention_days: [1, 365], job_retention_days: [1, 365], run_retention_days: [1, 365],
    audit_retention_days: [1, 3650], agent_history_retention_days: [1, 365], fanout_cap: [1, 100] },
  wave_policy: {},
} as never;

beforeEach(() => { vi.restoreAllMocks(); });

describe('TenantsPage', () => {
  it('renders tenant cards with counts', async () => {
    mockApis({ tenants: [ACME], users: 2, agents: 3 });
    render(<TenantsPage config={CONFIG} />);
    expect(await screen.findByText('Acme Corp')).toBeInTheDocument();
    expect(await screen.findByText('3')).toBeInTheDocument(); // agents
    expect(screen.getByText('2')).toBeInTheDocument();        // users
  });

  it('shows empty state when there are no tenants', async () => {
    mockApis({ tenants: [] });
    render(<TenantsPage config={CONFIG} />);
    expect(await screen.findByText('No tenants yet')).toBeInTheDocument();
  });

  it('shows an error banner when loading fails', async () => {
    vi.spyOn(api, 'listTenants').mockRejectedValue(new Error('down'));
    render(<TenantsPage config={CONFIG} />);
    expect(await screen.findByText('Failed to load tenants')).toBeInTheDocument();
  });

  it('pages the tenant grid forward with Next', async () => {
    const other: Tenant = { ...ACME, tenant_id: 'tenant_glob', name: 'Globex' };
    const spy = vi.spyOn(api, 'listTenants').mockImplementation((_u, _t, params = {}) =>
      Promise.resolve({ tenants: [Number(params.offset) >= 20 ? other : ACME], total: 42 }));
    vi.spyOn(api, 'listUsers').mockResolvedValue({ users: [] } as never);
    vi.spyOn(api, 'listAgentsAdmin').mockResolvedValue({ agents: [] } as never);
    render(<TenantsPage config={CONFIG} />);
    await screen.findByText('Acme Corp');
    expect(screen.getByText(/Showing 1–20 of 42/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.adminToken, expect.objectContaining({ offset: '20' })));
    await screen.findByText('Globex');
  });

  it('searches tenants only when Search is clicked', async () => {
    const spy = vi.spyOn(api, 'listTenants').mockResolvedValue({ tenants: [ACME] });
    vi.spyOn(api, 'listUsers').mockResolvedValue({ users: [] } as never);
    vi.spyOn(api, 'listAgentsAdmin').mockResolvedValue({ agents: [] } as never);
    render(<TenantsPage config={CONFIG} />);
    await screen.findByText('Acme Corp');

    fireEvent.change(screen.getByPlaceholderText('Search tenants by name or ID…'), { target: { value: 'acme' } });
    expect(spy).not.toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.adminToken, expect.objectContaining({ q: 'acme' }));
    fireEvent.click(screen.getByText('Search'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.adminToken, expect.objectContaining({ q: 'acme' })));
  });

  it('creates a tenant and reloads', async () => {
    mockApis({ tenants: [ACME] });
    const createSpy = vi.spyOn(api, 'createTenant').mockResolvedValue({} as never);
    render(<TenantsPage config={CONFIG} />);
    await screen.findByText('Acme Corp');

    fireEvent.click(screen.getByRole('button', { name: /New tenant/ }));
    await userEvent.type(screen.getByPlaceholderText('Acme Corp'), 'Globex');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.adminToken, 'Globex'),
    );
  });

  it('requires typing the tenant id to confirm disable', async () => {
    mockApis({ tenants: [ACME] });
    const disableSpy = vi.spyOn(api, 'disableTenant').mockResolvedValue({} as never);
    render(<TenantsPage config={CONFIG} />);
    await screen.findByText('Acme Corp');

    fireEvent.click(screen.getByRole('button', { name: 'Disable' }));
    const confirmBtn = screen.getByRole('button', { name: 'Disable tenant' });
    expect(confirmBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('tenant_acme'), 'tenant_acme');
    expect(confirmBtn).toBeEnabled();
    fireEvent.click(confirmBtn);

    await waitFor(() =>
      expect(disableSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.adminToken, 'tenant_acme'),
    );
  });

  it('enables a disabled tenant', async () => {
    mockApis({ tenants: [DISABLED] });
    const enableSpy = vi.spyOn(api, 'enableTenant').mockResolvedValue({} as never);
    render(<TenantsPage config={CONFIG} />);
    await screen.findByText('Old Inc');

    fireEvent.click(screen.getByRole('button', { name: 'Enable' }));
    fireEvent.click(screen.getByRole('button', { name: 'Enable tenant' }));

    await waitFor(() =>
      expect(enableSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.adminToken, 'tenant_old'),
    );
  });

  it('opens the settings override modal from a tenant card and saves an override', async () => {
    mockApis({ tenants: [ACME] });
    const getSpy = vi.spyOn(api, 'adminGetTenantSettings').mockResolvedValue(SETTINGS);
    const putSpy = vi.spyOn(api, 'adminUpdateTenantSettings').mockResolvedValue(SETTINGS);
    render(<TenantsPage config={CONFIG} />);
    await screen.findByText('Acme Corp');

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await waitFor(() =>
      expect(getSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.adminToken, 'tenant_acme'));

    // Raise the fan-out cap above the tenant bound (100) - the override bypasses bounds.
    const capInput = await screen.findByLabelText('Fan-out cap');
    fireEvent.change(capInput, { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save override' }));

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.adminToken, 'tenant_acme',
        { fanout_cap: 200 }));
  });
});
