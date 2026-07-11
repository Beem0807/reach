import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FleetsPage } from '../pages/FleetsPage';
import type { TenantConfig, Agent, Fleet } from '../types';
import * as api from '../api';
import { memberMismatchFlagged } from '../utils';

const CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com', tenantToken: 'tok_test', tenantId: 'tenant_1',
  tenantName: 'Acme', userId: 'user_1', username: 'alice', name: 'Alice',
  role: 'admin', mustResetPassword: false,
};

const FLEET: Fleet = {
  fleet_id: 'fleet_1', tenant_id: 'tenant_1', name: 'web-asg', type: 'host',
  mode: 'approved', grant_service_mgmt: true, grant_docker: true, tags: [],
  status: 'ACTIVE', reap_after_seconds: 1800, created_at: '2026-01-01T00:00:00Z',
};

function member(id: string, over: Partial<Agent> = {}): Agent {
  return {
    agent_id: id, tenant_id: 'tenant_1', status: 'ACTIVE', hostname: id,
    mode: 'approved', access_level: 'open', tags: [], fleet_id: 'fleet_1',
    grant_service_mgmt: true, grant_docker: true, ...over,
  };
}

// Members belong to the first fleet; the fleet list carries the aggregate counts the
// backend computes (collapsed rows render from these), and listFleetAgents lazily
// returns the members when a fleet is expanded / its detail opened.
function mockApis(agents: Agent[], fleets: Fleet[] = [FLEET]) {
  const withCounts = fleets.map(f => {
    const mine = agents.filter(a => a.fleet_id === f.fleet_id && a.status !== 'DELETED');
    return {
      ...f,
      member_count: mine.length,
      active_count: mine.filter(a => a.status === 'ACTIVE').length,
      inactive_count: mine.filter(a => a.status === 'INACTIVE').length,
      mismatch_count: mine.filter(a => a.status !== 'REVOKED' && memberMismatchFlagged(a, f)).length,
    };
  });
  // Server-side: honour the q (name/id substring) and limit/offset params.
  const fleetsSpy = vi.spyOn(api, 'listFleets').mockImplementation((_u, _t, params = {}) => {
    const q = (params.q ?? '').toLowerCase();
    const matched = q
      ? withCounts.filter(f => (f.name ?? '').toLowerCase().includes(q) || f.fleet_id.toLowerCase().includes(q))
      : withCounts;
    const off = Number(params.offset ?? 0);
    const lim = params.limit ? Number(params.limit) : matched.length;
    return Promise.resolve({
      fleets: matched.slice(off, off + lim), default_reap_after_seconds: 1800, total: matched.length,
    });
  });
  vi.spyOn(api, 'listFleetAgents').mockImplementation((_u, _t, fleetId) =>
    Promise.resolve({ fleet_id: fleetId, agents: agents.filter(a => a.fleet_id === fleetId) }));
  const ackSpy = vi.spyOn(api, 'reconcileFleetGrants').mockResolvedValue({
    fleet_id: 'fleet_1', reconciled: 1, blocked: [], grant_service_mgmt: true, grant_docker: true,
  });
  const acceptSpy = vi.spyOn(api, 'acceptFleetGrantMismatch').mockResolvedValue({ fleet_id: 'fleet_1', accepted: 1 });
  const updateSpy = vi.spyOn(api, 'updateFleet').mockResolvedValue(FLEET);
  const rotateSpy = vi.spyOn(api, 'rotateFleetToken').mockResolvedValue({
    fleet_id: 'fleet_1', join_token: 'fleet_newtok', install: 'curl ... | bash', previous_token_valid_until: null,
  });
  return { fleetsSpy, ackSpy, acceptSpy, updateSpy, rotateSpy };
}

beforeEach(() => { vi.restoreAllMocks(); try { localStorage.clear(); } catch { /* jsdom */ } });

