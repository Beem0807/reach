// ---------- Platform admin config ----------
export interface Config {
  apiUrl: string;
  adminToken: string;
}

// ---------- Tenant admin config ----------
export type TenantRole = 'admin' | 'operator' | 'developer';

export interface TenantConfig {
  apiUrl: string;
  tenantToken: string;
  tenantId: string;
  tenantName: string;
  userId: string;
  username: string;
  name?: string;
  role: TenantRole;
  mustResetPassword: boolean;
}

export interface Tenant {
  tenant_id: string;
  name: string;
  status?: 'ACTIVE' | 'DISABLED';
  created_at?: string;
}

// Per-tenant settings: retention windows (days) + the fan-out blast-radius cap.
export type TenantSettingKey =
  | 'approval_retention_days'
  | 'job_retention_days'
  | 'run_retention_days'
  | 'audit_retention_days'
  | 'agent_history_retention_days'
  | 'fanout_cap';

// Staged-rollout wave policy: advancement mode + failure handling. A missing entry means
// "no staging" for that (scope, read/write). The wave SIZE comes from the fan-out cap.
export type WaveMode = 'auto' | 'manual';
export type WaveFailure = 'stop' | 'continue';
// concurrency = hosts per wave; omitted means "use the fan-out cap" (and never above it).
export interface WaveStrategy { mode: WaveMode; on_failure: WaveFailure; concurrency?: number; }
export type WaveRW = 'read' | 'write';
// Tenant policy is scoped: tag runs vs fleet-run defaults. Fleet override is just read/write.
export type TenantWavePolicy = Partial<Record<'tag' | 'fleet', Partial<Record<WaveRW, WaveStrategy>>>>;
export type FleetWavePolicy = Partial<Record<WaveRW, WaveStrategy>>;

export interface TenantSettings {
  // The in-force value per key (tenant override merged over the platform default).
  settings: Record<TenantSettingKey, number>;
  // Only the keys the tenant has explicitly overridden (so the UI can show 'default').
  overrides: Partial<Record<TenantSettingKey, number>>;
  // Platform defaults + the [min, max] a tenant admin may set within.
  defaults: Record<TenantSettingKey, number>;
  bounds: Record<TenantSettingKey, [number, number]>;
  // Staged-rollout policy: {tag/fleet -> {read/write -> {mode, on_failure}}}.
  wave_policy: TenantWavePolicy;
}

export interface TenantUser {
  user_id: string;
  username: string;
  name?: string;
  role?: TenantRole;
  status?: 'ACTIVE' | 'REVOKED';
  must_reset_password?: boolean;
  last_login_at?: string;
  disabled_at?: string;
  created_at?: string;
  readwrite_agent_ids?: string[] | null;
  readonly_agent_ids?: string[] | null;
  readwrite_fleet_ids?: string[] | null;
  readonly_fleet_ids?: string[] | null;
}

// A user's full agent/fleet access scope. Lists partition by capability:
// readwrite_* = read+write, readonly_* = read-only. ["*"] = all. null = tenant-wide
// (admins only). A restricted user's access is the union; write is readwrite_* only.
export interface UserAccessScope {
  readwrite_agent_ids: string[] | null;
  readonly_agent_ids: string[] | null;
  readwrite_fleet_ids: string[] | null;
  readonly_fleet_ids: string[] | null;
}

export interface AgentHistory {
  history_id: string;
  agent_id: string;
  tenant_id: string;
  from_status?: string;
  to_status: string;
  triggered_by?: string;
  note?: string;
  created_at: string;
}

export interface ApiToken {
  token_id: string;
  name?: string;
  status?: 'ACTIVE' | 'REVOKED';
  created_at?: string;
  last_used_at?: string;
  revoked_at?: string;
  token?: string; // only on create
}

export interface AuditLog {
  log_id: string;
  tenant_id?: string;
  actor_id?: string;
  actor_name?: string;
  actor_role?: string;
  action: string;
  resource_type?: string;
  resource_id?: string;
  metadata?: Record<string, unknown>;
  ip_address?: string;
  created_at: string;
}

export interface User {
  user_id: string;
  tenant_id?: string;
  name: string;
  username?: string;
  role?: string;
  created_at?: string;
  status?: 'ACTIVE' | 'REVOKED';
  must_reset_password?: boolean;
  last_login_at?: string;
  token?: string;
  temp_password?: string;
  commands?: { cli_login?: string };
}

