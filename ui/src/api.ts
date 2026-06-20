import type { Agent, AgentHistory, ApiToken, Approval, AuditLog, Job, Tenant, TenantUser } from './types';

export async function adminLogin(apiUrl: string, password: string): Promise<string> {
  const url = apiUrl.replace(/\/$/, '');
  const resp = await fetch(`${url}/admin/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = (data as { error?: string }).error ?? `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg);
  }
  return (data as { token: string }).token;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

let _onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: () => void) {
  _onUnauthorized = fn;
}

async function req<T>(
  apiUrl: string,
  token: string,
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const resp = await fetch(`${apiUrl}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      _onUnauthorized?.();
    }
    const msg = (data as { error?: string; detail?: string }).error
      ?? (data as { detail?: string }).detail
      ?? `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg);
  }
  return data as T;
}

// Tenants
export const listTenants = (u: string, t: string) =>
  req<{ tenants: Tenant[] }>(u, t, 'GET', '/admin/tenants');

export const createTenant = (u: string, t: string, name: string) =>
  req<Tenant>(u, t, 'POST', '/admin/tenants', { name });

// Platform admin - read-only agent list (for tenant card counts)
export const listAgentsAdmin = (u: string, t: string, tenantId: string, tag?: string) => {
  const p = new URLSearchParams({ tenant_id: tenantId });
  if (tag) p.set('tag', tag);
  return req<{ agents: Agent[] }>(u, t, 'GET', `/admin/agents?${p}`);
};

// Platform admin - user management
export const listUsers = (u: string, t: string, tenantId: string) =>
  req<{ users: { user_id: string; name: string; username?: string; role?: string; status?: 'ACTIVE' | 'REVOKED' }[] }>(
    u, t, 'GET', `/admin/tenants/${tenantId}/users`,
  );
export const disableTenant = (u: string, t: string, tenantId: string) =>
  req<{ status: string }>(u, t, 'POST', `/admin/tenants/${tenantId}/disable`);

export const enableTenant = (u: string, t: string, tenantId: string) =>
  req<{ status: string }>(u, t, 'POST', `/admin/tenants/${tenantId}/enable`);

export const createTenantAdminUser = (
  u: string, t: string, tenantId: string,
  body: { username: string; name?: string; role?: string },
) =>
  req<TenantUser & { temp_password: string }>(u, t, 'POST', `/admin/tenants/${tenantId}/admin-users`, body);

export const platformResetUserPassword = (u: string, t: string, tenantId: string, userId: string) =>
  req<{ temp_password: string }>(u, t, 'POST', `/admin/tenants/${tenantId}/users/${userId}/reset-password`);

export const platformDisableUser = (u: string, t: string, tenantId: string, userId: string) =>
  req<{ status: string }>(u, t, 'POST', `/admin/tenants/${tenantId}/users/${userId}/disable`);

export const platformSetUserRole = (u: string, t: string, tenantId: string, userId: string, role: string) =>
  req<{ user_id: string; role: string }>(u, t, 'PATCH', `/admin/tenants/${tenantId}/users/${userId}/role`, { role });

export const platformUpdateUserName = (u: string, t: string, tenantId: string, userId: string, name: string) =>
  req<{ user_id: string; name: string }>(u, t, 'PATCH', `/admin/tenants/${tenantId}/users/${userId}/name`, { name });

export const listPlatformAuditLogs = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ logs: AuditLog[]; next_cursor?: string }>(u, t, 'GET', `/admin/audit-logs?${new URLSearchParams(params)}`);

