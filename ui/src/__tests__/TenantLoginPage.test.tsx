import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TenantLoginPage } from '../pages/TenantLoginPage';
import * as api from '../api';

const LOGIN_RESP = {
  token: 'tok_xyz',
  must_reset_password: false,
  user: {
    user_id: 'user_1',
    tenant_id: 'tenant_1',
    tenant_name: 'Acme',
    username: 'alice',
    name: 'Alice',
    role: 'admin',
  },
};

function renderPage() {
  const onLogin = vi.fn();
  const onSwitchToPlatform = vi.fn();
  render(<TenantLoginPage onLogin={onLogin} onSwitchToPlatform={onSwitchToPlatform} />);
  return { onLogin, onSwitchToPlatform };
}

async function fillForm() {
  await userEvent.type(screen.getByPlaceholderText('Enter Tenant Name'), 'Acme');
  await userEvent.type(screen.getByPlaceholderText('Enter User Name'), 'alice');
  await userEvent.type(screen.getByPlaceholderText('Enter Password'), 'secret');
}

beforeEach(() => { vi.restoreAllMocks(); });

describe('TenantLoginPage', () => {
  it('shows validation error when fields are empty', async () => {
    const spy = vi.spyOn(api, 'tenantLogin');
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('All fields are required.')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it('calls tenantLogin and maps response into config on success', async () => {
    vi.spyOn(api, 'tenantLogin').mockResolvedValue(LOGIN_RESP as never);
    const { onLogin } = renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledTimes(1));
    expect(onLogin.mock.calls[0][0]).toMatchObject({
      tenantToken: 'tok_xyz',
      tenantId: 'tenant_1',
      tenantName: 'Acme',
      userId: 'user_1',
      username: 'alice',
      role: 'admin',
      mustResetPassword: false,
    });
  });

  it('shows error message when login fails', async () => {
    vi.spyOn(api, 'tenantLogin').mockRejectedValue(new Error('Invalid credentials'));
    const { onLogin } = renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Invalid credentials')).toBeInTheDocument();
    expect(onLogin).not.toHaveBeenCalled();
  });

  it('switches to platform login', () => {
    const { onSwitchToPlatform } = renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in here' }));
    expect(onSwitchToPlatform).toHaveBeenCalledTimes(1);
  });
});
