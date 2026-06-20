import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PasswordResetPage } from '../pages/PasswordResetPage';
import type { TenantConfig } from '../types';
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
  mustResetPassword: true,
};

function renderPage() {
  const onComplete = vi.fn();
  render(<PasswordResetPage config={CONFIG} onComplete={onComplete} />);
  return { onComplete };
}

const newPw = () => screen.getByPlaceholderText('At least 8 characters');
const confirmPw = () => screen.getAllByPlaceholderText('••••••••')[1];

beforeEach(() => { vi.restoreAllMocks(); });

describe('PasswordResetPage', () => {
  it('rejects mismatched passwords without calling the API', async () => {
    const spy = vi.spyOn(api, 'tenantChangePassword');
    renderPage();
    await userEvent.type(screen.getAllByPlaceholderText('••••••••')[0], 'oldpass12');
    await userEvent.type(newPw(), 'newpassword1');
    await userEvent.type(confirmPw(), 'different1');
    fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it('rejects passwords shorter than 8 characters', async () => {
    const spy = vi.spyOn(api, 'tenantChangePassword');
    renderPage();
    await userEvent.type(screen.getAllByPlaceholderText('••••••••')[0], 'oldpass12');
    await userEvent.type(newPw(), 'short');
    await userEvent.type(confirmPw(), 'short');
    fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
    expect(await screen.findByText('Password must be at least 8 characters.')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it('calls tenantChangePassword and onComplete on success', async () => {
    vi.spyOn(api, 'tenantChangePassword').mockResolvedValue(undefined as never);
    const { onComplete } = renderPage();
    await userEvent.type(screen.getAllByPlaceholderText('••••••••')[0], 'oldpass12');
    await userEvent.type(newPw(), 'newpassword1');
    await userEvent.type(confirmPw(), 'newpassword1');
    fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));
    expect(api.tenantChangePassword).toHaveBeenCalledWith(
      CONFIG.apiUrl, CONFIG.tenantToken, 'oldpass12', 'newpassword1',
    );
  });

  it('surfaces API errors', async () => {
    vi.spyOn(api, 'tenantChangePassword').mockRejectedValue(new Error('wrong temp password'));
    const { onComplete } = renderPage();
    await userEvent.type(screen.getAllByPlaceholderText('••••••••')[0], 'badtemp12');
    await userEvent.type(newPw(), 'newpassword1');
    await userEvent.type(confirmPw(), 'newpassword1');
    fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
    expect(await screen.findByText('wrong temp password')).toBeInTheDocument();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('shows a strength meter as the new password is typed', async () => {
    renderPage();
    await userEvent.type(newPw(), 'abc');
    expect(screen.getByText('Weak')).toBeInTheDocument();
    await userEvent.clear(newPw());
    await userEvent.type(newPw(), 'Abcdefgh1234!');
    expect(screen.getByText('Strong')).toBeInTheDocument();
  });
});
