import type { Agent, AgentHistory, ApiToken, Approval, AuditLog, FanoutPreview, FanoutResult, Fleet, FleetRun, FleetToken, FleetWavePolicy, HostRule, Job, JobPreview, K8sRule, RunStatus, Tenant, TenantSettings, TenantWavePolicy, TenantUser, UserAccessScope } from './types';

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
export const listTenants = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ tenants: Tenant[]; total?: number; limit?: number; offset?: number }>(
    u, t, 'GET', `/admin/tenants${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

export const createTenant = (u: string, t: string, name: string) =>
  req<Tenant>(u, t, 'POST', '/admin/tenants', { name });

// Platform admin - read-only agent list (for tenant card counts)
export const listAgentsAdmin = (u: string, t: string, tenantId: string, tag?: string) => {
  const p = new URLSearchParams({ tenant_id: tenantId });
  if (tag) p.set('tag', tag);
  return req<{ agents: Agent[] }>(u, t, 'GET', `/admin/agents?${p}`);
};

// Platform admin - user management
export const listUsers = (u: string, t: string, tenantId: string, params: Record<string, string> = {}) =>
  req<{ users: { user_id: string; name: string; username?: string; role?: string; status?: 'ACTIVE' | 'REVOKED' }[]; total?: number; limit?: number; offset?: number }>(
    u, t, 'GET', `/admin/tenants/${tenantId}/users${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`,
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

// Tenant admin - per-tenant settings (retention windows + fan-out cap)
export const getTenantSettings = (u: string, t: string) =>
  req<TenantSettings>(u, t, 'GET', '/tenant/settings');

// A null value clears a key back to the platform default; wave_policy is a nested object.
export type TenantSettingsPatch = Record<string, number | null | TenantWavePolicy>;
export const updateTenantSettings = (u: string, t: string, body: TenantSettingsPatch) =>
  req<TenantSettings>(u, t, 'PUT', '/tenant/settings', body);

// Platform admin - view/override any tenant's settings (bypasses tenant bounds).
export const adminGetTenantSettings = (u: string, t: string, tenantId: string) =>
  req<TenantSettings>(u, t, 'GET', `/admin/tenants/${tenantId}/settings`);

export const adminUpdateTenantSettings = (u: string, t: string, tenantId: string, body: TenantSettingsPatch) =>
  req<TenantSettings>(u, t, 'PUT', `/admin/tenants/${tenantId}/settings`, body);

// Tenant admin - user management
export const listTenantUsers = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ users: TenantUser[]; total?: number; limit?: number; offset?: number }>(
    u, t, 'GET', `/tenant/users${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

export const createTenantUser = (u: string, t: string, body: { username: string; name?: string; role?: string } & Partial<UserAccessScope>) =>
  req<TenantUser & { temp_password: string }>(u, t, 'POST', '/tenant/users', body);

export const disableTenantUser = (u: string, t: string, userId: string) =>
  req<{ status: string }>(u, t, 'POST', `/tenant/users/${userId}/disable`);

export const enableTenantUser = (u: string, t: string, userId: string) =>
  req<{ status: string }>(u, t, 'POST', `/tenant/users/${userId}/enable`);

export const deleteTenantUser = (u: string, t: string, userId: string) =>
  req<{ user_id: string; deleted: boolean }>(u, t, 'DELETE', `/tenant/users/${userId}`);

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

// Two-step deletion on one endpoint: DELETE an ACTIVE token revokes it (soft);
// DELETE an already-REVOKED token hard-deletes the record.
export const revokeApiToken = (u: string, t: string, tokenId: string) =>
  req<{ status: string }>(u, t, 'DELETE', `/tenant/api-tokens/${tokenId}`);

export const deleteApiToken = (u: string, t: string, tokenId: string) =>
  req<{ status: string }>(u, t, 'DELETE', `/tenant/api-tokens/${tokenId}`);

// Tenant admin - fleets (reusable-join-token groups of host agents)
export const listFleets = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ fleets: Fleet[]; default_reap_after_seconds: number; default_max_fanout?: number; default_wave_policy?: FleetWavePolicy; total?: number; limit?: number; offset?: number }>(
    u, t, 'GET', `/tenant/fleets${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

// A single fleet's members (lazy-loaded on expand, via the fleet_id index) - so the
// Fleets page never has to load every agent in the tenant. Carries grant state so the
// grant-mismatch reconcile/accept flows work without a separate agents fetch.
export const listFleetAgents = (u: string, t: string, fleetId: string, params: Record<string, string> = {}) =>
  req<{ fleet_id: string; fleet_name?: string; agents: Agent[]; total?: number; limit?: number; offset?: number }>(
    u, t, 'GET', `/fleets/${fleetId}/agents${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

export const createFleet = (
  u: string, t: string,
  body: { name: string; mode?: string; grant_service_mgmt?: boolean; grant_docker?: boolean; sandbox_ack?: boolean; tags?: string[]; reap_after_seconds?: number | null; max_fanout?: number | null; wave_policy?: FleetWavePolicy | null },
) => req<Fleet & FleetToken>(u, t, 'POST', '/tenant/fleets', body);

export const updateFleet = (
  u: string, t: string, fleetId: string,
  body: Partial<{ name: string; mode: string; tags: string[]; reap_after_seconds: number | null;
                  grant_service_mgmt: boolean; grant_docker: boolean; sandbox_ack: boolean; max_fanout: number | null;
                  wave_policy: FleetWavePolicy | null }>,
) => req<Fleet>(u, t, 'PUT', `/tenant/fleets/${fleetId}`, body);

// Reconcile a fleet's existing members to its (possibly edited) grants, clearing the
// grant mismatch. Verified against detection: members whose host doesn't yet report a
// granted capability come back under `blocked` (not reconciled). Pass agentId for a
// single member; omit it to reconcile every mismatched member.
// Both resolutions share one endpoint: POST /tenant/fleets/{id}/resolve-grants with a
// `resolution` of "reconcile" or "accept" (+ optional agent_id for a single member).
export const reconcileFleetGrants = (u: string, t: string, fleetId: string, agentId?: string) =>
  req<{ fleet_id: string; reconciled: number;
        blocked: { agent_id: string; hostname?: string; reason: string }[];
        agent_id?: string | null; grant_service_mgmt: boolean; grant_docker: boolean }>(
    u, t, 'POST', `/tenant/fleets/${fleetId}/resolve-grants`,
    { resolution: 'reconcile', ...(agentId ? { agent_id: agentId } : {}) });

// Accept a fleet member's grant mismatch as an intentional exception (keeps its real
// grants, stops flagging it) instead of reconciling. Re-flags if the fleet grants change.
export const acceptFleetGrantMismatch = (u: string, t: string, fleetId: string, agentId?: string) =>
  req<{ fleet_id: string; accepted: number; agent_id?: string | null }>(
    u, t, 'POST', `/tenant/fleets/${fleetId}/resolve-grants`,
    { resolution: 'accept', ...(agentId ? { agent_id: agentId } : {}) });

export const rotateFleetToken = (u: string, t: string, fleetId: string, graceSeconds?: number) =>
  req<FleetToken>(u, t, 'POST', `/tenant/fleets/${fleetId}/rotate-token`,
    graceSeconds === undefined ? {} : { grace_seconds: graceSeconds });

export const revokeFleet = (u: string, t: string, fleetId: string, members: 'keep' | 'remove' = 'keep') =>
  req<{ fleet_id: string; status: string; members: string; affected: number }>(u, t, 'POST', `/tenant/fleets/${fleetId}/revoke`, { members });

export const deleteFleet = (u: string, t: string, fleetId: string) =>
  req<{ fleet_id: string; deleted: boolean }>(u, t, 'DELETE', `/tenant/fleets/${fleetId}`);

export const removeFleetMember = (u: string, t: string, fleetId: string, agentId: string) =>
  req<{ agent_id: string; deleted: boolean }>(u, t, 'DELETE', `/tenant/fleets/${fleetId}/members/${agentId}`);

export const renameApiToken = (u: string, t: string, tokenId: string, name: string) =>
  req<{ token_id: string; name: string }>(u, t, 'PATCH', `/tenant/api-tokens/${tokenId}`, { name });

// Tenant admin - audit logs
export const listTenantAuditLogs = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ logs: AuditLog[]; next_cursor?: string }>(u, t, 'GET', `/tenant/audit-logs?${new URLSearchParams(params)}`);

// Tenant console - agents (read-only, all roles). Pagination is opt-in: pass a `limit`
// (and optional `q` search over hostname/id/tags, `offset`) to get one page + `total`;
// omit params to get every accessible agent (used where the full set is needed).
export const listTenantAgents = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ agents: Agent[]; total?: number; limit?: number; offset?: number; all_tags?: string[] }>(
    u, t, 'GET', `/agents${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

// Tenant admin - agent management (admin/operator via JWT)
// Installable versions for the create dropdown, newest-first ('latest' is the
// implicit default the UI shows on top). type=host lists agent binaries; k8s
// lists Helm chart versions.
export const listAgentVersions = (u: string, t: string, type: 'host' | 'k8s') =>
  req<{ type: string; default: string; versions: string[] }>(
    u, t, 'GET', `/tenant/agent-versions?type=${type}`,
  );

export const createTenantAgent = (
  u: string, t: string,
  mode?: string,
  grantServiceMgmt?: boolean,
  grantDocker?: boolean,
  type?: 'host' | 'k8s',
  grantUserIds?: string[],
  version?: string,
  grantReadonlyUserIds?: string[],
  hostOs?: 'linux' | 'mac',
) =>
  req<Agent & { install_token: string; install_token_expires_at: string; commands: Record<string, string> }>(
    u, t, 'POST', '/tenant/agents',
    {
      ...(mode ? { mode } : {}),
      ...(type ? { type } : {}),
      ...(grantServiceMgmt !== undefined ? { grant_service_mgmt: grantServiceMgmt } : {}),
      ...(grantDocker !== undefined ? { grant_docker: grantDocker } : {}),
      ...(grantUserIds && grantUserIds.length ? { grant_user_ids: grantUserIds } : {}),
      ...(grantReadonlyUserIds && grantReadonlyUserIds.length ? { grant_readonly_user_ids: grantReadonlyUserIds } : {}),
      ...(version && version !== 'latest' ? { version } : {}),
      ...(hostOs ? { os: hostOs } : {}),
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

export const acknowledgeCapability = (u: string, t: string, agentId: string, capability: 'docker' | 'service_mgmt' | 'k8s_permissions') =>
  req<{ agent_id: string; capability: string; acknowledged: boolean }>(
    u, t, 'POST', `/tenant/agents/${agentId}/acknowledge-capability`, { capability }
  );

// Acknowledge (or revoke) running readonly/approved WITHOUT the Landlock kernel sandbox on a
// host agent whose kernel lacks it. Delivered to the agent on its next sync.
export const acknowledgeSandbox = (u: string, t: string, agentId: string, acknowledged: boolean) =>
  req<{ agent_id: string; sandbox_ack: boolean }>(
    u, t, 'POST', `/tenant/agents/${agentId}/acknowledge-sandbox`, { acknowledged }
  );

// Tenant console - jobs (all roles). Params: agent_id, fleet_id, run_id, limit, cursor.
export const listTenantJobs = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ jobs: Job[]; next_cursor?: string }>(u, t, 'GET', `/jobs?${new URLSearchParams(params)}`);

// A single job by id - used to poll a just-dispatched run until it reaches a terminal
// state (the agent runs it on its next poll, then posts stdout/exit back).
export const getJob = (u: string, t: string, jobId: string) =>
  req<Job>(u, t, 'GET', `/jobs/${jobId}`);

// Fan-out runs for a fleet. Uses the CLI/API-token endpoint, which also accepts the
// console session token.
export const listFleetRuns = (u: string, t: string, fleetId: string, params: Record<string, string> = {}) =>
  req<{ fleet_id: string; fleet_name?: string; runs: FleetRun[]; next_cursor?: string }>(
    u, t, 'GET', `/fleets/${fleetId}/runs${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

// Fan-out runs across standalone (non-fleet) agents - tag fan-outs. The tenant-wide
// counterpart to listFleetRuns.
export const listTagRuns = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ runs: FleetRun[]; next_cursor?: string }>(
    u, t, 'GET', `/jobs/runs${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`);

// A run's full status: counts + the bounded who/why of skipped members + wave progress.
export const getRun = (u: string, t: string, runId: string) =>
  req<RunStatus>(u, t, 'GET', `/tenant/runs/${runId}`);

// --- Run a command from the console (write-gated per target) -----------------
// These reuse the CLI/API-token endpoints, which also accept the console session token.

// Single-agent job. With dry_run:true returns a JobPreview (classify + confirm, no
// dispatch); otherwise creates the job and returns { job_id, status }.
export function createJob(u: string, t: string, agentId: string, command: string,
  opts: { dry_run: true }): Promise<JobPreview>;
export function createJob(u: string, t: string, agentId: string, command: string,
  opts?: { dry_run?: false }): Promise<{ job_id: string; status: string }>;
export function createJob(u: string, t: string, agentId: string, command: string,
  opts?: { dry_run?: boolean }) {
  const body: Record<string, unknown> = { agent_id: agentId, command };
  if (opts?.dry_run) body.dry_run = true;
  return req(u, t, 'POST', '/jobs', body);
}

// Fleet fan-out. With dry_run:true returns a FanoutPreview; otherwise a FanoutResult.
export function fleetFanout(u: string, t: string, fleetId: string,
  opts: { command: string; max_targets?: number; dry_run: true }): Promise<FanoutPreview>;
export function fleetFanout(u: string, t: string, fleetId: string,
  opts: { command: string; max_targets?: number; dry_run?: false }): Promise<FanoutResult>;
export function fleetFanout(u: string, t: string, fleetId: string,
  opts: { command: string; max_targets?: number; dry_run?: boolean }) {
  const body: Record<string, unknown> = { command: opts.command };
  if (opts.max_targets != null) body.max_targets = opts.max_targets;
  if (opts.dry_run) body.dry_run = true;
  return req(u, t, 'POST', `/fleets/${fleetId}/jobs`, body);
}

// Tag fan-out across standalone agents. dry_run overload mirrors fleetFanout.
export function fanoutByTag(u: string, t: string,
  opts: { tag: string; command: string; type?: string; dry_run: true }): Promise<FanoutPreview>;
export function fanoutByTag(u: string, t: string,
  opts: { tag: string; command: string; type?: string; dry_run?: false }): Promise<FanoutResult>;
export function fanoutByTag(u: string, t: string,
  opts: { tag: string; command: string; type?: string; dry_run?: boolean }) {
  const body: Record<string, unknown> = { tag: opts.tag, command: opts.command };
  if (opts.type) body.type = opts.type;
  if (opts.dry_run) body.dry_run = true;
  return req(u, t, 'POST', '/jobs/fanout', body);
}

// Staged-rollout control (only meaningful for a run with wave_total > 1).
export const pauseRun = (u: string, t: string, runId: string) =>
  req<{ run_id: string; state: string; current_wave: number; wave_total: number }>(u, t, 'POST', `/tenant/runs/${runId}/pause`);

export const resumeRun = (u: string, t: string, runId: string) =>
  req<{ run_id: string; state: string; current_wave: number; wave_total: number }>(u, t, 'POST', `/tenant/runs/${runId}/resume`);

export const cancelRun = (u: string, t: string, runId: string) =>
  req<{ run_id: string; state: string; canceled: number }>(u, t, 'POST', `/tenant/runs/${runId}/cancel`);

// Tenant console - approvals (pending, for current user - all roles)
export const listTenantApprovals = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ approvals: Approval[]; total?: number; limit?: number; offset?: number }>(u, t, 'GET', `/approvals/pending?${new URLSearchParams(params)}`);

// Tenant admin - approval management (operator+)
export const listAllTenantApprovals = (u: string, t: string, params: Record<string, string> = {}) =>
  req<{ approvals: Approval[]; total?: number; limit?: number; offset?: number }>(u, t, 'GET', `/tenant/approvals?${new URLSearchParams(params)}`);

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

// Fleet-scoped pre-approval: approvals apply to every member of the fleet.
export const tenantPreApproveFleet = (u: string, t: string, fleetId: string, command: string, duration?: string) =>
  req<Approval>(u, t, 'POST', '/tenant/approvals', {
    fleet_id: fleetId,
    command,
    ...(duration ? { duration } : {}),
  });

// k8s agents: create/pre-approve a structured rule instead of a command string.
export const tenantPreApproveRule = (u: string, t: string, agentId: string, rule: K8sRule, duration?: string) =>
  req<Approval>(u, t, 'POST', '/tenant/approvals', {
    agent_id: agentId,
    k8s_rule: rule,
    ...(duration ? { duration } : {}),
  });

// host agents: create/pre-approve a structured exec rule {bin, args[]} instead of a string.
export const tenantPreApproveHostRule = (u: string, t: string, agentId: string, rule: HostRule, duration?: string) =>
  req<Approval>(u, t, 'POST', '/tenant/approvals', {
    agent_id: agentId,
    host_rule: rule,
    ...(duration ? { duration } : {}),
  });

// Fleet-scoped structured rule: applies to every member of the fleet (fleets are host-only).
export const tenantPreApproveFleetHostRule = (u: string, t: string, fleetId: string, rule: HostRule, duration?: string) =>
  req<Approval>(u, t, 'POST', '/tenant/approvals', {
    fleet_id: fleetId,
    host_rule: rule,
    ...(duration ? { duration } : {}),
  });


// Agent history
export const listAgentHistory = (u: string, t: string, agentId: string) =>
  req<{ history: AgentHistory[] }>(u, t, 'GET', `/tenant/agents/${agentId}/history`);

// User agent/fleet access scope (read-write + read-only, agents + fleets)
export const getUserAgentAccess = (u: string, t: string, userId: string) =>
  req<{ user_id: string } & UserAccessScope>(u, t, 'GET', `/tenant/users/${userId}/agents`);

export const setUserAgentAccess = (u: string, t: string, userId: string, scope: UserAccessScope) =>
  req<{ user_id: string } & UserAccessScope>(u, t, 'PUT', `/tenant/users/${userId}/agents`, scope);
