import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  adminLogin, ApiError,
  listTenants, createTenant,
  listUsers, listAgentsAdmin,
  disableTenant, enableTenant,
  createTenantAdminUser, platformResetUserPassword, platformDisableUser,
  platformSetUserRole, platformUpdateUserName,
  listPlatformAuditLogs,
  tenantLogin, tenantChangePassword, tenantMe,
  listTenantUsers, createTenantUser, disableTenantUser, setTenantUserRole, resetTenantUserPassword,
  listTenantAuditLogs,
  listTenantAgents, createTenantAgent, revokeTenantAgent, deleteTenantAgent,
  reissueTenantInstallToken, requestAgentRotation, removeTenantAgent,
  setTenantAgentMode, setTenantAgentTags,
  approveTenantApproval, denyTenantApproval, deleteTenantApproval,
  listApiTokens, createApiToken, revokeApiToken, renameApiToken,
  listTenantJobs, listTenantApprovals, listAllTenantApprovals, tenantPreApprove,
  listAgentHistory, getUserAgentAccess, setUserAgentAccess,
} from '../api';

const URL = 'https://api.example.com';
const TOKEN = 'test-token';
const TENANT = 'tenant_abc';
const USER = 'user_xyz';
const AGENT = 'agent_123';
const APPROVAL = 'appr_xyz';

function mockFetch(body: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  }));
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// adminLogin
// ---------------------------------------------------------------------------

describe('adminLogin', () => {
  it('returns token on success', async () => {
    mockFetch({ token: 'sess-token-123' });
    const t = await adminLogin(URL, 'pass');
    expect(t).toBe('sess-token-123');
  });

  it('throws ApiError on 401', async () => {
    mockFetch({ error: 'invalid credentials' }, 401);
    await expect(adminLogin(URL, 'wrong')).rejects.toBeInstanceOf(ApiError);
  });

  it('posts to /admin/login', async () => {
    mockFetch({ token: 't' });
    await adminLogin(URL, 'pass');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/login`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({ password: 'pass' });
  });
});

// ---------------------------------------------------------------------------
// Tenants
// ---------------------------------------------------------------------------

describe('listTenants', () => {
  it('calls GET /admin/tenants with auth header', async () => {
    mockFetch({ tenants: [] });
    await listTenants(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants`);
    expect(opts.method).toBe('GET');
    expect(opts.headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });
});

describe('createTenant', () => {
  it('posts to /admin/tenants with name', async () => {
    mockFetch({ tenant_id: TENANT, name: 'Acme' });
    await createTenant(URL, TOKEN, 'Acme');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({ name: 'Acme' });
  });
});

