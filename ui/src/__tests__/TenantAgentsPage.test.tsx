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
