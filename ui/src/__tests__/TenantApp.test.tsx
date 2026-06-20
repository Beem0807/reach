import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, within } from '@testing-library/react';
import { TenantApp } from '../TenantApp';
import type { TenantConfig } from '../types';
import * as api from '../api';

// ---------------------------------------------------------------------------
// localStorage stub (jsdom in this vitest setup doesn't implement Storage)
// ---------------------------------------------------------------------------
const _ls: Record<string, string> = {};
vi.stubGlobal('localStorage', {
  getItem:    (k: string) => _ls[k] ?? null,
  setItem:    (k: string, v: string) => { _ls[k] = v; },
  removeItem: (k: string) => { delete _ls[k]; },
  clear:      () => { Object.keys(_ls).forEach(k => delete _ls[k]); },
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ADMIN_CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'tok',
  tenantId: 'tenant_1',
  tenantName: 'Acme Corp',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice Admin',
  role: 'admin',
  mustResetPassword: false,
};

const DEV_CONFIG: TenantConfig = {
  ...ADMIN_CONFIG,
  role: 'developer',
  username: 'devuser',
  name: 'Dev User',
};

function renderApp(config: TenantConfig = ADMIN_CONFIG, onSignOut = vi.fn()) {
  vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [] });
  return render(<TenantApp config={config} onSignOut={onSignOut} />);
}

// Scope queries to the sidebar <aside> to avoid matching page content
function sb() {
  return within(document.querySelector('aside')!);
}

beforeEach(() => {
  vi.restoreAllMocks();
  Object.keys(_ls).forEach(k => delete _ls[k]); // reset store between tests
});

// ---------------------------------------------------------------------------
// Sidebar - expanded state (default)
// ---------------------------------------------------------------------------

describe('TenantSidebar - expanded (default)', () => {
  it('shows nav labels for admin-visible items', () => {
    renderApp();
    expect(sb().getByText('Users')).toBeInTheDocument();
    expect(sb().getByText('Agents')).toBeInTheDocument();
    expect(sb().getByText('Jobs')).toBeInTheDocument();
    expect(sb().getByText('Audit Logs')).toBeInTheDocument();
  });

  it('shows the collapse button', () => {
    renderApp();
    expect(sb().getByTitle('Collapse sidebar')).toBeInTheDocument();
  });

  it('shows tenant name in info card', () => {
    renderApp();
    expect(sb().getByText('Acme Corp')).toBeInTheDocument();
  });

  it('shows username and role in footer', () => {
    renderApp();
    expect(sb().getByText('@alice')).toBeInTheDocument();
    expect(sb().getByText('Admin')).toBeInTheDocument();
  });

  it('shows "Sign out" text', () => {
    renderApp();
    expect(sb().getByText('Sign out')).toBeInTheDocument();
  });

  it('nav buttons do NOT have title attribute when expanded', () => {
    renderApp();
    expect(sb().getByText('Users').closest('button')?.title).toBeFalsy();
  });
});

// ---------------------------------------------------------------------------
// Sidebar - collapsing
// ---------------------------------------------------------------------------

describe('TenantSidebar - collapsing', () => {
  function collapse() {
    fireEvent.click(sb().getByTitle('Collapse sidebar'));
  }

  it('collapses when collapse button is clicked', () => {
    renderApp();
    collapse();
    expect(sb().queryByText('Users')).not.toBeInTheDocument();
  });

  it('hides all nav labels after collapse', () => {
    renderApp();
    collapse();
    ['Users', 'Agents', 'Jobs', 'Approvals', 'API Tokens', 'Audit Logs'].forEach(label => {
      expect(sb().queryByText(label)).not.toBeInTheDocument();
    });
  });

  it('shows expand button after collapse', () => {
    renderApp();
    collapse();
    expect(sb().getByTitle('Expand sidebar')).toBeInTheDocument();
  });

  it('collapse button disappears after collapse', () => {
    renderApp();
    collapse();
    expect(sb().queryByTitle('Collapse sidebar')).not.toBeInTheDocument();
  });

  it('nav buttons get title attribute (tooltip) after collapse', () => {
    renderApp();
    collapse();
    expect(sb().getByTitle('Users')).toBeInTheDocument();
    expect(sb().getByTitle('Agents')).toBeInTheDocument();
  });

  it('hides tenant info card after collapse', () => {
    renderApp();
    collapse();
    expect(sb().queryByText('Acme Corp')).not.toBeInTheDocument();
  });

  it('hides username and role after collapse', () => {
    renderApp();
    collapse();
    expect(sb().queryByText('@alice')).not.toBeInTheDocument();
    expect(sb().queryByText('Admin')).not.toBeInTheDocument();
  });

  it('"Sign out" text hidden after collapse - button still present with title', () => {
    renderApp();
    collapse();
    expect(sb().queryByText('Sign out')).not.toBeInTheDocument();
    expect(sb().getByTitle('Sign out')).toBeInTheDocument();
  });

  it('persists collapsed state to localStorage', () => {
    renderApp();
    collapse();
    expect(localStorage.getItem('sidebar_collapsed')).toBe('true');
  });
});