describe('disableTenant', () => {
  it('posts to /admin/tenants/{id}/disable', async () => {
    mockFetch({ status: 'DISABLED' });
    await disableTenant(URL, TOKEN, TENANT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/disable`);
    expect(opts.method).toBe('POST');
  });
});

describe('enableTenant', () => {
  it('posts to /admin/tenants/{id}/enable', async () => {
    mockFetch({ status: 'ACTIVE' });
    await enableTenant(URL, TOKEN, TENANT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/enable`);
    expect(opts.method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// Platform admin users
// ---------------------------------------------------------------------------

describe('listUsers', () => {
  it('calls GET /admin/tenants/{id}/users', async () => {
    mockFetch({ users: [] });
    await listUsers(URL, TOKEN, TENANT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/users`);
    expect(opts.method).toBe('GET');
  });
});

describe('createTenantAdminUser', () => {
  it('posts to /admin/tenants/{id}/admin-users', async () => {
    mockFetch({ user_id: USER, username: 'alice', temp_password: 'tmp' });
    await createTenantAdminUser(URL, TOKEN, TENANT, { username: 'alice', role: 'admin' });
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/admin-users`);
    expect(opts.method).toBe('POST');
  });
});

describe('platformResetUserPassword', () => {
  it('posts to /admin/tenants/{id}/users/{uid}/reset-password', async () => {
    mockFetch({ temp_password: 'tmp' });
    await platformResetUserPassword(URL, TOKEN, TENANT, USER);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/users/${USER}/reset-password`);
    expect(opts.method).toBe('POST');
  });
});

describe('platformDisableUser', () => {
  it('posts to /admin/tenants/{id}/users/{uid}/disable', async () => {
    mockFetch({ status: 'REVOKED' });
    await platformDisableUser(URL, TOKEN, TENANT, USER);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/users/${USER}/disable`);
    expect(opts.method).toBe('POST');
  });
});

describe('platformSetUserRole', () => {
  it('sends PATCH with role', async () => {
    mockFetch({ user_id: USER, role: 'operator' });
    await platformSetUserRole(URL, TOKEN, TENANT, USER, 'operator');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/users/${USER}/role`);
    expect(opts.method).toBe('PATCH');
    expect(JSON.parse(opts.body)).toEqual({ role: 'operator' });
  });
});

describe('platformUpdateUserName', () => {
  it('sends PATCH with name', async () => {
    mockFetch({ user_id: USER, name: 'Alice' });
    await platformUpdateUserName(URL, TOKEN, TENANT, USER, 'Alice');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/admin/tenants/${TENANT}/users/${USER}/name`);
    expect(opts.method).toBe('PATCH');
    expect(JSON.parse(opts.body)).toEqual({ name: 'Alice' });
  });
});

// ---------------------------------------------------------------------------
// Platform admin - agents (read-only)
// ---------------------------------------------------------------------------

describe('listAgentsAdmin', () => {
  it('passes tenant_id as query param', async () => {
    mockFetch({ agents: [] });
    await listAgentsAdmin(URL, TOKEN, TENANT);
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain(`tenant_id=${TENANT}`);
  });

  it('includes optional tag param when provided', async () => {
    mockFetch({ agents: [] });
    await listAgentsAdmin(URL, TOKEN, TENANT, 'env:prod');
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('tag=env%3Aprod');
  });
});

// ---------------------------------------------------------------------------
// Tenant agent management
// ---------------------------------------------------------------------------

describe('listTenantAgents', () => {
  it('calls GET /agents', async () => {
    mockFetch({ agents: [] });
    await listTenantAgents(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/agents`);
    expect(opts.method).toBe('GET');
  });
});

describe('createTenantAgent', () => {
  it('posts to /tenant/agents', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', commands: {} });
    await createTenantAgent(URL, TOKEN, 'wild');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toMatchObject({ mode: 'wild' });
  });

  it('sends grant_service_mgmt when provided', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', commands: {} });
    await createTenantAgent(URL, TOKEN, 'wild', true, false);
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
    expect(body.grant_service_mgmt).toBe(true);
    expect(body.grant_docker).toBe(false);
  });

  it('sends grant_docker when provided', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', commands: {} });
    await createTenantAgent(URL, TOKEN, 'wild', false, true);
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
    expect(body.grant_docker).toBe(true);
  });

  it('omits grant flags when undefined', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', commands: {} });
    await createTenantAgent(URL, TOKEN, 'wild');
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
    expect(body).not.toHaveProperty('grant_service_mgmt');
    expect(body).not.toHaveProperty('grant_docker');
  });
});

describe('revokeTenantAgent', () => {
  it('posts to /tenant/agents/{id}/revoke', async () => {
    mockFetch({ agent_id: AGENT, status: 'REVOKED' });
    await revokeTenantAgent(URL, TOKEN, AGENT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/revoke`);
    expect(opts.method).toBe('POST');
  });
});

describe('deleteTenantAgent', () => {
  it('sends DELETE to /tenant/agents/{id}', async () => {
    mockFetch({ agent_id: AGENT, status: 'DELETED' });
    await deleteTenantAgent(URL, TOKEN, AGENT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}`);
    expect(opts.method).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// Tenant approval management
// ---------------------------------------------------------------------------

describe('approveTenantApproval', () => {
  it('puts to /tenant/approvals/{id}/approve', async () => {
    mockFetch({ approval_id: APPROVAL, status: 'approved' });
    await approveTenantApproval(URL, TOKEN, APPROVAL);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/approvals/${APPROVAL}/approve`);
    expect(opts.method).toBe('PUT');
  });

  it('includes duration in body when provided', async () => {
    mockFetch({ approval_id: APPROVAL, status: 'approved' });
    await approveTenantApproval(URL, TOKEN, APPROVAL, '8h');
    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ duration: '8h' });
  });
});

