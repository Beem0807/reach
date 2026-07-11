import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TenantUsersPage } from '../pages/TenantUsersPage';
import type { TenantUser, Agent, Fleet, TenantConfig, UserAccessScope } from '../types';
import * as api from '../api';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok_test',
  tenantId: 'tenant_1',
  tenantName: 'acme',
  userId: 'user_self',
  username: 'alice',
  name: 'Alice',
  role: 'admin',
  mustResetPassword: false,
};

const NON_ADMIN_CONFIG: TenantConfig = { ...CONFIG, role: 'developer' };

// A restricted developer with no access by default.
const BASE_USER: TenantUser = {
  user_id: 'user_abc',
  username: 'bob',
  name: 'Bob Smith',
  role: 'developer',
  status: 'ACTIVE',
  must_reset_password: false,
  readwrite_agent_ids: [],
  readonly_agent_ids: [],
  readwrite_fleet_ids: [],
  readonly_fleet_ids: [],
  created_at: '2024-01-01T00:00:00Z',
};

const BASE_AGENT: Agent = {
  agent_id: 'agent_111',
  tenant_id: 'tenant_1',
  status: 'ACTIVE',
  hostname: 'host-alpha.local',
  mode: 'wild',
  access_level: 'open',
  tags: [],
  grant_docker: false,
  grant_service_mgmt: false,
};

const AGENT_2: Agent = { ...BASE_AGENT, agent_id: 'agent_222', hostname: 'host-beta.local' };

const FLEET_1: Fleet = {
  fleet_id: 'fleet_1', tenant_id: 'tenant_1', name: 'web-prod', type: 'host',
  mode: 'readonly', grant_service_mgmt: false, grant_docker: false, status: 'ACTIVE',
};

function emptyScope(user_id: string): { user_id: string } & UserAccessScope {
  return { user_id, readwrite_agent_ids: [], readonly_agent_ids: [], readwrite_fleet_ids: [], readonly_fleet_ids: [] };
}

function renderPage(users: TenantUser[], config = CONFIG) {
  vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users });
  return render(<TenantUsersPage config={config} />);
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// Filters, search & pagination
// ---------------------------------------------------------------------------

describe('Filters, search & pagination', () => {
  it('stages the role dropdown and applies it only on Search', async () => {
    const spy = vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [BASE_USER] });
    render(<TenantUsersPage config={CONFIG} />);
    await screen.findByText('Bob Smith');

    // Choosing the option alone must not re-query.
    fireEvent.change(screen.getByDisplayValue('All roles'), { target: { value: 'developer' } });
    expect(spy).not.toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ role: 'developer' }),
    );

    fireEvent.click(screen.getByText('Search'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ role: 'developer', offset: '0' }),
    ));
  });

  it('applies role + status + search together on one Search click', async () => {
    const spy = vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [BASE_USER] });
    render(<TenantUsersPage config={CONFIG} />);
    await screen.findByText('Bob Smith');

    fireEvent.change(screen.getByDisplayValue('All roles'), { target: { value: 'developer' } });
    fireEvent.change(screen.getByDisplayValue('All statuses'), { target: { value: 'REVOKED' } });
    fireEvent.change(screen.getByPlaceholderText('Search name or username…'), { target: { value: 'bob' } });
    fireEvent.click(screen.getByText('Search'));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken,
      expect.objectContaining({ role: 'developer', status: 'REVOKED', q: 'bob' }),
    ));
  });

  it('searches only when the Search button is clicked', async () => {
    const spy = vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [BASE_USER] });
    render(<TenantUsersPage config={CONFIG} />);
    await screen.findByText('Bob Smith');

    fireEvent.change(screen.getByPlaceholderText('Search name or username…'), { target: { value: 'bob' } });
    expect(spy).not.toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ q: 'bob' }),
    );

    fireEvent.click(screen.getByText('Search'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ q: 'bob' }),
    ));
  });

  it('pages forward with Next when total exceeds the page size', async () => {
    const spy = vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [BASE_USER], total: 42, limit: 20, offset: 0 });
    render(<TenantUsersPage config={CONFIG} />);
    await screen.findByText('Bob Smith');
    expect(screen.getByText(/Showing 1–20 of 42/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ offset: '20' }),
    ));
  });
});

// ---------------------------------------------------------------------------
// Access summary in the Name cell
// ---------------------------------------------------------------------------

