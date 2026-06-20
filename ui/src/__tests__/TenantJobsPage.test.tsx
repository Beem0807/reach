import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TenantJobsPage } from '../pages/TenantJobsPage';
import type { TenantConfig, Job, Agent } from '../types';
import * as api from '../api';

const CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok_test',
  tenantId: 'tenant_1',
  tenantName: 'Acme',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice',
  role: 'admin',
  mustResetPassword: false,
};

const AGENT: Agent = {
  agent_id: 'agent_1',
  tenant_id: 'tenant_1',
  status: 'ACTIVE',
  hostname: 'web-01.local',
  mode: 'wild',
  access_level: 'open',
  tags: [],
  grant_docker: false,
  grant_service_mgmt: false,
};

const JOB: Job = {
  job_id: 'job_1',
  agent_id: 'agent_1',
  agent_hostname: 'web-01.local',
  agent_mode: 'wild',
  tenant_id: 'tenant_1',
  created_by: 'user_1',
  command: 'docker ps',
  status: 'SUCCEEDED',
  exit_code: 0,
  stdout: 'CONTAINER ID',
  created_at: '2026-06-20T10:00:00Z',
  started_at: '2026-06-20T10:00:00Z',
  completed_at: '2026-06-20T10:00:01Z',
};

function mockApis({ jobs = [] as Job[], agents = [AGENT] } = {}) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents });
  const jobsSpy = vi.spyOn(api, 'listTenantJobs').mockResolvedValue({ jobs });
  return { jobsSpy };
}

beforeEach(() => { vi.restoreAllMocks(); });

describe('TenantJobsPage', () => {
  it('renders a job row', async () => {
    mockApis({ jobs: [JOB] });
    render(<TenantJobsPage config={CONFIG} />);
    expect(await screen.findByText('docker ps')).toBeInTheDocument();
  });

  it('shows status counts in the header', async () => {
    mockApis({
      jobs: [
        JOB,
        { ...JOB, job_id: 'job_2', status: 'RUNNING' },
        { ...JOB, job_id: 'job_3', status: 'FAILED' },
      ],
    });
    render(<TenantJobsPage config={CONFIG} />);
    expect(await screen.findByText('1 running')).toBeInTheDocument();
    expect(screen.getByText('1 completed')).toBeInTheDocument();
    expect(screen.getByText('1 failed')).toBeInTheDocument();
  });

  it('shows empty fallback when there are no jobs', async () => {
    mockApis({ jobs: [] });
    render(<TenantJobsPage config={CONFIG} />);
    expect(await screen.findByText('No jobs found')).toBeInTheDocument();
  });

  it('filters by agent when Search is clicked', async () => {
    const { jobsSpy } = mockApis({ jobs: [JOB] });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByText('docker ps');

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'agent_1' } });
    fireEvent.click(screen.getByRole('button', { name: /Search/ }));

    await waitFor(() =>
      expect(jobsSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, { agent_id: 'agent_1' }),
    );
  });

  it('opens the job detail modal when a row is clicked', async () => {
    mockApis({ jobs: [JOB] });
    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(await screen.findByText('docker ps'));
    // Modal renders stdout output.
    expect(await screen.findByText('CONTAINER ID')).toBeInTheDocument();
    expect(screen.getByText('stdout')).toBeInTheDocument();
  });

  it('surfaces an error when job loading fails', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listTenantJobs').mockRejectedValue(new Error('boom'));
    render(<TenantJobsPage config={CONFIG} />);
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});