describe('denyTenantApproval', () => {
  it('puts to /tenant/approvals/{id}/deny', async () => {
    mockFetch({ approval_id: APPROVAL, status: 'denied' });
    await denyTenantApproval(URL, TOKEN, APPROVAL);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/approvals/${APPROVAL}/deny`);
    expect(opts.method).toBe('PUT');
  });
});

describe('deleteTenantApproval', () => {
  it('sends DELETE to /tenant/approvals/{id}', async () => {
    mockFetch({ deleted: true });
    await deleteTenantApproval(URL, TOKEN, APPROVAL);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/approvals/${APPROVAL}`);
    expect(opts.method).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// API Tokens
// ---------------------------------------------------------------------------

const TOKEN_ID = 'tkid_abc';

describe('listApiTokens', () => {
  it('calls GET /tenant/api-tokens with auth header', async () => {
    mockFetch({ tokens: [] });
    await listApiTokens(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/api-tokens`);
    expect(opts.method).toBe('GET');
    expect(opts.headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });
});

describe('createApiToken', () => {
  it('posts to /tenant/api-tokens with name', async () => {
    mockFetch({ token_id: TOKEN_ID, name: 'laptop', token: 'tok_abc' });
    await createApiToken(URL, TOKEN, 'laptop');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/api-tokens`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({ name: 'laptop' });
  });
});

describe('revokeApiToken', () => {
  it('sends DELETE to /tenant/api-tokens/{id}', async () => {
    mockFetch({ status: 'REVOKED' });
    await revokeApiToken(URL, TOKEN, TOKEN_ID);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/api-tokens/${TOKEN_ID}`);
    expect(opts.method).toBe('DELETE');
  });
});

describe('renameApiToken', () => {
  it('sends PATCH to /tenant/api-tokens/{id} with new name', async () => {
    mockFetch({ token_id: TOKEN_ID, name: 'prod key' });
    await renameApiToken(URL, TOKEN, TOKEN_ID, 'prod key');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/api-tokens/${TOKEN_ID}`);
    expect(opts.method).toBe('PATCH');
    expect(JSON.parse(opts.body)).toEqual({ name: 'prod key' });
  });

  it('throws ApiError on 404', async () => {
    mockFetch({ error: 'token not found' }, 404);
    await expect(renameApiToken(URL, TOKEN, 'bad-id', 'x')).rejects.toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// Tenant auth
// ---------------------------------------------------------------------------

describe('tenantLogin', () => {
  it('posts to /tenant/login with credentials', async () => {
    mockFetch({ token: 'sess', must_reset_password: false, user: { user_id: 'u1', username: 'alice', role: 'admin', tenant_id: TENANT, tenant_name: 'acme' } });
    await tenantLogin(URL, { tenant_name: 'acme', username: 'alice', password: 'pw' });
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/login`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toMatchObject({ tenant_name: 'acme', username: 'alice', password: 'pw' });
  });

  it('throws ApiError on 401', async () => {
    mockFetch({ error: 'invalid credentials' }, 401);
    await expect(tenantLogin(URL, { tenant_name: 'x', username: 'y', password: 'z' })).rejects.toBeInstanceOf(ApiError);
  });
});

describe('tenantChangePassword', () => {
  it('posts to /tenant/me/password', async () => {
    mockFetch({ changed: true });
    await tenantChangePassword(URL, TOKEN, 'oldpass', 'newpass');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/me/password`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toMatchObject({ current_password: 'oldpass', new_password: 'newpass' });
  });
});

describe('tenantMe', () => {
  it('calls GET /tenant/me with auth header', async () => {
    mockFetch({ user_id: USER, username: 'alice', role: 'admin', tenant_id: TENANT, tenant_name: 'acme', must_reset_password: false });
    await tenantMe(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/me`);
    expect(opts.method).toBe('GET');
    expect(opts.headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });
});

// ---------------------------------------------------------------------------
// Tenant user management
// ---------------------------------------------------------------------------

describe('listTenantUsers', () => {
  it('calls GET /tenant/users', async () => {
    mockFetch({ users: [] });
    await listTenantUsers(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users`);
    expect(opts.method).toBe('GET');
  });
});

describe('createTenantUser', () => {
  it('posts to /tenant/users with body', async () => {
    mockFetch({ user_id: USER, username: 'bob', temp_password: 'tmp' });
    await createTenantUser(URL, TOKEN, { username: 'bob', role: 'operator' });
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toMatchObject({ username: 'bob', role: 'operator' });
  });
});

describe('disableTenantUser', () => {
  it('posts to /tenant/users/{id}/disable', async () => {
    mockFetch({ status: 'REVOKED' });
    await disableTenantUser(URL, TOKEN, USER);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users/${USER}/disable`);
    expect(opts.method).toBe('POST');
  });
});

describe('setTenantUserRole', () => {
  it('sends PUT with role to /tenant/users/{id}/role', async () => {
    mockFetch({ role: 'operator' });
    await setTenantUserRole(URL, TOKEN, USER, 'operator');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users/${USER}/role`);
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ role: 'operator' });
  });
});

describe('resetTenantUserPassword', () => {
  it('posts to /tenant/users/{id}/reset-password', async () => {
    mockFetch({ temp_password: 'tmp123' });
    await resetTenantUserPassword(URL, TOKEN, USER);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users/${USER}/reset-password`);
    expect(opts.method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// Audit logs
// ---------------------------------------------------------------------------

describe('listPlatformAuditLogs', () => {
  it('calls GET /admin/audit-logs', async () => {
    mockFetch({ logs: [] });
    await listPlatformAuditLogs(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/admin/audit-logs');
    expect(opts.method).toBe('GET');
  });

  it('passes params as query string', async () => {
    mockFetch({ logs: [] });
    await listPlatformAuditLogs(URL, TOKEN, { tenant_id: TENANT });
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain(`tenant_id=${TENANT}`);
  });
});

describe('listTenantAuditLogs', () => {
  it('calls GET /tenant/audit-logs', async () => {
    mockFetch({ logs: [] });
    await listTenantAuditLogs(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/tenant/audit-logs');
    expect(opts.method).toBe('GET');
  });

  it('passes params as query string', async () => {
    mockFetch({ logs: [] });
    await listTenantAuditLogs(URL, TOKEN, { action: 'job.created' });
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('action=job.created');
  });
});

// ---------------------------------------------------------------------------
// Agent install token / rotation / removal
// ---------------------------------------------------------------------------

describe('reissueTenantInstallToken', () => {
  it('posts to /tenant/agents/{id}/reissue-install-token', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', install_token_expires_at: 'ts', commands: {} });
    await reissueTenantInstallToken(URL, TOKEN, AGENT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/reissue-install-token`);
    expect(opts.method).toBe('POST');
  });

  it('includes force flag when true', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', install_token_expires_at: 'ts', commands: {} });
    await reissueTenantInstallToken(URL, TOKEN, AGENT, true);
    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ force: true });
  });

  it('sends grant_service_mgmt and grant_docker when provided', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', install_token_expires_at: 'ts', commands: {} });
    await reissueTenantInstallToken(URL, TOKEN, AGENT, undefined, true, false);
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
    expect(body.grant_service_mgmt).toBe(true);
    expect(body.grant_docker).toBe(false);
  });

  it('omits grant flags when undefined', async () => {
    mockFetch({ agent_id: AGENT, install_token: 'tok', install_token_expires_at: 'ts', commands: {} });
    await reissueTenantInstallToken(URL, TOKEN, AGENT);
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
    expect(body).not.toHaveProperty('grant_service_mgmt');
    expect(body).not.toHaveProperty('grant_docker');
  });
});