export interface K8sResourceRule {
  verbs: string[];
  api_groups?: string[];
  resources?: string[];
  resource_names?: string[];
}

export interface K8sNonResourceRule {
  verbs: string[];
  non_resource_urls?: string[];
}

// Rules effective only in one namespace, beyond the cluster-wide baseline.
export interface K8sNamespacePerms {
  namespace: string;
  resource_rules: K8sResourceRule[];
}

// The agent's effective RBAC, deduped: cluster-wide rules reported once, plus the
// extra rules bound in specific namespaces. Self-reported via SelfSubjectRulesReview.
export interface K8sPermissions {
  cluster_wide: K8sResourceRule[];
  non_resource_rules?: K8sNonResourceRule[];
  namespaces?: K8sNamespacePerms[];
  incomplete: boolean;  // a namespace review couldn't be fully evaluated
  truncated?: boolean;  // snapshot exceeded the size cap; some entries dropped
  hash: string;
}

export interface Agent {
  agent_id: string;
  tenant_id: string;
  status: 'CREATED' | 'ACTIVE' | 'INACTIVE' | 'REVOKED' | 'DELETED';
  hostname?: string;
  agent_version?: string;
  type?: 'k8s' | 'host';
  fleet_id?: string | null;
  k8s_permissions?: K8sPermissions;
  k8s_permissions_acked?: K8sPermissions | null;
  k8s_permissions_drift?: boolean;
  k8s_permissions_reported?: boolean;
  k8s_allowed_binaries?: string[] | null;
  landlock_status?: 'active' | 'unavailable' | 'unsupported' | null;   // host filesystem sandbox
  sandbox_ack?: boolean;   // admin acknowledged running readonly/approved without the sandbox
  mode: 'wild' | 'readonly' | 'approved';
  access_level: 'open' | 'elevated' | 'managed' | 'restricted';
  writable?: boolean;  // whether the requesting user may run write commands (read-only grant → false)
  claimed_at?: string;
  created_at?: string;
  token_issued_at?: string;
  last_heartbeat_at?: string;
  tags?: string[];
  running_as_root?: string;
  grant_service_mgmt?: boolean;
  grant_docker?: boolean;
  service_mgmt_detected?: boolean;
  docker_detected?: boolean;
  // Accepted fleet grant-mismatch exception (the fleet grant signature it was accepted
  // against); null/absent = no exception. See memberMismatchAccepted in utils.
  grants_exception?: string | null;
  install_token?: string;
  install_token_expires_at?: string;
  commands?: { agent?: string; cli_use?: string };
}

// A fleet: a reusable-join-token group of host agents. Any host that installs
// with the join token auto-enrolls, inheriting the fleet's mode/grants.
export interface Fleet {
  fleet_id: string;
  tenant_id: string;
  name: string;
  type: 'host';
  mode: 'wild' | 'readonly' | 'approved';
  grant_service_mgmt: boolean;
  grant_docker: boolean;
  sandbox_ack?: boolean;   // members run readonly/approved unsandboxed when they lack Landlock
  tags?: string[];
  status: 'ACTIVE' | 'REVOKED';
  reap_after_seconds?: number | null;
  // Per-fleet fan-out blast-radius ceiling (null = the tenant's fanout_cap; a set value
  // can't exceed it). Hard cap - the wave size for a fan-out.
  max_fanout?: number | null;
  // Advanced: fleet-level staged-rollout override ({read/write -> {mode, on_failure}});
  // null = inherit the tenant's fleet default.
  wave_policy?: FleetWavePolicy | null;
  created_at?: string;
  member_count?: number;
  // Per-fleet member stats from the fleet-list aggregation, so the console renders the
  // list (with the grant-mismatch badge) without loading every member.
  active_count?: number;
  inactive_count?: number;
  mismatch_count?: number;
  writable?: boolean;  // whether the requesting user may write to the fleet (read-only grant → false)
}

// Returned once on create / rotate: the raw join token + the launch-template line.
export interface FleetToken {
  fleet_id: string;
  join_token: string;
  install: string;
  previous_token_valid_until?: string | null;
}

export interface K8sRule {
  verb: string;
  resource: string;
  namespace: string;
  name: string;
}

// Structured host approval rule: a bin + positional args, each a literal or "*" wildcard.
export interface HostRule {
  bin: string;
  args: string[];
}

