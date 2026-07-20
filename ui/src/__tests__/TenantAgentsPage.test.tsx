import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TenantAgentsPage } from '../pages/TenantAgentsPage';
import type { Agent } from '../types';
import * as api from '../api';

const CONFIG = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok_test',
  tenantId: 'tenant_1',
  tenantName: 'acme',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice',
  role: 'admin' as const,
  mustResetPassword: false,
};

const BASE_AGENT: Agent = {
  agent_id: 'agent_abc',
  tenant_id: 'tenant_1',
  status: 'ACTIVE',
  hostname: 'myhost.local',
  mode: 'wild',
  access_level: 'open',
  tags: [],
  grant_docker: false,
  grant_service_mgmt: false,
  docker_detected: undefined,
  service_mgmt_detected: undefined,
};

function renderPage(agents: Agent[]) {
  vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents });
  return render(<TenantAgentsPage config={CONFIG} />);
}

beforeEach(() => { vi.restoreAllMocks(); });

describe('search + pagination', () => {
  it('sends the search query to the server only when Search is clicked', async () => {
    const spy = vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
    render(<TenantAgentsPage config={CONFIG} />);
    await screen.findByText('myhost.local');
    fireEvent.change(screen.getByPlaceholderText(/Search agents/), { target: { value: 'web-01' } });
    // Typing alone does NOT call the API with q.
    expect(spy).not.toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ q: 'web-01' }));
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken,
        expect.objectContaining({ q: 'web-01', limit: '20', offset: '0' })),
    );
  });

  it('shows a pager and advances the offset when there are more than one page', async () => {
    const spy = vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [BASE_AGENT], total: 45, limit: 20, offset: 0 });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
    render(<TenantAgentsPage config={CONFIG} />);
    await screen.findByText(/Showing 1–1 of 45/);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken,
        expect.objectContaining({ offset: '20' })),
    );
  });

  it('populates the tag dropdown from the server facet, not the current page', async () => {
    // The page holds one agent with no tags, yet the facet lists the whole tenant's tags.
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({
      agents: [BASE_AGENT], total: 1, limit: 20, offset: 0, all_tags: ['env:prod', 'team:core'],
    });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
    render(<TenantAgentsPage config={CONFIG} />);
    await screen.findByText('myhost.local');
    fireEvent.click(screen.getByRole('button', { name: /Tags/ }));
    expect(screen.getByText('env')).toBeInTheDocument();
    expect(screen.getByText('team')).toBeInTheDocument();
  });

  it('applies a staged tag filter to the server only on Search', async () => {
    const spy = vi.spyOn(api, 'listTenantAgents').mockResolvedValue({
      agents: [BASE_AGENT], total: 1, limit: 20, offset: 0, all_tags: ['env:prod'],
    });
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
    render(<TenantAgentsPage config={CONFIG} />);
    await screen.findByText('myhost.local');

    fireEvent.click(screen.getByRole('button', { name: /Tags/ }));
    fireEvent.click(screen.getByText('env'));           // stage the tag
    expect(spy).not.toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ tag: 'env:prod' }));

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ tag: 'env:prod' })));
  });
});

// ---------------------------------------------------------------------------
// CapabilityCell states - Docker column
// ---------------------------------------------------------------------------

