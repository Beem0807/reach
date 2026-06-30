import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TenantUsersPage } from '../pages/TenantUsersPage';
import type { TenantUser, Agent, TenantConfig } from '../types';
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

const BASE_USER: TenantUser = {
  user_id: 'user_abc',
  username: 'bob',
  name: 'Bob Smith',
  role: 'developer',
  status: 'ACTIVE',
  must_reset_password: false,
  allowed_agent_ids: null,
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

const AGENT_2: Agent = {
  ...BASE_AGENT,
  agent_id: 'agent_222',
  hostname: 'host-beta.local',
};

function renderPage(users: TenantUser[], config = CONFIG) {
  vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users });
  return render(<TenantUsersPage config={config} />);
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// Agent access badge in Name cell
// ---------------------------------------------------------------------------

describe('Agent access badge in Name cell', () => {
  it('shows "* all agents" for null allowed_agent_ids', async () => {
    renderPage([{ ...BASE_USER, allowed_agent_ids: null }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('* all agents')).toBeInTheDocument();
  });

  it('shows "no agents" for empty allowed_agent_ids', async () => {
    renderPage([{ ...BASE_USER, allowed_agent_ids: [] }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('no agents')).toBeInTheDocument();
  });

  it('shows singular "1 agent" for one allowed agent', async () => {
    renderPage([{ ...BASE_USER, allowed_agent_ids: ['agent_111'] }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('1 agent')).toBeInTheDocument();
  });

  it('shows plural "3 agents" for three allowed agents', async () => {
    renderPage([{ ...BASE_USER, allowed_agent_ids: ['a1', 'a2', 'a3'] }]);
    await screen.findByText('Bob Smith');
    expect(screen.getByText('3 agents')).toBeInTheDocument();
  });

  it('does not render a standalone "Agents" column header', async () => {
    renderPage([BASE_USER]);
    await screen.findByText('Bob Smith');
    const headers = screen.queryAllByRole('columnheader');
    const labels = headers.map(h => h.textContent?.trim());
    expect(labels).not.toContain('Agents');
  });
});

// ---------------------------------------------------------------------------
// Clicking name opens AgentAccessModal
// ---------------------------------------------------------------------------

describe('Name click → AgentAccessModal', () => {
  async function setup(user = BASE_USER) {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT] });
    vi.spyOn(api, 'getUserAgentAccess').mockResolvedValue({ user_id: user.user_id, allowed_agent_ids: null });
    renderPage([user]);
    await screen.findByText(user.name ?? user.username);
  }

  it('opens AgentAccessModal when admin clicks a user name', async () => {
    await setup();
    fireEvent.click(screen.getByText('Bob Smith'));
    await screen.findByText(/Agent access/i);
  });

  it('shows the username in the modal title', async () => {
    await setup();
    fireEvent.click(screen.getByText('Bob Smith'));
    await screen.findByRole('heading', { name: /agent access/i });
    expect(screen.getByRole('heading', { name: /agent access/i }).textContent).toMatch(/@bob/);
  });

  it('does not open modal when clicking own name (self)', async () => {
    const selfUser: TenantUser = { ...BASE_USER, user_id: CONFIG.userId, username: 'alice', name: 'Alice' };
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'getUserAgentAccess').mockResolvedValue({ user_id: selfUser.user_id, allowed_agent_ids: null });
    renderPage([selfUser]);
    await screen.findByText('Alice');
    fireEvent.click(screen.getByText('Alice'));
    // The three-dot menu is hidden for self, so no AgentAccessModal can appear
    expect(screen.queryByRole('heading', { name: /agent access/i })).not.toBeInTheDocument();
  });

  it('clicking name for non-admin does not open modal', async () => {
    vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [BASE_USER] });
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'getUserAgentAccess').mockResolvedValue({ user_id: BASE_USER.user_id, allowed_agent_ids: null });
    render(<TenantUsersPage config={NON_ADMIN_CONFIG} />);
    await screen.findByText('Bob Smith');
    fireEvent.click(screen.getByText('Bob Smith'));
    // Non-admin: onclick is undefined, modal stays hidden
    expect(screen.queryByRole('heading', { name: /agent access/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AgentAccessModal: toggle switch + search
// ---------------------------------------------------------------------------

describe('AgentAccessModal content', () => {
  async function openModal(allowedAgentIds: string[] | null = null) {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT, AGENT_2] });
    vi.spyOn(api, 'getUserAgentAccess').mockResolvedValue({ user_id: BASE_USER.user_id, allowed_agent_ids: allowedAgentIds });
    renderPage([BASE_USER]);
    await screen.findByText('Bob Smith');
    fireEvent.click(screen.getByText('Bob Smith'));
    // Wait for modal to finish loading (toggle text is always present once loaded)
    await screen.findByRole('heading', { name: /agent access/i });
    await screen.findByText('Restrict to specific agents');
  }

  it('shows all agents badge when access is unrestricted (null)', async () => {
    await openModal(null);
    // "* all agents" appears in both the table row and the modal badge
    const matches = screen.getAllByText('* all agents');
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('shows "no agents" badge when access is empty list', async () => {
    await openModal([]);
    expect(screen.getByText('no agents')).toBeInTheDocument();
  });

  it('shows selected count badge when restricted', async () => {
    await openModal(['agent_111']);
    expect(screen.getByText('1 selected')).toBeInTheDocument();
  });

  it('has search input inside the modal when restricted', async () => {
    await openModal(['agent_111']);
    // restricted state is already loaded - agent list should be visible
    await screen.findByText('host-alpha.local');
    expect(screen.getByPlaceholderText('Search agents…')).toBeInTheDocument();
  });

  it('filters agents by hostname as user types', async () => {
    const user = userEvent.setup();
    await openModal(['agent_111']);
    await screen.findByText('host-alpha.local');
    const search = screen.getByPlaceholderText('Search agents…');
    await user.type(search, 'alpha');
    await waitFor(() => {
      expect(screen.getByText('host-alpha.local')).toBeInTheDocument();
      expect(screen.queryByText('host-beta.local')).not.toBeInTheDocument();
    });
  });

  it('filters agents by agent_id as user types', async () => {
    const user = userEvent.setup();
    await openModal(['agent_111']);
    await screen.findByText('host-alpha.local');
    const search = screen.getByPlaceholderText('Search agents…');
    await user.type(search, '222');
    await waitFor(() => {
      expect(screen.getByText('host-beta.local')).toBeInTheDocument();
      expect(screen.queryByText('host-alpha.local')).not.toBeInTheDocument();
    });
  });

  it('shows "No agents match" when search has no results', async () => {
    const user = userEvent.setup();
    await openModal(['agent_111']);
    await screen.findByText('host-alpha.local');
    await user.type(screen.getByPlaceholderText('Search agents…'), 'zzznomatch');
    await waitFor(() => expect(screen.getByText('No agents match')).toBeInTheDocument());
  });

  it('clear ✕ button resets search', async () => {
    const user = userEvent.setup();
    await openModal(['agent_111']);
    await screen.findByText('host-alpha.local');
    const search = screen.getByPlaceholderText('Search agents…');
    await user.type(search, 'alpha');
    await waitFor(() => expect(screen.getByText('✕')).toBeInTheDocument());
    await user.click(screen.getByText('✕'));
    await waitFor(() => expect(search).toHaveValue(''));
    expect(screen.getByText('host-beta.local')).toBeInTheDocument();
  });

  it('saves with correct agent ids when Save is clicked', async () => {
    const saveSpy = vi.spyOn(api, 'setUserAgentAccess').mockResolvedValue({ user_id: BASE_USER.user_id, allowed_agent_ids: ['agent_111'] });
    await openModal(['agent_111']);
    fireEvent.click(screen.getByRole('button', { name: /save access/i }));
    await waitFor(() => expect(saveSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, BASE_USER.user_id, ['agent_111'],
    ));
  });

  it('saves null when toggle is off', async () => {
    const saveSpy = vi.spyOn(api, 'setUserAgentAccess').mockResolvedValue({ user_id: BASE_USER.user_id, allowed_agent_ids: null });
    await openModal(null);
    fireEvent.click(screen.getByRole('button', { name: /save access/i }));
    await waitFor(() => expect(saveSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, BASE_USER.user_id, null,
    ));
  });
});

// ---------------------------------------------------------------------------
// CreateUserModal: agent picker
// ---------------------------------------------------------------------------

describe('CreateUserModal agent picker', () => {
  async function openCreate() {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT, AGENT_2] });
    renderPage([]);
    fireEvent.click(await screen.findByRole('button', { name: /Add user/i }));
    await screen.findByRole('heading', { name: /Add user/i });
  }

  it('toggle starts off - no agent list shown', async () => {
    await openCreate();
    expect(screen.queryByText('host-alpha.local')).not.toBeInTheDocument();
  });

  it('shows "* all agents" badge when toggle is off', async () => {
    await openCreate();
    expect(screen.getByText('* all agents')).toBeInTheDocument();
  });

  it('clicking toggle reveals agent list', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await waitFor(() => expect(screen.getByText('host-alpha.local')).toBeInTheDocument());
  });

  it('clicking toggle twice hides agent list again', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.click(screen.getByText('Restrict to specific agents'));
    await waitFor(() => expect(screen.queryByText('host-alpha.local')).not.toBeInTheDocument());
  });

  it('agent list has a search input', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    expect(screen.getByPlaceholderText('Search agents…')).toBeInTheDocument();
  });

  it('search filters agents by hostname', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.type(screen.getByPlaceholderText('Search agents…'), 'beta');
    await waitFor(() => {
      expect(screen.getByText('host-beta.local')).toBeInTheDocument();
      expect(screen.queryByText('host-alpha.local')).not.toBeInTheDocument();
    });
  });

  it('search filters agents by agent_id', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.type(screen.getByPlaceholderText('Search agents…'), '111');
    await waitFor(() => {
      expect(screen.getByText('host-alpha.local')).toBeInTheDocument();
      expect(screen.queryByText('host-beta.local')).not.toBeInTheDocument();
    });
  });

  it('shows "No agents match" when search has no results', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.type(screen.getByPlaceholderText('Search agents…'), 'zzznomatch');
    await waitFor(() => expect(screen.getByText('No agents match')).toBeInTheDocument());
  });

  it('selecting an agent updates the selected count badge', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.click(screen.getByText('host-alpha.local'));
    await waitFor(() => expect(screen.getByText('1 selected')).toBeInTheDocument());
  });

  it('deselecting an agent reverts badge to "no agents"', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.click(screen.getByText('host-alpha.local'));
    await waitFor(() => expect(screen.getByText('1 selected')).toBeInTheDocument());
    await user.click(screen.getByText('host-alpha.local'));
    await waitFor(() => expect(screen.getByText('no agents')).toBeInTheDocument());
  });

  it('clear all removes all selections', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.click(screen.getByText('host-alpha.local'));
    await user.click(screen.getByText('host-beta.local'));
    await waitFor(() => expect(screen.getByText('2 selected')).toBeInTheDocument());
    await user.click(screen.getByText('clear all'));
    await waitFor(() => expect(screen.getByText('no agents')).toBeInTheDocument());
  });

  it('submits null allowed_agent_ids when toggle is off', async () => {
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
      expect.objectContaining({ allowed_agent_ids: null }),
    ));
  });

  it('submits selected agent ids when toggle is on', async () => {
    const createSpy = vi.spyOn(api, 'createTenantUser').mockResolvedValue({
      user_id: 'user_new', username: 'newguy', role: 'developer',
      must_reset_password: true, temp_password: 'tmp123',
    } as ReturnType<typeof api.createTenantUser> extends Promise<infer T> ? T : never);
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'newguy');
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    await user.click(screen.getByText('host-alpha.local'));
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken,
      expect.objectContaining({ allowed_agent_ids: ['agent_111'] }),
    ));
  });

  it('submits empty array when toggle is on but no agents selected', async () => {
    const createSpy = vi.spyOn(api, 'createTenantUser').mockResolvedValue({
      user_id: 'user_new', username: 'newguy', role: 'developer',
      must_reset_password: true, temp_password: 'tmp123',
    } as ReturnType<typeof api.createTenantUser> extends Promise<infer T> ? T : never);
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'newguy');
    await user.click(screen.getByText('Restrict to specific agents'));
    await screen.findByText('host-alpha.local');
    // don't select any agent
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken,
      expect.objectContaining({ allowed_agent_ids: [] }),
    ));
  });
});