export interface Approval {
  approval_id: string;
  agent_id: string | null;
  agent_hostname?: string;
  agent_type?: 'k8s' | 'host';
  // Fleet-scoped approvals target a fleet instead of a standalone agent; exactly
  // one of agent_id / fleet_id is set. `scope` is set by the list enrichment.
  fleet_id?: string | null;
  fleet_name?: string | null;
  scope?: 'agent' | 'fleet';
  tenant_id: string;
  command: string;
  k8s_rule?: K8sRule | null;
  host_rule?: HostRule | null;
  status: 'pending' | 'approved' | 'denied' | 'expired';
  requested_by?: string;
  requester_name?: string;
  job_id?: string;
  expires_at?: string;
  created_at: string;
  reviewed_at?: string;
  reviewed_by?: string;
}

export interface Job {
  job_id: string;
  agent_id: string;
  agent_hostname?: string;
  agent_mode?: string;
  agent_fleet_id?: string | null;   // set when the job's agent is a fleet member
  run_id?: string | null;         // shared by all jobs from one fleet/tag fan-out
  wave?: number;                  // staged-rollout wave index (0 = first / non-staged)
  tenant_id: string;
  created_by: string;
  command: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'REJECTED' | 'EXPIRED' | 'HELD' | 'CANCELED';
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  stdout_truncated?: boolean;   // output was capped (agent-side and/or on ingest)
  stderr_truncated?: boolean;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

// A "run" = one fan-out (fleets exec / exec --tag), grouping the jobs it created by
// run_id. `tag` is set only for standalone tag runs (the tag the fan-out selected on).
export interface FleetRun {
  run_id: string;
  tag?: string | null;
  command: string;
  created_at: string;
  created_by?: string | null;
  state?: string;            // pending | running | succeeded | partial | failed
  members: number;
  ok: number;
  failed: number;
  pending: number;
}

// A run's full status (GET /tenant/runs/{run_id}) - counts plus the bounded who/why
// of members that were skipped, so it's clear why a host didn't run. There is no
// "capping": every eligible member runs, in waves of the fan-out cap.
export interface RunStatus {
  run_id: string;
  fleet_id?: string | null;
  tag?: string | null;
  command: string;
  state: string;
  counts: { ok: number; failed: number; pending: number; running: number };
  total: number;
  terminal: boolean;
  dispatched: number;
  skipped_count: number;
  skipped: { agent_id: string; hostname?: string | null; reason: string }[];
  failures: { agent_id: string; status?: string; exit_code?: number | null; stderr?: string }[];
  // Staged rollout: wave_total 1 (or null rollout) means the run is not staged.
  rollout?: { waves: number[]; mode: string; on_failure: string } | null;
  current_wave: number;
  wave_total: number;
  staged: number;   // jobs still HELD (later waves not yet released)
}

// Dry-run preview of a fleet/tag fan-out (POST .../jobs or /jobs/fanout with dry_run:true).
// Fields common to both scopes, plus scope-specific extras.
export interface FanoutPreview {
  dry_run: true;
  command: string;
  matched: number;
  wave_size: number;
  wave_strategy: string;   // 'auto' | 'manual'
  failure_policy: string;  // 'stop' | 'continue'
  wave_total: number;
  is_write: boolean;
  skipped: { agent_id: string; hostname?: string | null; reason: string }[];
  // fleet scope
  fleet_id?: string;
  fleet_name?: string;
  mode?: string;
  approval_required?: boolean;
  // tag scope
  tag?: string;
  type?: string;
}

// Dry-run preview of a single-agent job (POST /jobs with dry_run:true).
export interface JobPreview {
  dry_run: true;
  agent_id: string;
  hostname?: string;
  command: string;
  mode: string;
  type?: string;   // 'host' | 'k8s' - host is_write is heuristic; k8s is authoritative
  is_write: boolean;
  approval_required: boolean;
}

// Result of a real (non-dry-run) fan-out dispatch.
export interface FanoutResult {
  command: string;
  run_id: string | null;
  dispatched: number;
  total: number;
  wave_total: number;
  jobs: { agent_id: string; hostname?: string; job_id: string; status: string; wave: number }[];
  skipped: { agent_id: string; hostname?: string | null; reason: string }[];
  fleet_id?: string;
  fleet_name?: string;
  tag?: string;
  type?: string;
}

export interface UserAgents {
  user_id: string;
  readwrite_agent_ids: string[];
}