describe('Docker capability cell', () => {
  it('shows - when neither granted nor detected', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: false, docker_detected: undefined }]);
    await screen.findByText('myhost.local');
    // all "-" placeholders are present; check at least one exists
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThan(0);
  });

  it('shows Granted when granted but not yet detected', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: true, docker_detected: false }]);
    await screen.findByText('myhost.local');
    expect(within(document.querySelector('table')!).getByText('Granted')).toBeInTheDocument();
  });

  it('shows Active when granted and detected', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: true, docker_detected: true }]);
    await screen.findByText('myhost.local');
    expect(within(document.querySelector('table')!).getByText('Active')).toBeInTheDocument();
  });

  it('shows Detected when detected but not granted (out-of-band)', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: false, docker_detected: true }]);
    await screen.findByText('myhost.local');
    expect(within(document.querySelector('table')!).getByText('Detected')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// CapabilityCell states - Service mgmt column
// ---------------------------------------------------------------------------

describe('Service mgmt capability cell', () => {
  it('shows Granted when granted but not detected', async () => {
    renderPage([{ ...BASE_AGENT, grant_service_mgmt: true, service_mgmt_detected: false }]);
    await screen.findByText('myhost.local');
    expect(within(document.querySelector('table')!).getByText('Granted')).toBeInTheDocument();
  });

  it('shows Active when granted and detected', async () => {
    renderPage([{ ...BASE_AGENT, grant_service_mgmt: true, service_mgmt_detected: true }]);
    await screen.findByText('myhost.local');
    expect(within(document.querySelector('table')!).getByText('Active')).toBeInTheDocument();
  });

  it('shows Detected when detected but not granted', async () => {
    renderPage([{ ...BASE_AGENT, grant_service_mgmt: false, service_mgmt_detected: true }]);
    await screen.findByText('myhost.local');
    expect(within(document.querySelector('table')!).getByText('Detected')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Both columns active simultaneously
// ---------------------------------------------------------------------------

describe('Both capability columns', () => {
  it('shows two Active badges when both granted and detected', async () => {
    renderPage([{
      ...BASE_AGENT,
      grant_docker: true, docker_detected: true,
      grant_service_mgmt: true, service_mgmt_detected: true,
    }]);
    await screen.findByText('myhost.local');
    const active = within(document.querySelector('table')!).getAllByText('Active');
    expect(active).toHaveLength(2);
  });

  it('shows two Detected badges when both out-of-band', async () => {
    renderPage([{
      ...BASE_AGENT,
      grant_docker: false, docker_detected: true,
      grant_service_mgmt: false, service_mgmt_detected: true,
    }]);
    await screen.findByText('myhost.local');
    const detected = within(document.querySelector('table')!).getAllByText('Detected');
    expect(detected).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// Access level badge
// ---------------------------------------------------------------------------

describe('Access level badge', () => {
  it('renders the access_level returned by the API', async () => {
    renderPage([{ ...BASE_AGENT, access_level: 'elevated' }]);
    await screen.findByText('myhost.local');
    expect(screen.getByText('elevated')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Page rendering basics
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Sudo notice in CreateAgentModal
// ---------------------------------------------------------------------------

describe('Sudo notice in CreateAgentModal', () => {
  async function openCreate() {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    vi.spyOn(api, 'listAgentVersions').mockResolvedValue({ type: 'host', default: 'latest', versions: [] });
    render(<TenantAgentsPage config={CONFIG} />);
    fireEvent.click(await screen.findByRole('button', { name: /New agent/i }));
    await screen.findByRole('heading', { name: /new agent/i });
  }

  function permissionCheckbox(label: string) {
    return screen.getByText(label).closest('label')!.querySelector('input[type="checkbox"]') as HTMLInputElement;
  }
  const sudoNotice = () => screen.queryByText(/install command requires/i);
  const extraGrantText = () => screen.queryByText(/group membership and sudoers/i);

  it('shows sudo notice immediately (always visible)', async () => {
    await openCreate();
    expect(sudoNotice()).toBeInTheDocument();
  });

  it('does not show extra grant text when no permissions selected', async () => {
    await openCreate();
    expect(extraGrantText()).not.toBeInTheDocument();
  });

  it('shows extra grant text when Docker is checked', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(permissionCheckbox('Docker access'));
    await waitFor(() => expect(extraGrantText()).toBeInTheDocument());
  });

  it('shows extra grant text when Service management is checked', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(permissionCheckbox('Service management'));
    await waitFor(() => expect(extraGrantText()).toBeInTheDocument());
  });

  it('hides extra grant text when Docker is unchecked again', async () => {
    const user = userEvent.setup();
    await openCreate();
    await user.click(permissionCheckbox('Docker access'));
    await waitFor(() => expect(extraGrantText()).toBeInTheDocument());
    await user.click(permissionCheckbox('Docker access'));
    await waitFor(() => expect(extraGrantText()).not.toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// Version dropdown in CreateAgentModal
// ---------------------------------------------------------------------------

describe('Version dropdown in CreateAgentModal', () => {
  async function openCreate(versions: string[]) {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    const spy = vi.spyOn(api, 'listAgentVersions')
      .mockResolvedValue({ type: 'host', default: 'latest', versions });
    render(<TenantAgentsPage config={CONFIG} />);
    fireEvent.click(await screen.findByRole('button', { name: /New agent/i }));
    await screen.findByRole('heading', { name: /new agent/i });
    return spy;
  }
  const versionSelect = () =>
    screen.getByRole('option', { name: /^Latest/ }).closest('select') as HTMLSelectElement;

  it('defaults to Latest and lists discovered versions', async () => {
    await openCreate(['0.9.4', '0.9.1']);
    await waitFor(() => expect(screen.getByRole('option', { name: /Latest \(0\.9\.4\)/ })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: '0.9.4' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '0.9.1' })).toBeInTheDocument();
    expect(versionSelect().value).toBe('');  // Latest is the default selection
  });

  it('re-fetches versions when the agent type switches to k8s', async () => {
    const spy = await openCreate(['0.9.4']);
    await waitFor(() => expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'host'));
    fireEvent.click(screen.getByRole('button', { name: /Kubernetes/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'k8s'));
  });

  it('passes the picked version to createTenantAgent', async () => {
    const user = userEvent.setup();
    await openCreate(['0.9.4', '0.9.1']);
    const create = vi.spyOn(api, 'createTenantAgent')
      .mockResolvedValue({ agent_id: 'agent_new', commands: {} } as never);
    await waitFor(() => expect(screen.getByRole('option', { name: '0.9.4' })).toBeInTheDocument());
    await user.selectOptions(versionSelect(), '0.9.4');
    await user.click(screen.getByRole('button', { name: /Create agent/i }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    // version is the 8th positional arg of createTenantAgent
    expect(create.mock.calls[0][7]).toBe('0.9.4');
  });
});

// ---------------------------------------------------------------------------
// Sudo notice in ReissueModal
// ---------------------------------------------------------------------------

describe('Sudo notice in ReissueModal', () => {
  async function openReissue(agent: Agent = BASE_AGENT) {
    renderPage([agent]);
    // Step 1: click any row cell to open the AgentDetailModal
    fireEvent.click(await screen.findByText(agent.hostname!));
    // Step 2: click "Reissue token" inside the detail modal
    fireEvent.click(await screen.findByRole('button', { name: /reissue token/i }));
    // Step 3: wait for the 50ms setTimeout inside AgentDetailModal.open() to fire
    await screen.findByRole('heading', { name: /reissue install token/i });
  }

  function permissionCheckbox(label: string) {
    return screen.getByText(label).closest('label')!.querySelector('input[type="checkbox"]') as HTMLInputElement;
  }
  const sudoNotice = () => screen.queryByText(/install command requires/i);
  const extraGrantText = () => screen.queryByText(/group membership and sudoers/i);

  it('shows sudo notice immediately on open', async () => {
    await openReissue();
    expect(sudoNotice()).toBeInTheDocument();
  });

  it('does not show extra grant text when no permissions selected', async () => {
    await openReissue({ ...BASE_AGENT, grant_docker: false, grant_service_mgmt: false });
    expect(extraGrantText()).not.toBeInTheDocument();
  });

  it('pre-populates service mgmt checkbox from agent.grant_service_mgmt', async () => {
    await openReissue({ ...BASE_AGENT, grant_service_mgmt: true });
    const cb = permissionCheckbox('Grant systemctl / service management access');
    expect(cb.checked).toBe(true);
  });

  it('pre-populates docker checkbox from agent.grant_docker', async () => {
    await openReissue({ ...BASE_AGENT, grant_docker: true });
    const cb = permissionCheckbox('Grant Docker access');
    expect(cb.checked).toBe(true);
  });

  it('shows extra grant text when pre-populated with service mgmt true', async () => {
    await openReissue({ ...BASE_AGENT, grant_service_mgmt: true });
    expect(extraGrantText()).toBeInTheDocument();
  });

  it('shows extra grant text when Docker is checked', async () => {
    const user = userEvent.setup();
    await openReissue({ ...BASE_AGENT, grant_docker: false, grant_service_mgmt: false });
    await user.click(permissionCheckbox('Grant Docker access'));
    await waitFor(() => expect(extraGrantText()).toBeInTheDocument());
  });

  it('shows extra grant text when service mgmt is checked', async () => {
    const user = userEvent.setup();
    await openReissue({ ...BASE_AGENT, grant_docker: false, grant_service_mgmt: false });
    await user.click(permissionCheckbox('Grant systemctl / service management access'));
    await waitFor(() => expect(extraGrantText()).toBeInTheDocument());
  });

  it('hides extra grant text when Docker is unchecked again', async () => {
    const user = userEvent.setup();
    await openReissue({ ...BASE_AGENT, grant_docker: false, grant_service_mgmt: false });
    await user.click(permissionCheckbox('Grant Docker access'));
    await waitFor(() => expect(extraGrantText()).toBeInTheDocument());
    await user.click(permissionCheckbox('Grant Docker access'));
    await waitFor(() => expect(extraGrantText()).not.toBeInTheDocument());
  });

  it('hides host-only grants and sudo notice for a k8s agent, shows RBAC note', async () => {
    await openReissue({ ...BASE_AGENT, type: 'k8s' });
    expect(screen.queryByText('Grant Docker access')).not.toBeInTheDocument();
    expect(screen.queryByText('Grant systemctl / service management access')).not.toBeInTheDocument();
    expect(sudoNotice()).not.toBeInTheDocument();
    expect(screen.getByText(/kubernetes rbac/i)).toBeInTheDocument();
  });
});

describe('TenantAgentsPage rendering', () => {
  it('shows agent hostname', async () => {
    renderPage([BASE_AGENT]);
    expect(await screen.findByText('myhost.local')).toBeInTheDocument();
  });

  it('shows empty state when no agents', async () => {
    renderPage([]);
    expect(await screen.findByText(/No agents registered/)).toBeInTheDocument();
  });

  it('shows New agent button for operators', async () => {
    renderPage([]);
    await screen.findByText(/No agents registered/);
    expect(screen.getByRole('button', { name: /New agent/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Capability tooltip (legend moved to hover tooltip)
// ---------------------------------------------------------------------------

describe('CapabilityCell hover tooltips', () => {
  it('tooltip for Active state is in the document', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: true, docker_detected: true }]);
    await screen.findByText('myhost.local');
    expect(screen.getByText(/Granted and currently detected running on this agent/i)).toBeInTheDocument();
  });

  it('tooltip for Granted state is in the document', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: true, docker_detected: false }]);
    await screen.findByText('myhost.local');
    expect(screen.getByText(/not yet detected running on this agent/i)).toBeInTheDocument();
  });

  it('tooltip for Detected (out-of-band) state is in the document', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: false, docker_detected: true }]);
    await screen.findByText('myhost.local');
    expect(screen.getByText(/out-of-band access, needs acknowledgement/i)).toBeInTheDocument();
  });

  it('tooltip for unconfigured state is in the document', async () => {
    renderPage([{ ...BASE_AGENT, grant_docker: false, docker_detected: false }]);
    await screen.findByText('myhost.local');
    expect(screen.getAllByText(/Not configured/i).length).toBeGreaterThan(0);
  });

  it('no <details> legend element is rendered', async () => {
    renderPage([BASE_AGENT]);
    await screen.findByText('myhost.local');
    expect(document.querySelector('details')).not.toBeInTheDocument();
  });

  it('legend text is not shown as a visible block above the table', async () => {
    renderPage([BASE_AGENT]);
    await screen.findByText('myhost.local');
    expect(screen.queryByText(/Capability status legend/i)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Agent type: filter, n/a host-grants for k8s, drift indicator
// ---------------------------------------------------------------------------

describe('Agent type column and filter', () => {
  const HOST_AGENT: Agent = { ...BASE_AGENT, agent_id: 'agent_host', hostname: 'host-1', type: 'host' };
  const K8S_AGENT: Agent = {
    ...BASE_AGENT, agent_id: 'agent_k8s', hostname: 'cluster-1', type: 'k8s',
  };

  it('filters the table to the selected type on Search (server-side)', async () => {
    // The dropdown just stages the filter; the server applies it when Search is clicked.
    const spy = vi.spyOn(api, 'listTenantAgents').mockImplementation((_u, _t, params = {}) =>
      Promise.resolve({ agents: params.type === 'k8s' ? [K8S_AGENT] : [HOST_AGENT, K8S_AGENT] }));
    vi.spyOn(api, 'listFleets').mockResolvedValue({ fleets: [], default_reap_after_seconds: 1800 });
    render(<TenantAgentsPage config={CONFIG} />);
    await screen.findByText('host-1');

    // Selecting the type alone does NOT re-query.
    await userEvent.selectOptions(screen.getByDisplayValue('All types'), 'k8s');
    expect(spy).not.toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ type: 'k8s' }));
    expect(screen.getByText('host-1')).toBeInTheDocument();

    // Clicking Search applies it and the server returns only the k8s agent.
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, expect.objectContaining({ type: 'k8s' })));
    await waitFor(() => expect(screen.queryByText('host-1')).not.toBeInTheDocument());
    expect(screen.getByText('cluster-1')).toBeInTheDocument();
  });

  it('shows n/a for docker and service-mgmt on k8s agents', async () => {
    renderPage([K8S_AGENT]);
    await screen.findByText('cluster-1');
    const table = document.querySelector('table')!;
    expect(within(table).getAllByText('n/a').length).toBe(2);
  });

  it('shows a clickable drift indicator on k8s agents with permission drift', async () => {
    renderPage([{ ...K8S_AGENT, k8s_permissions_drift: true }]);
    await screen.findByText('cluster-1');
    const btn = screen.getByRole('button', { name: /cluster rbac needs review/i });
    expect(btn).toBeInTheDocument();
    // Hover reason is rendered (tooltip text), and clicking opens the RBAC detail modal.
    expect(screen.getByText(/needs acknowledgement/i)).toBeInTheDocument();
    await userEvent.click(btn);
    // "Claimed at" is a detail-modal-only field label, confirming the modal opened.
    expect(await screen.findByText('Claimed at')).toBeInTheDocument();
  });

  // The RBAC snapshot must be present on the list item so the detail modal can show it
  // (regression: it was previously omitted from GET /agents, so the section never rendered).
  const K8S_AGENT_PERMS: Agent = {
    ...K8S_AGENT, k8s_permissions_reported: true,
    k8s_permissions: {
      cluster_wide: [
        { verbs: ['get', 'list', 'watch'], api_groups: ['apps'], resources: ['deployments', 'statefulsets'] },
      ],
      namespaces: [
        { namespace: 'team-a', resource_rules: [{ verbs: ['get', 'update', 'patch'], api_groups: [''], resources: ['pods'] }] },
      ],
      incomplete: false,
      hash: 'abc123',
    },
  };

  it('shows the cluster RBAC rules in the detail modal for a k8s agent', async () => {
    renderPage([K8S_AGENT_PERMS]);
    await screen.findByText('cluster-1');
    fireEvent.click(screen.getByText('cluster-1'));  // open the detail modal
    expect(await screen.findByText('Cluster permissions')).toBeInTheDocument();
    expect(screen.getByText(/Effective in every namespace/i)).toBeInTheDocument();
    expect(screen.getByText(/deployments, statefulsets/)).toBeInTheDocument();
    expect(screen.getByText(/team-a/)).toBeInTheDocument();
  });

  it('shows the Acknowledge action when a k8s agent has RBAC drift', async () => {
    renderPage([{ ...K8S_AGENT_PERMS, k8s_permissions_drift: true }]);
    await screen.findByText('cluster-1');
    await userEvent.click(screen.getByRole('button', { name: /cluster rbac needs review/i }));
    expect(await screen.findByText('Cluster permissions')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Acknowledge/i })).toBeInTheDocument();
  });

  it('shows the k8s execution allowlist in the detail modal', async () => {
    renderPage([{ ...K8S_AGENT_PERMS, k8s_allowed_binaries: ['kubectl', 'jq', 'helm'] }]);
    await screen.findByText('cluster-1');
    fireEvent.click(screen.getByText('cluster-1'));
    expect(await screen.findByText('Execution allowlist')).toBeInTheDocument();
    expect(screen.getByText('helm')).toBeInTheDocument();
  });

  it('shows a "not yet reported" note when the allowlist is absent', async () => {
    renderPage([{ ...K8S_AGENT_PERMS, k8s_allowed_binaries: null }]);
    await screen.findByText('cluster-1');
    fireEvent.click(screen.getByText('cluster-1'));
    expect(await screen.findByText('Execution allowlist')).toBeInTheDocument();
    expect(screen.getByText(/Not yet reported/i)).toBeInTheDocument();
  });

  it('shows n/a in the Cluster RBAC column for host agents', async () => {
    renderPage([HOST_AGENT]);
    await screen.findByText('host-1');
    const table = document.querySelector('table')!;
    // host: docker + service-mgmt show CapabilityCell, Cluster RBAC shows n/a
    expect(within(table).getByText('n/a')).toBeInTheDocument();
  });

  it('renders running-as-root as n/a (with the two-axis note) in the k8s detail', async () => {
    renderPage([K8S_AGENT]);
    await screen.findByText('cluster-1');
    fireEvent.click(screen.getByText('cluster-1'));  // open detail modal
    expect(await screen.findByText('Running as root')).toBeInTheDocument();
    expect(screen.getByTitle(/non-root/i)).toHaveTextContent('n/a');
    expect(screen.getByText(/Reflects policy mode/i)).toBeInTheDocument();
  });
});

describe('AgentDetailModal - filesystem sandbox', () => {
  it('shows write protection On for a capable host', async () => {
    renderPage([{ ...BASE_AGENT, landlock_status: 'active' }]);
    fireEvent.click(await screen.findByText('myhost.local'));
    expect(await screen.findByText(/blocked by the kernel/i)).toBeInTheDocument();
  });

  it('warns and offers to allow when there is no write protection', async () => {
    renderPage([{ ...BASE_AGENT, mode: 'approved', landlock_status: 'unavailable', sandbox_ack: false }]);
    fireEvent.click(await screen.findByText('myhost.local'));
    expect(await screen.findByText(/commands held - no write protection/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /allow without protection/i })).toBeInTheDocument();
  });

  it('allowing asks for confirmation first, then calls the API', async () => {
    const spy = vi.spyOn(api, 'acknowledgeSandbox').mockResolvedValue({ agent_id: 'agent_abc', sandbox_ack: true });
    renderPage([{ ...BASE_AGENT, landlock_status: 'unavailable', sandbox_ack: false }]);
    fireEvent.click(await screen.findByText('myhost.local'));
    fireEvent.click(await screen.findByRole('button', { name: /allow without protection/i }));
    // A confirmation dialog appears - the API is NOT called yet.
    expect(await screen.findByText(/without kernel write protection/i)).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /allow without protection/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(CONFIG.apiUrl, CONFIG.tenantToken, 'agent_abc', true));
  });

  it('shows the allowed state with a require-protection action', async () => {
    renderPage([{ ...BASE_AGENT, landlock_status: 'unavailable', sandbox_ack: true }]);
    fireEvent.click(await screen.findByText('myhost.local'));
    expect(await screen.findByText(/running without protection/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /require protection/i })).toBeInTheDocument();
  });

  it('treats macOS (unsupported) like unavailable - offers to allow with macOS wording', async () => {
    renderPage([{ ...BASE_AGENT, landlock_status: 'unsupported', sandbox_ack: false }]);
    fireEvent.click(await screen.findByText('myhost.local'));
    expect(await screen.findByText(/macOS has no kernel write protection/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /allow without protection/i })).toBeInTheDocument();
  });

  it('flags "blocked" in the list for a host with no write protection', async () => {
    renderPage([
      { ...BASE_AGENT, agent_id: 'a1', hostname: 'linux-old', landlock_status: 'unavailable' },
      { ...BASE_AGENT, agent_id: 'a2', hostname: 'linux-ok', landlock_status: 'active' },
    ]);
    await screen.findByText('linux-old');
    // The unprotected host is flagged 'blocked'; the protected one is not.
    expect(screen.getAllByText(/^blocked$/i).length).toBe(1);
  });

  it('a fleet member routes the write-protection exception to its fleet (no per-agent action)', async () => {
    renderPage([{ ...BASE_AGENT, fleet_id: 'fleet_x', landlock_status: 'unavailable', sandbox_ack: false }]);
    fireEvent.click(await screen.findByText('myhost.local'));
    expect(await screen.findByText(/fleet member/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow without protection/i })).not.toBeInTheDocument();
  });

  it('create form: choosing macOS warns it runs without write protection', async () => {
    renderPage([BASE_AGENT]);
    fireEvent.click(await screen.findByRole('button', { name: /New agent/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'macOS' }));
    expect(screen.getByText(/no kernel write protection/i)).toBeInTheDocument();
  });
});