describe('FleetsPage grant mismatch', () => {
  it('shows no mismatch when members match the fleet grants', async () => {
    mockApis([member('a1')]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    expect(screen.queryByText(/grant mismatch/)).not.toBeInTheDocument();
  });

  it('name search filters the list only when Search is clicked', async () => {
    const other: Fleet = { ...FLEET, fleet_id: 'fleet_2', name: 'db-asg' };
    mockApis([], [FLEET, other]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    expect(screen.getByText('db-asg')).toBeInTheDocument();

    // Typing alone does not filter.
    fireEvent.change(screen.getByPlaceholderText('Search fleets…'), { target: { value: 'web' } });
    expect(screen.getByText('db-asg')).toBeInTheDocument();

    // Clicking Search applies the filter.
    fireEvent.click(screen.getByText('Search'));
    await waitFor(() => expect(screen.queryByText('db-asg')).not.toBeInTheDocument());
    expect(screen.getByText('web-asg')).toBeInTheDocument();
  });

  it('pages the fleet list forward with Next', async () => {
    const many: Fleet[] = Array.from({ length: 25 }, (_, i) => ({
      ...FLEET, fleet_id: `fleet_${i}`, name: `asg-${String(i).padStart(2, '0')}`,
    }));
    const { fleetsSpy: spy } = mockApis([], many);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('asg-00');
    expect(screen.getByText(/Showing 1–20 of 25/)).toBeInTheDocument();
    expect(screen.queryByText('asg-20')).not.toBeInTheDocument();  // on page 2

    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ offset: '20' })));
    await screen.findByText('asg-20');
  });

  it('flags mismatch and reconciles all', async () => {
    // a2 enrolled without docker; the fleet now wants docker -> mismatch.
    const { ackSpy } = mockApis([member('a1'), member('a2', { grant_docker: false })]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    // Members-column mismatch chip.
    expect(screen.getByText(/1 grant mismatch/)).toBeInTheDocument();

    // Expand the fleet to reveal the mismatch banner + reconcile-all button.
    fireEvent.click(screen.getByText('web-asg').closest('tr')!);
    const ackBtn = await screen.findByRole('button', { name: /Reconcile all/ });
    fireEvent.click(ackBtn);

    // Confirm in the modal (whole-fleet).
    const confirm = await screen.findByRole('button', { name: /Reconcile 1 member/ });
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(ackSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1', undefined),
    );
  });

  it('reconciles a single member from its row', async () => {
    const { ackSpy } = mockApis([member('a1'), member('a2', { grant_docker: false })]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    // Expand to reveal member rows; the mismatched member has a per-row "reconcile".
    fireEvent.click(screen.getByText('web-asg').closest('tr')!);
    const rowAck = await screen.findByRole('button', { name: 'reconcile' });
    fireEvent.click(rowAck);
    // Modal targets just that agent (a2).
    const confirm = await screen.findByRole('button', { name: /Reconcile a2/ });
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(ackSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1', 'a2'),
    );
  });

  it('accepts a member mismatch as-is (keeps grants, stops flagging)', async () => {
    const { acceptSpy } = mockApis([member('a1'), member('a2', { grant_docker: false })]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    fireEvent.click(screen.getByText('web-asg').closest('tr')!);
    fireEvent.click(await screen.findByRole('button', { name: 'reconcile' }));   // opens the resolve modal
    fireEvent.click(await screen.findByRole('button', { name: 'Accept as-is' }));
    await waitFor(() =>
      expect(acceptSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1', 'a2'),
    );
  });

  it('does not flag a member whose mismatch is accepted (signature matches)', async () => {
    // FLEET wants sm+dk. a2 has sm on, dk off -> signature "10-11"; accepted for it.
    mockApis([member('a1'), member('a2', { grant_docker: false, grants_exception: '10-11' })]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    expect(screen.queryByText(/grant mismatch/)).not.toBeInTheDocument();
  });

  it('does not flag a member that now matches the fleet, even with a stale exception', async () => {
    // a1 matches FLEET (both grants on) but carries a leftover exception from an earlier
    // divergence. No mismatch -> not flagged; the exception is dormant.
    mockApis([member('a1', { grants_exception: '00-00' })]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    expect(screen.queryByText(/grant mismatch/)).not.toBeInTheDocument();
  });

  it('re-flags an accepted member after its own grants change to a new divergence', async () => {
    // Scenario: fleet grants nothing. A host gained service-mgmt and was accepted
    // (signature "10-00"). Later the host also gained docker -> its grants are now
    // "11", the current signature is "11-00" != the stored "10-00", so it re-flags.
    const bareFleet: Fleet = { ...FLEET, grant_service_mgmt: false, grant_docker: false };
    const drifted = member('a2', { grant_service_mgmt: true, grant_docker: true, grants_exception: '10-00' });
    mockApis([drifted], [bareFleet]);
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    expect(screen.getByText(/1 grant mismatch/)).toBeInTheDocument();  // came back out of the exception
  });

  it('keeps the modal open and shows blocked hosts when nothing could be reconciled', async () => {
    mockApis([member('a1'), member('a2', { grant_docker: false })]);
    vi.spyOn(api, 'reconcileFleetGrants').mockResolvedValue({
      fleet_id: 'fleet_1', reconciled: 0,
      blocked: [{ agent_id: 'a2', hostname: 'a2', reason: 'host does not report docker yet - re-provision it first' }],
      grant_service_mgmt: true, grant_docker: true,
    });
    render(<FleetsPage config={CONFIG} />);
    await screen.findByText('web-asg');
    fireEvent.click(screen.getByText('web-asg').closest('tr')!);
    fireEvent.click(await screen.findByRole('button', { name: /Reconcile all/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Reconcile 1 member/ }));
    // Modal stays open and explains why the host was skipped.
    expect(await screen.findByText(/does not report docker/)).toBeInTheDocument();
  });
});

describe('FleetsPage role gating', () => {
  const DEV: TenantConfig = { ...CONFIG, role: 'developer' };

  it('developer gets a read-only view (no write actions)', async () => {
    // Mismatched member so an operator would see reconcile affordances.
    mockApis([member('a1'), member('a2', { grant_docker: false })]);
    render(<FleetsPage config={DEV} />);
    await screen.findByText('web-asg');
    // No "New fleet" for developers.
    expect(screen.queryByRole('button', { name: /New fleet/ })).not.toBeInTheDocument();
    // Mismatch is still visible (read), but the per-row reconcile action is not.
    fireEvent.click(screen.getByText('web-asg').closest('tr')!);
    expect(await screen.findByText(/1 grant mismatch/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'reconcile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Reconcile all/ })).not.toBeInTheDocument();
  });

  it('operator sees write actions', async () => {
    mockApis([member('a1')]);
    render(<FleetsPage config={{ ...CONFIG, role: 'operator' }} />);
    await screen.findByText('web-asg');
    expect(screen.getByRole('button', { name: /New fleet/ })).toBeInTheDocument();
  });

  it('read-only-granted fleet (writable=false) hides write actions for an operator', async () => {
    mockApis([member('a1')], [{ ...FLEET, writable: false }]);
    render(<FleetsPage config={{ ...CONFIG, role: 'operator' }} />);
    await screen.findByText('web-asg');
    // Open the kebab menu - it should offer only "View details".
    fireEvent.click(screen.getByText('web-asg').closest('tr')!.querySelector('button')!);
    // "New fleet" is not fleet-scoped, so it still shows for an operator; the fleet's
    // own write items should not appear in its menu.
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
  });
});

describe('FleetsPage grant edit -> rotate gating', () => {
  async function openEdit() {
    // Open the fleet detail modal (click the name), then its Edit button.
    fireEvent.click(await screen.findByRole('button', { name: 'web-asg' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    // The detail modal closes then opens Edit after a short timeout.
    return screen.findByText('Host grants');
  }
  const dockerCheckbox = () =>
    screen.getByText('Docker access').closest('label')!.querySelector('input') as HTMLInputElement;

  it('a grant change is not saved until the rotate step is confirmed', async () => {
    const { updateSpy, rotateSpy } = mockApis([member('a1')]);
    render(<FleetsPage config={CONFIG} />);
    await openEdit();

    // Turn docker OFF (fleet had it on) -> grants changed.
    fireEvent.click(dockerCheckbox());
    // Button becomes "Next: rotate token", not "Save".
    const next = screen.getByRole('button', { name: /Next: rotate token/ });
    fireEvent.click(next);

    // Nothing saved yet - we're at the rotate step.
    expect(updateSpy).not.toHaveBeenCalled();
    const saveRotate = await screen.findByRole('button', { name: /Save & rotate token/ });

    // Confirming commits the grant change THEN rotates.
    fireEvent.click(saveRotate);
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'fleet_1',
        expect.objectContaining({ grant_docker: false, grant_service_mgmt: true })),
    );
    expect(rotateSpy).toHaveBeenCalled();
  });

  it('cancelling the rotate step discards the grant change', async () => {
    const { updateSpy, rotateSpy } = mockApis([member('a1')]);
    render(<FleetsPage config={CONFIG} />);
    await openEdit();
    fireEvent.click(dockerCheckbox());
    fireEvent.click(screen.getByRole('button', { name: /Next: rotate token/ }));
    // Cancel out of the rotate step.
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(updateSpy).not.toHaveBeenCalled();
    expect(rotateSpy).not.toHaveBeenCalled();
  });

  it('a non-grant edit saves directly without the rotate step', async () => {
    const { updateSpy, rotateSpy } = mockApis([member('a1')]);
    render(<FleetsPage config={CONFIG} />);
    await openEdit();
    // Change mode only (no grant change) -> button stays "Save".
    fireEvent.click(screen.getByRole('button', { name: 'wild' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(rotateSpy).not.toHaveBeenCalled();
  });
});