describe('Access summary in Name cell', () => {
  it('shows "tenant-wide" for an admin', async () => {
    renderPage([{ ...BASE_USER, role: 'admin', readwrite_agent_ids: null, readonly_agent_ids: null }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('tenant-wide')).toBeInTheDocument();
  });

  it('shows "no access" for a user with empty grants', async () => {
    renderPage([BASE_USER]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('no access')).toBeInTheDocument();
  });

  it('shows agent read-write and read-only levels', async () => {
    renderPage([{ ...BASE_USER, readwrite_agent_ids: ['a1', 'a2'], readonly_agent_ids: ['a3'] }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('agents')).toBeInTheDocument();
    expect(screen.getByText('2 r/w')).toBeInTheDocument();
    expect(screen.getByText('1 read')).toBeInTheDocument();
  });

  it('shows fleet access with its level, distinct from agents', async () => {
    renderPage([{ ...BASE_USER, readwrite_agent_ids: ['a1'], readonly_fleet_ids: ['fleet_1'] }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('agents')).toBeInTheDocument();
    expect(screen.getByText('fleets')).toBeInTheDocument();
    expect(screen.getByText('1 r/w')).toBeInTheDocument();   // the agent grant
    expect(screen.getByText('1 read')).toBeInTheDocument();  // the fleet grant
  });

  it('shows fleet access even when there are no agent grants', async () => {
    renderPage([{ ...BASE_USER, readwrite_fleet_ids: ['fleet_1', 'fleet_2'] }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('fleets')).toBeInTheDocument();
    expect(screen.getByText('2 r/w')).toBeInTheDocument();
    expect(screen.queryByText('no access')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Access modal (capability picker)
// ---------------------------------------------------------------------------

describe('AccessModal', () => {
  async function openModal(scope = emptyScope(BASE_USER.user_id)) {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT, AGENT_2] });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [FLEET_1], default_reap_after_seconds: 1800 });
    vi.spyOn(api, 'getUserAgentAccess').mockResolvedValue(scope);
    renderPage([BASE_USER]);
    await screen.findByText('Bob Smith');
    fireEvent.click(screen.getByText('Bob Smith'));
    await screen.findByRole('heading', { name: /access/i });
  }

  it('opens with the username in the title', async () => {
    await openModal();
    expect(screen.getByRole('heading', { name: /access/i }).textContent).toMatch(/@bob/);
  });

  it('shows Agents and Fleets sections', async () => {
    await openModal();
    expect(screen.getByText('Agents')).toBeInTheDocument();
    expect(screen.getByText('Fleets')).toBeInTheDocument();
  });

  it('warns when the user will have no access', async () => {
    await openModal();
    expect(screen.getByText(/no agent access/i)).toBeInTheDocument();
  });

  it('shows the agent in Custom mode when it has a read-write grant', async () => {
    await openModal({ ...emptyScope(BASE_USER.user_id), readwrite_agent_ids: ['agent_111'] });
    expect(await screen.findByText('host-alpha.local')).toBeInTheDocument();
  });

  it('"All read-write" materializes every agent id (no wildcard)', async () => {
    const saveSpy = vi.spyOn(api, 'setUserAgentAccess').mockResolvedValue(emptyScope(BASE_USER.user_id));
    await openModal();
    // The first "All read-write" button is the Agents section.
    fireEvent.click(screen.getAllByText('All read-write')[0]);
    fireEvent.click(screen.getByRole('button', { name: /save access/i }));
    await waitFor(() => expect(saveSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, BASE_USER.user_id,
      expect.objectContaining({ readwrite_agent_ids: expect.arrayContaining(['agent_111', 'agent_222']) }),
    ));
    const arg = saveSpy.mock.calls[0][3];
    expect(arg.readwrite_agent_ids).not.toContain('*');
  });

  it('preserves an existing custom grant on save', async () => {
    const saveSpy = vi.spyOn(api, 'setUserAgentAccess').mockResolvedValue(emptyScope(BASE_USER.user_id));
    await openModal({ ...emptyScope(BASE_USER.user_id), readonly_agent_ids: ['agent_111'] });
    fireEvent.click(screen.getByRole('button', { name: /save access/i }));
    await waitFor(() => expect(saveSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, BASE_USER.user_id,
      expect.objectContaining({ readonly_agent_ids: ['agent_111'], readwrite_agent_ids: [] }),
    ));
  });

  it('does not open the modal for a non-admin viewer', async () => {
    vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [BASE_USER] });
    render(<TenantUsersPage config={NON_ADMIN_CONFIG} />);
    await screen.findByText('Bob Smith');
    fireEvent.click(screen.getByText('Bob Smith'));
    expect(screen.queryByRole('heading', { name: /^access/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Create user modal: no access by default
// ---------------------------------------------------------------------------

describe('CreateUserModal', () => {
  async function openCreate() {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT] });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [FLEET_1], default_reap_after_seconds: 1800 });
    renderPage([]);
    fireEvent.click(await screen.findByRole('button', { name: /Add user/i }));
    await screen.findByRole('heading', { name: /Add user/i });
  }

  it('shows the access editor (Agents + Fleets) for a non-admin', async () => {
    await openCreate();
    expect(screen.getByText('Agents')).toBeInTheDocument();
    expect(screen.getByText('Fleets')).toBeInTheDocument();
  });

  it('notes tenant-wide access when admin role is selected', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Admin'));
    expect(screen.getByText(/tenant-wide access/i)).toBeInTheDocument();
  });

  it('creates a non-admin with no access by default (empty scope)', async () => {
    const createSpy = vi.spyOn(api, 'createTenantUser').mockResolvedValue({
      user_id: 'user_new', username: 'newguy', role: 'developer',
      must_reset_password: true, temp_password: 'tmp123',
    } as ReturnType<typeof api.createTenantUser> extends Promise<infer T> ? T : never);
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'newguy');
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken,
      { username: 'newguy', name: '', role: 'developer', readwrite_agent_ids: [], readonly_agent_ids: [], readwrite_fleet_ids: [], readonly_fleet_ids: [] },
    ));
  });

  it('grants read-write on every agent at creation (materialized, no wildcard)', async () => {
    const createSpy = vi.spyOn(api, 'createTenantUser').mockResolvedValue({
      user_id: 'user_new', username: 'newguy', role: 'developer',
      must_reset_password: true, temp_password: 'tmp123',
    } as ReturnType<typeof api.createTenantUser> extends Promise<infer T> ? T : never);
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'newguy');
    await user.click(screen.getAllByText('All read-write')[0]);  // Agents section
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken,
      expect.objectContaining({ username: 'newguy', role: 'developer', readwrite_agent_ids: ['agent_111'] }),
    ));
  });
});

