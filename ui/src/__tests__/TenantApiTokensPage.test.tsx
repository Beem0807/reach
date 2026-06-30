import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TenantApiTokensPage } from '../pages/TenantApiTokensPage';
import type { ApiToken } from '../types';
import * as api from '../api';

const CONFIG = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok_test',
  tenantId: 'tenant_1',
  tenantName: 'acme',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice',
  role: 'admin' as const,
  mustResetPassword: false,
};

const TOKENS: ApiToken[] = [
  {
    token_id: 'tkid_1',
    name: 'My laptop',
    status: 'ACTIVE',
    created_at: '2026-06-01T10:00:00Z',
    last_used_at: undefined,
    revoked_at: undefined,
  },
  {
    token_id: 'tkid_2',
    name: 'CI runner',
    status: 'ACTIVE',
    created_at: '2026-06-02T10:00:00Z',
    last_used_at: '2026-06-20T10:00:00Z',
    revoked_at: undefined,
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'listApiTokens').mockResolvedValue({ tokens: TOKENS });
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

describe('TenantApiTokensPage rendering', () => {
  it('shows "Description" as column header (not "Name")', async () => {
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');
    // Column header accessible name includes the sort icon (⇅); use partial match
    expect(screen.getByRole('columnheader', { name: /^Description/i })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /^Name/i })).not.toBeInTheDocument();
  });

  it('shows token names after load', async () => {
    render(<TenantApiTokensPage config={CONFIG} />);
    expect(await screen.findByText('My laptop')).toBeInTheDocument();
    expect(screen.getByText('CI runner')).toBeInTheDocument();
  });

  it('shows revoked tokens with a Revoked status', async () => {
    vi.spyOn(api, 'listApiTokens').mockResolvedValue({
      tokens: [...TOKENS, { ...TOKENS[0], token_id: 'tkid_3', name: 'Old key', status: 'REVOKED' }],
    });
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');
    expect(screen.getByText('Old key')).toBeInTheDocument();
    expect(screen.getAllByText(/revoked/i).length).toBeGreaterThan(0);
  });

  it('shows active count badge', async () => {
    render(<TenantApiTokensPage config={CONFIG} />);
    expect(await screen.findByText('2 active')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Rename flow
// ---------------------------------------------------------------------------

describe('rename token', () => {
  it('shows inline input when pencil is clicked', async () => {
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');

    fireEvent.mouseOver(screen.getAllByText('My laptop')[0].closest('tr')!);
    const pencils = document.querySelectorAll('button[title="Rename"]');
    fireEvent.click(pencils[0]);

    expect(screen.getByDisplayValue('My laptop')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('calls renameApiToken and updates name on save', async () => {
    vi.spyOn(api, 'renameApiToken').mockResolvedValue({ token_id: 'tkid_1', name: 'Work laptop' });
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');

    fireEvent.click(document.querySelectorAll('button[title="Rename"]')[0]);
    const input = screen.getByDisplayValue('My laptop');
    fireEvent.change(input, { target: { value: 'Work laptop' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.renameApiToken).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, 'tkid_1', 'Work laptop'
    ));
    expect(await screen.findByText('Work laptop')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('Work laptop')).not.toBeInTheDocument();
  });

  it('saves on Enter key', async () => {
    vi.spyOn(api, 'renameApiToken').mockResolvedValue({ token_id: 'tkid_1', name: 'New name' });
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');

    fireEvent.click(document.querySelectorAll('button[title="Rename"]')[0]);
    const input = screen.getByDisplayValue('My laptop');
    fireEvent.change(input, { target: { value: 'New name' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.renameApiToken).toHaveBeenCalled());
  });

  it('cancels on Escape key without calling API', async () => {
    vi.spyOn(api, 'renameApiToken').mockResolvedValue({ token_id: 'tkid_1', name: 'x' });
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');

    fireEvent.click(document.querySelectorAll('button[title="Rename"]')[0]);
    fireEvent.keyDown(screen.getByDisplayValue('My laptop'), { key: 'Escape' });

    expect(screen.queryByDisplayValue('My laptop')).not.toBeInTheDocument();
    expect(api.renameApiToken).not.toHaveBeenCalled();
    expect(screen.getByText('My laptop')).toBeInTheDocument();
  });

  it('cancels without calling API when Cancel is clicked', async () => {
    vi.spyOn(api, 'renameApiToken').mockResolvedValue({ token_id: 'tkid_1', name: 'x' });
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');

    fireEvent.click(document.querySelectorAll('button[title="Rename"]')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(api.renameApiToken).not.toHaveBeenCalled();
    expect(screen.getByText('My laptop')).toBeInTheDocument();
  });

  it('does not call API when name is cleared and saved', async () => {
    vi.spyOn(api, 'renameApiToken').mockResolvedValue({ token_id: 'tkid_1', name: 'x' });
    render(<TenantApiTokensPage config={CONFIG} />);
    await screen.findByText('My laptop');

    fireEvent.click(document.querySelectorAll('button[title="Rename"]')[0]);
    fireEvent.change(screen.getByDisplayValue('My laptop'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(api.renameApiToken).not.toHaveBeenCalled();
  });
});
