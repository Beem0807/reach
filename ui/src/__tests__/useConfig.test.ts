import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useConfig } from '../hooks/useConfig';
import type { Config } from '../types';

const KEY = 'reach_admin_config';

const VALID_CONFIG: Config = {
  apiUrl: 'https://api.example.com',
  adminToken: 'tok_secret',
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

describe('useConfig', () => {
  it('returns null when localStorage is empty', () => {
    const { result } = renderHook(() => useConfig());
    expect(result.current.config).toBeNull();
  });

  it('loads config from localStorage on mount', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useConfig());
    expect(result.current.config).toEqual(VALID_CONFIG);
  });

  it('returns null when stored JSON lacks required fields', () => {
    localStorage.setItem(KEY, JSON.stringify({ apiUrl: 'https://x.com' }));
    const { result } = renderHook(() => useConfig());
    expect(result.current.config).toBeNull();
  });

  it('returns null when stored value is malformed JSON', () => {
    localStorage.setItem(KEY, 'not-json');
    const { result } = renderHook(() => useConfig());
    expect(result.current.config).toBeNull();
  });

  it('setConfig stores config in localStorage', () => {
    const { result } = renderHook(() => useConfig());
    act(() => { result.current.setConfig(VALID_CONFIG); });
    const stored = JSON.parse(localStorage.getItem(KEY)!);
    expect(stored.apiUrl).toBe('https://api.example.com');
    expect(stored.adminToken).toBe('tok_secret');
  });

  it('setConfig updates the state', () => {
    const { result } = renderHook(() => useConfig());
    act(() => { result.current.setConfig(VALID_CONFIG); });
    expect(result.current.config).toEqual(VALID_CONFIG);
  });

  it('setConfig strips trailing slash from apiUrl', () => {
    const { result } = renderHook(() => useConfig());
    act(() => { result.current.setConfig({ ...VALID_CONFIG, apiUrl: 'https://api.example.com/' }); });
    expect(result.current.config?.apiUrl).toBe('https://api.example.com');
    const stored = JSON.parse(localStorage.getItem(KEY)!);
    expect(stored.apiUrl).toBe('https://api.example.com');
  });

  it('clearConfig removes item from localStorage', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useConfig());
    act(() => { result.current.clearConfig(); });
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('clearConfig sets state to null', () => {
    localStorage.setItem(KEY, JSON.stringify(VALID_CONFIG));
    const { result } = renderHook(() => useConfig());
    act(() => { result.current.clearConfig(); });
    expect(result.current.config).toBeNull();
  });
});
