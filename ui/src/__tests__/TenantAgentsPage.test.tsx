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

  it('filters the table to the selected type', async () => {
    renderPage([HOST_AGENT, K8S_AGENT]);
    await screen.findByText('host-1');
    expect(screen.getByText('cluster-1')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByDisplayValue('All types'), 'k8s');
    expect(screen.getByText('cluster-1')).toBeInTheDocument();
    expect(screen.queryByText('host-1')).not.toBeInTheDocument();
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
