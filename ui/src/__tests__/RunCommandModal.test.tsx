import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RunCommandModal } from '../components/RunCommandModal';
import type { TenantConfig, Agent, Fleet } from '../types';
import * as api from '../api';

const CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com', tenantToken: 'tok', tenantId: 'tenant_1',
  tenantName: 'Acme', role: 'operator',
} as TenantConfig;

const AGENT: Agent = {
  agent_id: 'agent_1', tenant_id: 'tenant_1', status: 'ACTIVE', hostname: 'web-01',
  mode: 'wild', access_level: 'open', writable: true,
} as Agent;

const FLEET: Fleet = {
  fleet_id: 'fleet_1', tenant_id: 'tenant_1', name: 'web-tier', type: 'host',
  mode: 'wild', grant_service_mgmt: false, grant_docker: false, status: 'ACTIVE', writable: true,
} as Fleet;

beforeEach(() => { vi.restoreAllMocks(); });

describe('RunCommandModal - single agent', () => {
  it('previews (dry-run), dispatches, then polls the job to its result', async () => {
    const spy = vi.spyOn(api, 'createJob')
      .mockResolvedValueOnce({ dry_run: true, agent_id: 'agent_1', hostname: 'web-01',
        command: 'rm -rf /tmp/x', mode: 'wild', is_write: true, approval_required: false } as never)
      .mockResolvedValueOnce({ job_id: 'job_abc', status: 'PENDING' } as never);
    // The modal polls getJob until terminal - RUNNING first, then SUCCEEDED with output.
    const getJobSpy = vi.spyOn(api, 'getJob')
      .mockResolvedValueOnce({ job_id: 'job_abc', status: 'RUNNING' } as never)
      .mockResolvedValueOnce({ job_id: 'job_abc', status: 'SUCCEEDED', exit_code: 0, stdout: 'removed' } as never);
    render(<RunCommandModal config={CONFIG} target={{ kind: 'agent', agent: AGENT }} onClose={() => {}} />);

    await userEvent.type(screen.getByPlaceholderText('uptime'), 'rm -rf /tmp/x');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));

    // Preview classifies the command; a write is flagged before it runs.
    expect(await screen.findByText('Confirm & run')).toBeInTheDocument();
    expect(screen.getAllByText('write').length).toBeGreaterThan(0);
    expect(spy).toHaveBeenNthCalledWith(1, CONFIG.apiUrl, CONFIG.tenantToken, 'agent_1', 'rm -rf /tmp/x', { dry_run: true });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm & run' }));
    await waitFor(() => expect(spy).toHaveBeenNthCalledWith(2, CONFIG.apiUrl, CONFIG.tenantToken, 'agent_1', 'rm -rf /tmp/x'));
    expect(await screen.findByText('job_abc')).toBeInTheDocument();
    // Polls to the terminal result and shows exit code + stdout inline.
    expect(await screen.findByText('Succeeded', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText('removed')).toBeInTheDocument();
    expect(getJobSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'job_abc');
  });

  it('flags the host heuristic and warns on a wild-mode write', async () => {
    vi.spyOn(api, 'createJob').mockResolvedValueOnce({ dry_run: true, agent_id: 'agent_1',
      hostname: 'web-01', command: 'rm -rf /tmp/x', mode: 'wild', type: 'host',
      is_write: true, approval_required: false } as never);
    render(<RunCommandModal config={CONFIG} target={{ kind: 'agent', agent: AGENT }} onClose={() => {}} />);
    await userEvent.type(screen.getByPlaceholderText('uptime'), 'rm -rf /tmp/x');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(/best-effort \(host heuristic\)/)).toBeInTheDocument();
    expect(screen.getByText(/Wild mode/)).toBeInTheDocument();
  });
});

