import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { UsersPage } from '../pages/UsersPage';
import type { Config, Tenant, TenantUser, User } from '../types';
import * as api from '../api';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONFIG: Config = {
  apiUrl: 'https://api.example.com',
  adminToken: 'admin_tok',
};

const TENANT_A: Tenant = { tenant_id: 'tenant_1', name: 'Acme Corp', status: 'ACTIVE' };
const TENANT_B: Tenant = { tenant_id: 'tenant_2', name: 'Beta Inc', status: 'ACTIVE' };
const DISABLED_TENANT: Tenant = { tenant_id: 'tenant_3', name: 'Old Co', status: 'DISABLED' };

const USER_A: User = {
  user_id: 'user_1',
  name: 'Alice Smith',
  username: 'alice',
  role: 'admin',
  status: 'ACTIVE',
  must_reset_password: false,
  created_at: '2024-01-01T00:00:00Z',
};

const USER_B: User = {
  user_id: 'user_2',
  name: 'Bob Jones',
  username: 'bob',
  role: 'developer',
  status: 'ACTIVE',
  must_reset_password: false,
  created_at: '2024-02-01T00:00:00Z',
};

const REVOKED_USER: User = {
  user_id: 'user_3',
  name: 'Carol Lee',
  username: 'carol',
  role: 'operator',
  status: 'REVOKED',
  must_reset_password: false,
  created_at: '2024-03-01T00:00:00Z',
};