// Tenant admin auth
export const tenantLogin = (u: string, body: { tenant_name: string; username: string; password: string }) =>
  fetch(`${u}/tenant/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async r => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new ApiError(r.status, (d as { error?: string }).error ?? `HTTP ${r.status}`);
    return d as { token: string; must_reset_password: boolean; user: { user_id: string; username: string; name?: string; role: string; tenant_id: string; tenant_name: string } };
  });

export const tenantChangePassword = (u: string, t: string, currentPassword: string, newPassword: string) =>
  req<{ changed: boolean }>(u, t, 'POST', '/tenant/me/password', {
    current_password: currentPassword,
    new_password: newPassword,
  });

export const tenantMe = (u: string, t: string) =>
  req<{ user_id: string; username: string; name?: string; role: string; tenant_id: string; tenant_name: string; must_reset_password: boolean }>(u, t, 'GET', '/tenant/me');

// Tenant admin - user management
export const listTenantUsers = (u: string, t: string) =>
  req<{ users: TenantUser[] }>(u, t, 'GET', '/tenant/users');

export const createTenantUser = (u: string, t: string, body: { username: string; name?: string; role?: string; allowed_agent_ids?: string[] | null }) =>
  req<TenantUser & { temp_password: string }>(u, t, 'POST', '/tenant/users', body);

export const disableTenantUser = (u: string, t: string, userId: string) =>
  req<{ status: string }>(u, t, 'POST', `/tenant/users/${userId}/disable`);

export const enableTenantUser = (u: string, t: string, userId: string) =>
  req<{ status: string }>(u, t, 'POST', `/tenant/users/${userId}/enable`);

export const revokeAllUserTokens = (u: string, t: string, userId: string) =>
  req<{ revoked: number }>(u, t, 'POST', `/tenant/users/${userId}/revoke-tokens`);

export const setTenantUserRole = (u: string, t: string, userId: string, role: string) =>
  req<{ role: string }>(u, t, 'PUT', `/tenant/users/${userId}/role`, { role });

export const resetTenantUserPassword = (u: string, t: string, userId: string) =>
  req<{ temp_password: string }>(u, t, 'POST', `/tenant/users/${userId}/reset-password`);

// Tenant admin - API tokens
export const listApiTokens = (u: string, t: string) =>
  req<{ tokens: ApiToken[] }>(u, t, 'GET', '/tenant/api-tokens');

export const createApiToken = (u: string, t: string, name: string) =>
  req<ApiToken>(u, t, 'POST', '/tenant/api-tokens', { name });

export const revokeApiToken = (u: string, t: string, tokenId: string) =>
  req<{ status: string }>(u, t, 'DELETE', `/tenant/api-tokens/${tokenId}`);

export const renameApiToken = (u: string, t: string, tokenId: string, name: string) =>
  req<{ token_id: string; name: string }>(u, t, 'PATCH', `/tenant/api-tokens/${tokenId}`, { name });

// Tenant admin - audit logs
export const listTenantAuditLogs = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ logs: AuditLog[]; next_cursor?: string }>(u, t, 'GET', `/tenant/audit-logs?${new URLSearchParams(params)}`);

// Tenant console - agents (read-only, all roles)
export const listTenantAgents = (u: string, t: string) =>
  req<{ agents: Agent[] }>(u, t, 'GET', '/agents');

// Tenant admin - agent management (admin/operator via JWT)
export const createTenantAgent = (
  u: string, t: string,
  mode?: string,
  grantServiceMgmt?: boolean,
  grantDocker?: boolean,
) =>
  req<Agent & { install_token: string; install_token_expires_at: string; commands: Record<string, string> }>(
    u, t, 'POST', '/tenant/agents',
    {
      ...(mode ? { mode } : {}),
      ...(grantServiceMgmt !== undefined ? { grant_service_mgmt: grantServiceMgmt } : {}),
      ...(grantDocker !== undefined ? { grant_docker: grantDocker } : {}),
    },
  );

export const reissueTenantInstallToken = (
  u: string, t: string, agentId: string,
  force?: boolean,
  grantServiceMgmt?: boolean,
  grantDocker?: boolean,
) =>
  req<{ agent_id: string; install_token: string; install_token_expires_at: string; commands: Record<string, string> }>(
    u, t, 'POST', `/tenant/agents/${agentId}/reissue-install-token`,
    {
      ...(force ? { force: true } : {}),
      ...(grantServiceMgmt !== undefined ? { grant_service_mgmt: grantServiceMgmt } : {}),
      ...(grantDocker !== undefined ? { grant_docker: grantDocker } : {}),
    },
  );

export const requestAgentRotation = (u: string, t: string, agentId: string) =>
  req<{ agent_id: string; rotation_requested: boolean }>(u, t, 'POST', `/tenant/agents/${agentId}/request-rotation`);

export const revokeTenantAgent = (u: string, t: string, agentId: string) =>
  req<{ agent_id: string; status: string }>(u, t, 'POST', `/tenant/agents/${agentId}/revoke`);

export const deleteTenantAgent = (u: string, t: string, agentId: string) =>
  req<{ agent_id: string; status: string }>(u, t, 'DELETE', `/tenant/agents/${agentId}`);

export const removeTenantAgent = (u: string, t: string, agentId: string) =>
  req<{ agent_id: string; removed: boolean }>(u, t, 'DELETE', `/tenant/agents/${agentId}/remove`);

export const setTenantAgentMode = (u: string, t: string, agentId: string, mode: string) =>
  req<{ agent_id: string; mode: string }>(u, t, 'PUT', `/tenant/agents/${agentId}/policy/mode`, { mode });

export const setTenantAgentTags = (u: string, t: string, agentId: string, tags: string[]) =>
  req<{ agent_id: string; tags: string[] }>(u, t, 'PUT', `/tenant/agents/${agentId}/tags`, { tags });

export const acknowledgeCapability = (u: string, t: string, agentId: string, capability: 'docker' | 'service_mgmt') =>
  req<{ agent_id: string; capability: string; acknowledged: boolean }>(
    u, t, 'POST', `/tenant/agents/${agentId}/acknowledge-capability`, { capability }
  );

// Tenant console - jobs (all roles)
export const listTenantJobs = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ jobs: Job[] }>(u, t, 'GET', `/jobs?${new URLSearchParams(params)}`);

// Tenant console - approvals (pending, for current user - all roles)
export const listTenantApprovals = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ approvals: Approval[] }>(u, t, 'GET', `/approvals/pending?${new URLSearchParams(params)}`);

// Tenant admin - approval management (operator+)
export const listAllTenantApprovals = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ approvals: Approval[] }>(u, t, 'GET', `/tenant/approvals?${new URLSearchParams(params)}`);

export const approveTenantApproval = (u: string, t: string, approvalId: string, duration?: string) =>
  req<Approval>(u, t, 'PUT', `/tenant/approvals/${approvalId}/approve`, duration ? { duration } : {});

export const denyTenantApproval = (u: string, t: string, approvalId: string) =>
  req<Approval>(u, t, 'PUT', `/tenant/approvals/${approvalId}/deny`);

export const deleteTenantApproval = (u: string, t: string, approvalId: string) =>
  req<{ deleted: boolean }>(u, t, 'DELETE', `/tenant/approvals/${approvalId}`);

export const tenantPreApprove = (u: string, t: string, agentId: string, command: string, duration?: string) =>
  req<Approval>(u, t, 'POST', '/tenant/approvals', {
    agent_id: agentId,
    command,
    ...(duration ? { duration } : {}),
  });


// Agent history
export const listAgentHistory = (u: string, t: string, agentId: string) =>
  req<{ history: AgentHistory[] }>(u, t, 'GET', `/tenant/agents/${agentId}/history`);

// User agent access
export const getUserAgentAccess = (u: string, t: string, userId: string) =>
  req<{ user_id: string; allowed_agent_ids: string[] | null }>(u, t, 'GET', `/tenant/users/${userId}/agents`);

export const setUserAgentAccess = (u: string, t: string, userId: string, allowedAgentIds: string[] | null) =>
  req<{ user_id: string; allowed_agent_ids: string[] | null }>(u, t, 'PUT', `/tenant/users/${userId}/agents`, { allowed_agent_ids: allowedAgentIds });