// ---------------------------------------------------------------------------
// Username length guardrails (CreateUserModal)
// ---------------------------------------------------------------------------

describe('CreateUserModal username length', () => {
  async function openCreate() {
    renderPage([]);
    fireEvent.click(await screen.findByRole('button', { name: /Add user/i }));
    await screen.findByRole('heading', { name: /Add user/i });
  }

  it('shows 0/32 counter initially', async () => {
    await openCreate();
    expect(screen.getByText('0/32')).toBeInTheDocument();
  });

  it('counter updates as user types', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'hello');
    expect(screen.getByText('5/32')).toBeInTheDocument();
  });

  it('input has maxLength of 32', async () => {
    await openCreate();
    expect(screen.getByPlaceholderText('alice')).toHaveAttribute('maxLength', '32');
  });

  it('shows error when username is only 1 character', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'a');
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(screen.getByText(/at least 2/i)).toBeInTheDocument());
  });

  it('shows inline format error for invalid characters', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'alice-bob');
    await waitFor(() => expect(screen.getByText(/no spaces or special characters/i)).toBeInTheDocument());
  });

  it('blocks submit when username has invalid characters', async () => {
    const createSpy = vi.spyOn(api, 'createTenantUser');
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'bad-name');
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createSpy).not.toHaveBeenCalled());
  });

  it('accepts username of exactly 2 characters', async () => {
    const createSpy = vi.spyOn(api, 'createTenantUser').mockResolvedValue({
      user_id: 'u1', username: 'ab', role: 'developer',
      must_reset_password: true, temp_password: 'tmp',
    } as ReturnType<typeof api.createTenantUser> extends Promise<infer T> ? T : never);
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'ab');
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalled());
  });
});

// ---------------------------------------------------------------------------
// Users table basics
// ---------------------------------------------------------------------------

describe('TenantUsersPage table basics', () => {
  it('shows user name', async () => {
    renderPage([BASE_USER]);
    expect(await screen.findByText('Bob Smith')).toBeInTheDocument();
  });

  it('shows username column', async () => {
    renderPage([BASE_USER]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('@bob')).toBeInTheDocument();
  });

  it('shows empty state when no users', async () => {
    renderPage([]);
    expect(await screen.findByText('No users')).toBeInTheDocument();
  });

  it('shows TEMP PW badge for a user who has logged in but must reset', async () => {
    renderPage([{ ...BASE_USER, must_reset_password: true, last_login_at: '2024-02-01T00:00:00Z' }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('TEMP PW')).toBeInTheDocument();
  });

  it('shows INVITED badge for a created user who has never logged in', async () => {
    renderPage([{ ...BASE_USER, must_reset_password: true }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('INVITED')).toBeInTheDocument();
    expect(screen.queryByText('TEMP PW')).not.toBeInTheDocument();
  });

  it('hides Add user button for non-admins', async () => {
    vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [] });
    render(<TenantUsersPage config={NON_ADMIN_CONFIG} />);
    await screen.findByText('No users');
    expect(screen.queryByRole('button', { name: /Add user/i })).not.toBeInTheDocument();
  });

  it('shows multiple users', async () => {
    const user2: TenantUser = { ...BASE_USER, user_id: 'user_2', username: 'carol', name: 'Carol' };
    renderPage([BASE_USER, user2]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('Carol')).toBeInTheDocument();
  });
});
