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
  allowed_agent_ids?: string[] | null;
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

export interface Agent {
  agent_id: string;
  tenant_id: string;
  status: 'CREATED' | 'ACTIVE' | 'INACTIVE' | 'REVOKED' | 'DELETED';
  hostname?: string;
  agent_version?: string;
  mode: 'wild' | 'readonly' | 'approved';
  access_level: 'open' | 'elevated' | 'managed' | 'restricted';
  claimed_at?: string;
  token_issued_at?: string;
  last_heartbeat_at?: string;
  tags?: string[];
  running_as_root?: string;
  grant_service_mgmt?: boolean;
  grant_docker?: boolean;
  service_mgmt_detected?: boolean;
  docker_detected?: boolean;
  install_token?: string;
  install_token_expires_at?: string;
  commands?: { agent?: string; cli_use?: string };
}

export interface Approval {
  approval_id: string;
  agent_id: string;
  agent_hostname?: string;
  tenant_id: string;
  command: string;
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
  tenant_id: string;
  created_by: string;
  command: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'REJECTED' | 'EXPIRED';
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

export interface UserAgents {
  user_id: string;
  allowed_agent_ids: string[];
}
