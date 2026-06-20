import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTenantConfig } from '../hooks/useTenantConfig';
import type { TenantConfig } from '../types';

const KEY = 'reach_tenant_config';

const VALID_CONFIG: TenantConfig = {
  apiUrl: 'https://api.example.com',
  tenantToken: 'jwt_abc',
  tenantId: 'tenant_1',
  tenantName: 'Acme Corp',
  userId: 'user_1',
  username: 'alice',
  name: 'Alice',
  role: 'admin',
  mustResetPassword: false,
};

const store: Record<string, string> = {};
const mockStorage: Storage = {
  getItem: (k) => store[k] ?? null,
  setItem: (k, v) => { store[k] = v; },
  removeItem: (k) => { delete store[k]; },
  clear: () => { Object.keys(store).forEach(k => delete store[k]); },
  length: 0,
  key: () => null,
};

beforeEach(() => {
  vi.stubGlobal('localStorage', mockStorage);
  mockStorage.clear();
});

describe('useTenantConfig', () => {
  it('returns null when localStorage is empty', () => {
    const { result } = renderHook(() => useTenantConfig());
    expect(result.current.config).toBeNull();
  });

  it('loads config from localStorage on mount', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useTenantConfig());
    expect(result.current.config).toEqual(VALID_CONFIG);
  });

  it('returns null when stored value is malformed JSON', () => {
    localStorage.setItem(KEY, 'not-json');
    const { result } = renderHook(() => useTenantConfig());
    expect(result.current.config).toBeNull();
  });

  it('setConfig stores config in localStorage', () => {
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.setConfig(VALID_CONFIG); });
    const stored = JSON.parse(localStorage.getItem(KEY)!);
    expect(stored.tenantId).toBe('tenant_1');
    expect(stored.tenantToken).toBe('jwt_abc');
  });

  it('setConfig updates the state', () => {
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.setConfig(VALID_CONFIG); });
    expect(result.current.config).toEqual(VALID_CONFIG);
  });

  it('clearConfig removes item from localStorage', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.clearConfig(); });
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('clearConfig sets state to null', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.clearConfig(); });
    expect(result.current.config).toBeNull();
  });

  it('updateConfig applies a partial patch', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.updateConfig({ tenantName: 'Updated Corp' }); });
    expect(result.current.config?.tenantName).toBe('Updated Corp');
    expect(result.current.config?.tenantId).toBe('tenant_1');
  });

  it('updateConfig persists the patch to localStorage', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.updateConfig({ username: 'bob' }); });
    const stored = JSON.parse(localStorage.getItem(KEY)!);
    expect(stored.username).toBe('bob');
  });

  it('updateConfig does nothing when config is null', () => {
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.updateConfig({ tenantName: 'X' }); });
    expect(result.current.config).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('updateConfig preserves mustResetPassword field', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...VALID_CONFIG, mustResetPassword: true }));
    const { result } = renderHook(() => useTenantConfig());
    act(() => { result.current.updateConfig({ username: 'carol' }); });
    expect(result.current.config?.mustResetPassword).toBe(true);
  });
});
