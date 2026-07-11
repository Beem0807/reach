import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

function mockApis({ jobs = [] as Job[], agents = [AGENT], fleets = [] as any[], runs = [] as any[], tagRuns = [] as any[] } = {}) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents });
  vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets, default_reap_after_seconds: 1800 });
  const runsSpy = vi.spyOn(api, 'listFleetRuns').mockResolvedValue({ fleet_id: 'fleet_1', runs });
  const tagRunsSpy = vi.spyOn(api, 'listTagRuns').mockResolvedValue({ runs: tagRuns });
  const jobsSpy = vi.spyOn(api, 'listTenantJobs').mockResolvedValue({ jobs });
  return { jobsSpy, runsSpy, tagRunsSpy };
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

  it('filters by agent on selection', async () => {
    const { jobsSpy } = mockApis({ jobs: [JOB] });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByText('docker ps');

    // combobox[0] = fleet filter, combobox[1] = agent filter (jobs scope, no fleet).
    const combos = screen.getAllByRole('combobox');
    fireEvent.change(combos[1], { target: { value: 'agent_1' } });

    await waitFor(() =>
      expect(jobsSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, { agent_id: 'agent_1' }),
    );
  });

  it('filters jobs by fleet', async () => {
    const { jobsSpy } = mockApis({ jobs: [JOB], fleets: [{ fleet_id: 'fleet_1', name: 'web-asg' }] });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByText('docker ps');
    const combos = screen.getAllByRole('combobox');
    fireEvent.change(combos[0], { target: { value: 'fleet_1' } });  // fleet filter
    await waitFor(() =>
      expect(jobsSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, { fleet_id: 'fleet_1' }),
    );
  });

  it('pages forward and back with the created_at cursor', async () => {
    const JOB2: Job = { ...JOB, job_id: 'job_2', command: 'uptime' };
    const spy = vi.spyOn(api, 'listTenantJobs').mockImplementation((_u, _t, params = {}) =>
      params.cursor === 'cur1'
        ? Promise.resolve({ jobs: [JOB2] })                         // page 2, last page
        : Promise.resolve({ jobs: [JOB], next_cursor: 'cur1' }));    // page 1, more to come
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [AGENT] });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByText('docker ps');
    expect(screen.getByText('Page 1')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ cursor: 'cur1' })));
    await screen.findByText('uptime');
    expect(screen.getByText('Page 2')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Prev'));
    await screen.findByText('docker ps');
    expect(screen.getByText('Page 1')).toBeInTheDocument();
  });

  it('command search fires the API only when Search is clicked', async () => {
    const { jobsSpy } = mockApis({ jobs: [JOB] });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByText('docker ps');

    // Typing alone must NOT trigger a request with q.
    fireEvent.change(screen.getByPlaceholderText('Search command…'), { target: { value: 'docker' } });
    expect(jobsSpy).not.toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ q: 'docker' }),
    );

    // Clicking Search applies the query.
    fireEvent.click(screen.getByText('Search'));
    await waitFor(() =>
      expect(jobsSpy).toHaveBeenCalledWith(
        CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ q: 'docker' }),
      ),
    );
  });

  it('Fleet runs scope shows runs for the selected fleet', async () => {
    const { runsSpy } = mockApis({
      fleets: [{ fleet_id: 'fleet_1', name: 'web-asg' }],
      runs: [{ run_id: 'batch_a', command: 'systemctl restart app', created_at: '2026-06-20T10:00:00Z', members: 3, ok: 2, failed: 1, pending: 0 }],
    });
    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(screen.getByRole('button', { name: 'Fleet runs' }));
    // prompt to pick a fleet until one is chosen
    expect(await screen.findByText(/Pick a fleet/)).toBeInTheDocument();
    const combos = screen.getAllByRole('combobox');
    fireEvent.change(combos[0], { target: { value: 'fleet_1' } });
    expect(await screen.findByText('systemctl restart app')).toBeInTheDocument();
    expect(runsSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1', {});
  });

  it('Tag runs scope lists standalone fan-out runs (no fleet needed)', async () => {
    const { tagRunsSpy } = mockApis({
      tagRuns: [{ run_id: 'batch_t', tag: 'env:prod', command: 'systemctl status nginx', created_at: '2026-06-20T10:00:00Z', members: 2, ok: 1, failed: 1, pending: 0 }],
    });
    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(screen.getByRole('button', { name: 'Tag runs' }));
    expect(await screen.findByText('systemctl status nginx')).toBeInTheDocument();
    expect(screen.getByText('env:prod')).toBeInTheDocument();  // tag column
    expect(tagRunsSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, {});
  });

  it('opens the job detail modal when a row is clicked', async () => {
    mockApis({ jobs: [JOB] });
    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(await screen.findByText('docker ps'));
    // Modal renders stdout output.
    expect(await screen.findByText('CONTAINER ID')).toBeInTheDocument();
    expect(screen.getByText('stdout')).toBeInTheDocument();
  });

  it('a job opened from a run has a Back-to-wave-view button that reopens the run', async () => {
    const run = { run_id: 'run_x', command: 'deploy.sh', created_at: '2026-06-20T10:00:00Z',
                  tag: null, state: 'succeeded', members: 1, ok: 1, failed: 0, pending: 0 };
    const member: Job = { ...JOB, job_id: 'job_m', command: 'deploy.sh', status: 'SUCCEEDED',
                          agent_hostname: 'prod-web-07', wave: 0, run_id: 'run_x' };
    mockApis({ fleets: [{ fleet_id: 'fleet_1', name: 'web-asg' }], runs: [run] });
    vi.spyOn(api, 'getRun').mockResolvedValue({
      run_id: 'run_x', command: 'deploy.sh', state: 'succeeded',
      counts: { ok: 1, failed: 0, pending: 0, running: 0 }, total: 1, terminal: true,
      dispatched: 1, skipped_count: 0, skipped: [], failures: [],
      rollout: { waves: [1], mode: 'auto', on_failure: 'stop' }, current_wave: 0, wave_total: 1, staged: 0,
    } as any);
    vi.spyOn(api, 'listTenantJobs').mockResolvedValue({ jobs: [member] });

    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(screen.getByRole('button', { name: 'Fleet runs' }));
    expect(await screen.findByText(/Pick a fleet/)).toBeInTheDocument();
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'fleet_1' } });
    // Open the run (wave view), then a member within it.
    fireEvent.click(await screen.findByText('deploy.sh'));   // run row
    fireEvent.click(await screen.findByText('prod-web-07')); // member in the wave table
    // Job detail modal is up (wave-info bar gone); it has a Back-to-wave-view control.
    const back = await screen.findByTitle('Back to wave view');
    await waitFor(() => expect(screen.queryByText('Wave size')).toBeNull());
    fireEvent.click(back);
    // The run's wave view is back.
    expect(await screen.findByText('Wave size')).toBeInTheDocument();
  });

  it('surfaces an error when job loading fails', async () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listTenantJobs').mockRejectedValue(new Error('boom'));
    render(<TenantJobsPage config={CONFIG} />);
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});

