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
});