describe('requestAgentRotation', () => {
  it('posts to /tenant/agents/{id}/request-rotation', async () => {
    mockFetch({ agent_id: AGENT, rotation_requested: true });
    await requestAgentRotation(URL, TOKEN, AGENT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/request-rotation`);
    expect(opts.method).toBe('POST');
  });
});

describe('removeTenantAgent', () => {
  it('sends DELETE to /tenant/agents/{id}/remove', async () => {
    mockFetch({ agent_id: AGENT, removed: true });
    await removeTenantAgent(URL, TOKEN, AGENT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/remove`);
    expect(opts.method).toBe('DELETE');
  });
});

describe('setTenantAgentMode', () => {
  it('sends PUT with mode to /tenant/agents/{id}/policy/mode', async () => {
    mockFetch({ agent_id: AGENT, mode: 'readonly' });
    await setTenantAgentMode(URL, TOKEN, AGENT, 'readonly');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/policy/mode`);
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ mode: 'readonly' });
  });
});

describe('setTenantAgentTags', () => {
  it('sends PUT with tags to /tenant/agents/{id}/tags', async () => {
    mockFetch({ agent_id: AGENT, tags: ['env:prod'] });
    await setTenantAgentTags(URL, TOKEN, AGENT, ['env:prod']);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/tags`);
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ tags: ['env:prod'] });
  });
});

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