describe('RunCommandModal - fleet fan-out', () => {
  const PREVIEW = {
    dry_run: true as const, command: 'systemctl restart nginx', matched: 4, wave_size: 2,
    wave_strategy: 'manual', failure_policy: 'stop', wave_total: 2, is_write: true, skipped: [],
    fleet_id: 'fleet_1', fleet_name: 'web-tier',
  };

  it('previews (dry-run) then dispatches on confirm', async () => {
    const spy = vi.spyOn(api, 'fleetFanout')
      .mockResolvedValueOnce(PREVIEW as never)
      .mockResolvedValueOnce({ command: 'systemctl restart nginx', run_id: 'run_1', dispatched: 2,
        total: 4, wave_total: 2, jobs: [], skipped: [], fleet_id: 'fleet_1' } as never);
    render(<RunCommandModal config={CONFIG} target={{ kind: 'fleet', fleet: FLEET }} onClose={() => {}} />);

    await userEvent.type(screen.getByPlaceholderText('uptime'), 'systemctl restart nginx');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));

    // Preview phase shows the blast radius + wave plan.
    expect(await screen.findByText('Confirm & run')).toBeInTheDocument();
    expect(screen.getByText('MANUAL')).toBeInTheDocument();
    expect(screen.getByText(/child jobs, released/)).toBeInTheDocument();
    expect(spy).toHaveBeenNthCalledWith(1, CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1',
      { command: 'systemctl restart nginx', max_targets: undefined, dry_run: true });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm & run' }));
    await waitFor(() => expect(spy).toHaveBeenNthCalledWith(2, CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1',
      { command: 'systemctl restart nginx', max_targets: undefined }));
    expect(await screen.findByText('run_1')).toBeInTheDocument();
  });

  it('disables Confirm when the preview matches zero agents', async () => {
    vi.spyOn(api, 'fleetFanout').mockResolvedValue({ ...PREVIEW, matched: 0 } as never);
    render(<RunCommandModal config={CONFIG} target={{ kind: 'fleet', fleet: FLEET }} onClose={() => {}} />);
    await userEvent.type(screen.getByPlaceholderText('uptime'), 'uptime');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(/No agents match/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm & run' })).toBeDisabled();
  });
});

describe('RunCommandModal - pooled targets (launcher)', () => {
  it('agent-pick lists writable agents (inactive disabled) and runs the chosen active one', async () => {
    const spy = vi.spyOn(api, 'createJob')
      .mockResolvedValueOnce({ dry_run: true, agent_id: 'agent_2', hostname: 'web-02',
        command: 'uptime', mode: 'wild', is_write: false, approval_required: false } as never)
      .mockResolvedValueOnce({ job_id: 'job_x', status: 'PENDING' } as never);
    const agents = [
      AGENT,
      { ...AGENT, agent_id: 'agent_2', hostname: 'web-02' } as Agent,
      { ...AGENT, agent_id: 'agent_ro', hostname: 'ro', writable: false } as Agent,      // excluded (read-only)
      { ...AGENT, agent_id: 'agent_off', hostname: 'off', status: 'INACTIVE' } as Agent, // shown, disabled
    ];
    render(<RunCommandModal config={CONFIG} target={{ kind: 'agent-pick', agents }} onClose={() => {}} />);

    const select = screen.getByRole('combobox');
    const opts = Array.from(select.querySelectorAll('option')) as HTMLOptionElement[];
    const byVal = Object.fromEntries(opts.map(o => [o.value, o]));
    // Read-only agent is excluded; the inactive agent is present but disabled.
    expect(Object.keys(byVal).sort()).toEqual(['agent_1', 'agent_2', 'agent_off']);
    expect(byVal['agent_off'].disabled).toBe(true);
    expect(byVal['agent_2'].disabled).toBe(false);

    fireEvent.change(select, { target: { value: 'agent_2' } });
    await userEvent.type(screen.getByPlaceholderText('uptime'), 'uptime');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm & run' }));
    await waitFor(() => expect(spy).toHaveBeenNthCalledWith(2, CONFIG.apiUrl, CONFIG.tenantToken, 'agent_2', 'uptime'));
  });

  it('fleet-pick previews the chosen fleet as a fan-out', async () => {
    const spy = vi.spyOn(api, 'fleetFanout').mockResolvedValue({
      dry_run: true, command: 'uptime', matched: 2, wave_size: 2, wave_strategy: 'auto',
      failure_policy: 'continue', wave_total: 1, is_write: false, skipped: [], fleet_id: 'fleet_1',
    } as never);
    render(<RunCommandModal config={CONFIG} target={{ kind: 'fleet-pick', fleets: [FLEET] }} onClose={() => {}} />);

    await userEvent.type(screen.getByPlaceholderText('uptime'), 'uptime');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1',
      { command: 'uptime', max_targets: undefined, dry_run: true }));
    expect(await screen.findByText('Confirm & run')).toBeInTheDocument();
  });
});

describe('RunCommandModal - tag fan-out', () => {
  it('sends the selected tag and previews before dispatch', async () => {
    const spy = vi.spyOn(api, 'fanoutByTag').mockResolvedValue({
      dry_run: true, command: 'uptime', matched: 3, wave_size: 3, wave_strategy: 'auto',
      failure_policy: 'continue', wave_total: 1, is_write: false, skipped: [], tag: 'env:prod', type: 'host',
    } as never);
    render(<RunCommandModal config={CONFIG} target={{ kind: 'tag', tags: ['env:prod', 'env:dev'] }} onClose={() => {}} />);

    await userEvent.type(screen.getByPlaceholderText('uptime'), 'uptime');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken,
      { tag: 'env:prod', command: 'uptime', type: undefined, dry_run: true }));
    expect(await screen.findByText('read')).toBeInTheDocument();
  });
});
