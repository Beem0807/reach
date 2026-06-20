import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LoginPage } from '../pages/LoginPage';
import * as api from '../api';

beforeEach(() => { vi.restoreAllMocks(); });

describe('LoginPage', () => {
  it('calls adminLogin and forwards token on success', async () => {
    vi.spyOn(api, 'adminLogin').mockResolvedValue('admin_tok');
    const onLogin = vi.fn();
    render(<LoginPage onLogin={onLogin} />);
    await userEvent.type(screen.getByPlaceholderText('Enter password'), 'hunter2');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledTimes(1));
    expect(onLogin.mock.calls[0][0]).toMatchObject({ adminToken: 'admin_tok' });
    expect(api.adminLogin).toHaveBeenCalledWith(expect.any(String), 'hunter2');
  });

  it('shows "Invalid password." when login fails', async () => {
    vi.spyOn(api, 'adminLogin').mockRejectedValue(new Error('nope'));
    const onLogin = vi.fn();
    render(<LoginPage onLogin={onLogin} />);
    await userEvent.type(screen.getByPlaceholderText('Enter password'), 'wrong');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Invalid password.')).toBeInTheDocument();
    expect(onLogin).not.toHaveBeenCalled();
  });

  it('shows tenant switch link only when handler provided', () => {
    const { unmount } = render(<LoginPage onLogin={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Tenant sign in' })).not.toBeInTheDocument();
    unmount();

    const onSwitchToTenant = vi.fn();
    render(<LoginPage onLogin={vi.fn()} onSwitchToTenant={onSwitchToTenant} />);
    fireEvent.click(screen.getByRole('button', { name: 'Tenant sign in' }));
    expect(onSwitchToTenant).toHaveBeenCalledTimes(1);
  });
});