function mockApis({
  tenants = [TENANT_A] as Tenant[],
  users = [USER_A] as User[],
} = {}) {
  vi.spyOn(api, 'listTenants').mockResolvedValue({ tenants });
  vi.spyOn(api, 'listUsers').mockResolvedValue({ users });
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// Initial rendering
// ---------------------------------------------------------------------------

describe('initial rendering', () => {
  it('shows the Users page heading', async () => {
    mockApis();
    render(<UsersPage config={CONFIG} />);
    expect(screen.getByText('Users')).toBeInTheDocument();
  });

  it('loads and displays tenant names in the selector', async () => {
    mockApis({ tenants: [TENANT_A, TENANT_B] });
    render(<UsersPage config={CONFIG} />);
    expect(await screen.findByText(`${TENANT_A.name} (${TENANT_A.tenant_id})`)).toBeInTheDocument();
    expect(screen.getByText(`${TENANT_B.name} (${TENANT_B.tenant_id})`)).toBeInTheDocument();
  });

  it('selects the first tenant automatically', async () => {
    mockApis({ tenants: [TENANT_A, TENANT_B] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText(`${TENANT_A.name} (${TENANT_A.tenant_id})`);
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.value).toBe(TENANT_A.tenant_id);
  });

  it('shows an error when tenants fail to load', async () => {
    vi.spyOn(api, 'listTenants').mockRejectedValue(new Error('network'));
    vi.spyOn(api, 'listUsers').mockResolvedValue({ users: [] });
    render(<UsersPage config={CONFIG} />);
    expect(await screen.findByText('Failed to load tenants')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// User list
// ---------------------------------------------------------------------------

describe('user list', () => {
  it('renders user names', async () => {
    mockApis({ users: [USER_A, USER_B] });
    render(<UsersPage config={CONFIG} />);
    expect(await screen.findByText('Alice Smith')).toBeInTheDocument();
    expect(screen.getByText('Bob Jones')).toBeInTheDocument();
  });

  it('renders usernames with @ prefix', async () => {
    mockApis({ users: [USER_A] });
    render(<UsersPage config={CONFIG} />);
    expect(await screen.findByText('@alice')).toBeInTheDocument();
  });

  it('shows "active" badge for ACTIVE users', async () => {
    mockApis({ users: [USER_A] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  it('shows "disabled" badge for REVOKED users', async () => {
    mockApis({ users: [REVOKED_USER] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Carol Lee');
    expect(screen.getByText('disabled')).toBeInTheDocument();
  });

  it('shows TEMP PW badge for users who must reset password', async () => {
    mockApis({ users: [{ ...USER_A, must_reset_password: true }] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    expect(screen.getByText('TEMP PW')).toBeInTheDocument();
  });

  it('shows "No users in this tenant yet" when list is empty', async () => {
    mockApis({ users: [] });
    render(<UsersPage config={CONFIG} />);
    expect(await screen.findByText('No users in this tenant yet')).toBeInTheDocument();
  });

  it('shows no action menu for REVOKED users', async () => {
    mockApis({ users: [REVOKED_USER] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Carol Lee');
    expect(screen.queryByRole('button', { name: /···/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Disabled tenant
// ---------------------------------------------------------------------------

describe('disabled tenant', () => {
  it('shows DISABLED badge next to tenant name', async () => {
    mockApis({ tenants: [DISABLED_TENANT], users: [] });
    render(<UsersPage config={CONFIG} />);
    expect(await screen.findByText('DISABLED')).toBeInTheDocument();
  });

  it('disables the Add user button for disabled tenants', async () => {
    mockApis({ tenants: [DISABLED_TENANT], users: [] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('DISABLED');
    const addBtn = screen.getByRole('button', { name: /add user/i });
    expect(addBtn).toBeDisabled();
  });

  it('hides "Add the first user" link when tenant is disabled', async () => {
    mockApis({ tenants: [DISABLED_TENANT], users: [] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('No users in this tenant yet');
    expect(screen.queryByText('Add the first user →')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tenant switching
// ---------------------------------------------------------------------------

describe('tenant switching', () => {
  it('reloads users when a different tenant is selected', async () => {
    vi.spyOn(api, 'listTenants').mockResolvedValue({ tenants: [TENANT_A, TENANT_B] });
    const listUsers = vi.spyOn(api, 'listUsers').mockResolvedValue({ users: [USER_A] });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');

    listUsers.mockResolvedValue({ users: [USER_B] });
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: TENANT_B.tenant_id } });

    expect(await screen.findByText('Bob Jones')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Add user modal
// ---------------------------------------------------------------------------

describe('Add user modal', () => {
  it('opens the create user modal on button click', async () => {
    mockApis();
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    fireEvent.click(screen.getByRole('button', { name: /add user/i }));
    expect(screen.getByRole('heading', { name: 'Add user' })).toBeInTheDocument();
  });

  it('shows validation error when name is empty', async () => {
    mockApis();
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    fireEvent.click(screen.getByRole('button', { name: /add user/i }));
    fireEvent.click(screen.getByRole('button', { name: /create user/i }));
    expect(await screen.findByText('Name is required')).toBeInTheDocument();
  });

  it('shows validation error when username is empty', async () => {
    mockApis();
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    fireEvent.click(screen.getByRole('button', { name: /add user/i }));
    const inputs = screen.getAllByRole('textbox');
    fireEvent.change(inputs[0], { target: { value: 'Alice' } });
    fireEvent.click(screen.getByRole('button', { name: /create user/i }));
    expect(await screen.findByText('Username is required')).toBeInTheDocument();
  });

  it('calls createTenantAdminUser on valid submit', async () => {
    mockApis();
    const createUser = vi.spyOn(api, 'createTenantAdminUser').mockResolvedValue({
      user_id: 'new_user', name: 'Dave', username: 'dave', temp_password: 'abc123',
    } as TenantUser & { temp_password: string });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    fireEvent.click(screen.getByRole('button', { name: /add user/i }));
    const inputs = screen.getAllByRole('textbox');
    fireEvent.change(inputs[0], { target: { value: 'Dave Jones' } });
    fireEvent.change(inputs[1], { target: { value: 'dave' } });
    fireEvent.click(screen.getByRole('button', { name: /create user/i }));
    await waitFor(() => expect(createUser).toHaveBeenCalled());
  });

  it('shows credentials modal after user creation', async () => {
    mockApis();
    vi.spyOn(api, 'createTenantAdminUser').mockResolvedValue({
      user_id: 'new_user', name: 'Dave', username: 'dave', temp_password: 'temp-pw-here',
    } as TenantUser & { temp_password: string });
    render(<UsersPage config={CONFIG} />);
    await screen.findByText('Alice Smith');
    fireEvent.click(screen.getByRole('button', { name: /add user/i }));
    const inputs = screen.getAllByRole('textbox');
    fireEvent.change(inputs[0], { target: { value: 'Dave Jones' } });
    fireEvent.change(inputs[1], { target: { value: 'dave' } });
    fireEvent.click(screen.getByRole('button', { name: /create user/i }));
    expect(await screen.findByText('User created')).toBeInTheDocument();
    expect(screen.getByText('temp-pw-here')).toBeInTheDocument();
  });
});