// ---------------------------------------------------------------------------
// Sidebar - expanding back
// ---------------------------------------------------------------------------

describe('TenantSidebar - expanding back', () => {
  function collapseAndExpand() {
    fireEvent.click(sb().getByTitle('Collapse sidebar'));
    fireEvent.click(sb().getByTitle('Expand sidebar'));
  }

  it('restores nav labels after expanding', () => {
    renderApp();
    collapseAndExpand();
    expect(sb().getByText('Users')).toBeInTheDocument();
  });

  it('hides nav button titles after expanding', () => {
    renderApp();
    collapseAndExpand();
    const usersBtn = sb().getByText('Users').closest('button');
    expect(usersBtn?.title).toBeFalsy();
  });

  it('restores tenant info card after expanding', () => {
    renderApp();
    collapseAndExpand();
    expect(sb().getByText('Acme Corp')).toBeInTheDocument();
  });

  it('restores "Sign out" text after expanding', () => {
    renderApp();
    collapseAndExpand();
    expect(sb().getByText('Sign out')).toBeInTheDocument();
  });

  it('persists expanded state to localStorage', () => {
    renderApp();
    collapseAndExpand();
    expect(localStorage.getItem('sidebar_collapsed')).toBe('false');
  });
});

// ---------------------------------------------------------------------------
// Sidebar - localStorage persistence
// ---------------------------------------------------------------------------

describe('TenantSidebar - localStorage persistence', () => {
  it('starts collapsed when localStorage has "true"', () => {
    localStorage.setItem('sidebar_collapsed', 'true');
    renderApp();
    expect(sb().queryByText('Users')).not.toBeInTheDocument();
    expect(sb().getByTitle('Users')).toBeInTheDocument();
  });

  it('starts expanded when localStorage has "false"', () => {
    localStorage.setItem('sidebar_collapsed', 'false');
    renderApp();
    expect(sb().getByText('Users')).toBeInTheDocument();
  });

  it('starts expanded when localStorage has no entry', () => {
    renderApp();
    expect(sb().getByText('Users')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Sidebar - role-based nav visibility
// ---------------------------------------------------------------------------

describe('TenantSidebar - role-based nav visibility', () => {
  it('developer sees Agents, Jobs but not Users or Audit Logs', () => {
    vi.spyOn(api, 'listTenantAgents').mockResolvedValue({ agents: [] });
    render(<TenantApp config={DEV_CONFIG} onSignOut={vi.fn()} />);
    const sidebar = document.querySelector('aside')!;

    expect(within(sidebar).getByText('Agents')).toBeInTheDocument();
    expect(within(sidebar).getByText('Jobs')).toBeInTheDocument();
    expect(within(sidebar).queryByText('Users')).not.toBeInTheDocument();
    expect(within(sidebar).queryByText('Audit Logs')).not.toBeInTheDocument();
  });

  it('admin sees all nav items', () => {
    renderApp();
    ['Users', 'Agents', 'Jobs', 'Approvals', 'API Tokens', 'Audit Logs'].forEach(label => {
      expect(sb().getByText(label)).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Sign out callback
// ---------------------------------------------------------------------------

describe('TenantSidebar - sign out', () => {
  it('calls onSignOut when "Sign out" clicked', () => {
    const onSignOut = vi.fn();
    vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [] });
    render(<TenantApp config={ADMIN_CONFIG} onSignOut={onSignOut} />);
    fireEvent.click(sb().getByText('Sign out'));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it('calls onSignOut from icon-only button when collapsed', () => {
    const onSignOut = vi.fn();
    vi.spyOn(api, 'listTenantUsers').mockResolvedValue({ users: [] });
    render(<TenantApp config={ADMIN_CONFIG} onSignOut={onSignOut} />);
    fireEvent.click(sb().getByTitle('Collapse sidebar'));
    fireEvent.click(sb().getByTitle('Sign out'));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
});