describe('listTenantJobs', () => {
  it('calls GET /jobs', async () => {
    mockFetch({ jobs: [] });
    await listTenantJobs(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/jobs');
    expect(opts.method).toBe('GET');
    expect(opts.headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it('passes agent_id query param', async () => {
    mockFetch({ jobs: [] });
    await listTenantJobs(URL, TOKEN, { agent_id: AGENT });
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain(`agent_id=${AGENT}`);
  });
});

// ---------------------------------------------------------------------------
// Approval queues
// ---------------------------------------------------------------------------

describe('listTenantApprovals', () => {
  it('calls GET /approvals/pending', async () => {
    mockFetch({ approvals: [] });
    await listTenantApprovals(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/approvals/pending');
    expect(opts.method).toBe('GET');
  });
});

describe('listAllTenantApprovals', () => {
  it('calls GET /tenant/approvals', async () => {
    mockFetch({ approvals: [] });
    await listAllTenantApprovals(URL, TOKEN);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/tenant/approvals');
    expect(opts.method).toBe('GET');
  });
});

describe('tenantPreApprove', () => {
  it('posts to /tenant/approvals with agent_id and command', async () => {
    mockFetch({ approval_id: APPROVAL, status: 'approved' });
    await tenantPreApprove(URL, TOKEN, AGENT, 'docker ps');
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/approvals`);
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toMatchObject({ agent_id: AGENT, command: 'docker ps' });
  });

  it('includes duration when provided', async () => {
    mockFetch({ approval_id: APPROVAL, status: 'approved' });
    await tenantPreApprove(URL, TOKEN, AGENT, 'docker ps', '8h');
    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(opts.body)).toMatchObject({ duration: '8h' });
  });
});

// ---------------------------------------------------------------------------
// Agent history and user-agent access
// ---------------------------------------------------------------------------

describe('listAgentHistory', () => {
  it('calls GET /tenant/agents/{id}/history', async () => {
    mockFetch({ history: [] });
    await listAgentHistory(URL, TOKEN, AGENT);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/agents/${AGENT}/history`);
    expect(opts.method).toBe('GET');
  });
});

describe('getUserAgentAccess', () => {
  it('calls GET /tenant/users/{id}/agents', async () => {
    mockFetch({ user_id: USER, allowed_agent_ids: null });
    await getUserAgentAccess(URL, TOKEN, USER);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users/${USER}/agents`);
    expect(opts.method).toBe('GET');
  });
});

describe('setUserAgentAccess', () => {
  it('sends PUT with allowed_agent_ids', async () => {
    mockFetch({ user_id: USER, allowed_agent_ids: [AGENT] });
    await setUserAgentAccess(URL, TOKEN, USER, [AGENT]);
    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${URL}/tenant/users/${USER}/agents`);
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ allowed_agent_ids: [AGENT] });
  });

  it('passes null to revoke all access restrictions', async () => {
    mockFetch({ user_id: USER, allowed_agent_ids: null });
    await setUserAgentAccess(URL, TOKEN, USER, null);
    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ allowed_agent_ids: null });
  });
});

// ---------------------------------------------------------------------------
// ApiError
// ---------------------------------------------------------------------------

describe('ApiError', () => {
  it('exposes status code', async () => {
    mockFetch({ error: 'not found' }, 404);
    try {
      await listTenants(URL, TOKEN);
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(404);
      expect((e as ApiError).message).toBe('not found');
    }
  });
});