describe('TenantJobsPage - create job / new run launcher', () => {
  const WRITABLE_AGENT: Agent = { ...AGENT, writable: true };

  it('always shows Create job; the picker reports when there are no writable agents', async () => {
    mockApis({ agents: [AGENT] });   // AGENT has no writable flag
    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(await screen.findByRole('button', { name: /Create job/ }));
    // Modal opens even with nothing runnable; the picker says so and Preview is disabled.
    expect(await screen.findByRole('option', { name: /no writable agents/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
  });

  it('shows inactive writable agents in the picker (disabled, not hidden)', async () => {
    const inactive: Agent = { ...WRITABLE_AGENT, agent_id: 'agent_off', hostname: 'off-01', status: 'INACTIVE' };
    mockApis({ agents: [inactive] });
    render(<TenantJobsPage config={CONFIG} />);
    fireEvent.click(await screen.findByRole('button', { name: /Create job/ }));
    const opt = await screen.findByRole('option', { name: /off-01.*inactive/ }) as HTMLOptionElement;
    expect(opt.disabled).toBe(true);
  });

  it('shows Create job and dispatches to the picked agent after preview + confirm', async () => {
    mockApis({ agents: [WRITABLE_AGENT] });
    const spy = vi.spyOn(api, 'createJob')
      .mockResolvedValueOnce({ dry_run: true, agent_id: 'agent_1', hostname: 'web-01.local',
        command: 'uptime', mode: 'wild', is_write: false, approval_required: false } as never)
      .mockResolvedValueOnce({ job_id: 'job_z', status: 'PENDING' } as never);
    render(<TenantJobsPage config={CONFIG} />);

    fireEvent.click(await screen.findByRole('button', { name: /Create job/ }));
    await userEvent.type(await screen.findByPlaceholderText('uptime'), 'uptime');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm & run' }));
    await waitFor(() => expect(spy).toHaveBeenNthCalledWith(
      2, CONFIG.apiUrl, CONFIG.tenantToken, 'agent_1', 'uptime'));
  });

  it('switches to "New run" label on the Fleet runs tab', async () => {
    mockApis({ agents: [WRITABLE_AGENT], fleets: [
      { fleet_id: 'fleet_1', tenant_id: 'tenant_1', name: 'web-tier', type: 'host', mode: 'wild',
        grant_service_mgmt: false, grant_docker: false, status: 'ACTIVE', writable: true }] });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByRole('button', { name: /Create job/ });   // jobs tab default
    fireEvent.click(screen.getByRole('button', { name: 'Fleet runs' }));
    expect(await screen.findByRole('button', { name: /New run/ })).toBeInTheDocument();
  });

  it('offers "New run" on the Tag runs tab when standalone agents have tags', async () => {
    // Tags are derived from standalone (non-fleet) agents, not the all_tags facet.
    const tagged: Agent = { ...WRITABLE_AGENT, fleet_id: null, tags: ['env:prod'] };
    mockApis({ agents: [tagged] });
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByRole('button', { name: /Create job/ });
    fireEvent.click(screen.getByRole('button', { name: 'Tag runs' }));

    fireEvent.click(await screen.findByRole('button', { name: /New run/ }));
    // The tag dropdown is populated from the standalone agent's tag.
    expect(await screen.findByRole('option', { name: 'env:prod' })).toBeInTheDocument();
  });

  it('always shows "New run" on the Tag runs tab; picker reports when no tags exist', async () => {
    mockApis({ agents: [WRITABLE_AGENT] });   // no tags
    render(<TenantJobsPage config={CONFIG} />);
    await screen.findByRole('button', { name: /Create job/ });
    fireEvent.click(screen.getByRole('button', { name: 'Tag runs' }));
    fireEvent.click(await screen.findByRole('button', { name: /New run/ }));
    expect(await screen.findByRole('option', { name: /no tags/ })).toBeInTheDocument();
  });
});