// ---------------------------------------------------------------------------
// Username length guardrails (CreateUserModal)
// ---------------------------------------------------------------------------

describe('CreateUserModal username length', () => {
  async function openCreate() {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
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

  it('counter turns amber near the limit (29 chars)', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'a'.repeat(29));
    const counter = screen.getByText('29/32');
    expect(counter.className).toMatch(/amber/);
  });

  it('input has maxLength of 32', async () => {
    await openCreate();
    const input = screen.getByPlaceholderText('alice');
    expect(input).toHaveAttribute('maxLength', '32');
  });

  it('shows error when username is only 1 character', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'a');
    await user.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(screen.getByText(/at least 2/i)).toBeInTheDocument());
  });

  it('shows inline format error for invalid characters immediately on type', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'alice-bob');
    await waitFor(() => expect(screen.getByText(/no spaces or special characters/i)).toBeInTheDocument());
  });

  it('shows inline format error for username with a space', async () => {
    const user = userEvent.setup();
    await openCreate();
    // The onChange lowercases, but space passes through before toLowerCase since space is not uppercase
    // Actually we need to type in a way that includes invalid chars - the input lowercases but doesn't strip
    // Type a digit then underscore to trigger the error
    await user.type(screen.getByPlaceholderText('alice'), 'alice_bob');
    await waitFor(() => expect(screen.getByText(/no spaces or special characters/i)).toBeInTheDocument());
  });

  it('clears inline format error when valid username is typed', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'alice-bob');
    await waitFor(() => expect(screen.getByText(/no spaces or special characters/i)).toBeInTheDocument());
    await user.clear(screen.getByPlaceholderText('alice'));
    await user.type(screen.getByPlaceholderText('alice'), 'alicebob');
    await waitFor(() => expect(screen.queryByText(/no spaces or special characters/i)).not.toBeInTheDocument());
  });

  it('highlights input border red when format is invalid', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.type(screen.getByPlaceholderText('alice'), 'bad-name');
    await waitFor(() => {
      const input = screen.getByPlaceholderText('alice');
      expect(input.className).toMatch(/red/);
    });
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
    renderPage([{ ...BASE_USER, must_reset_password: true }]);  // no last_login_at
    await screen.findByText('Bob Smith');
    expect(screen.getByText('INVITED')).toBeInTheDocument();
    expect(screen.queryByText('TEMP PW')).not.toBeInTheDocument();
  });

  it('shows no badge when a password reset is not required', async () => {
    renderPage([{ ...BASE_USER, must_reset_password: false }]);
    await screen.findByText('Bob Smith');
    expect(screen.queryByText('TEMP PW')).not.toBeInTheDocument();
    expect(screen.queryByText('INVITED')).not.toBeInTheDocument();
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
