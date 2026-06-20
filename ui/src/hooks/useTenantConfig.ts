import { useCallback, useState } from 'react';
import type { TenantConfig } from '../types';

const KEY = 'reach_tenant_config';

function load(): TenantConfig | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as TenantConfig) : null;
  } catch {
    return null;
  }
}

export function useTenantConfig() {
  const [config, setConfigState] = useState<TenantConfig | null>(load);

  const setConfig = useCallback((c: TenantConfig) => {
    localStorage.setItem(KEY, JSON.stringify(c));
    setConfigState(c);
  }, []);

  const clearConfig = useCallback(() => {
    localStorage.removeItem(KEY);
    setConfigState(null);
  }, []);

  const updateConfig = useCallback((patch: Partial<TenantConfig>) => {
    setConfigState(prev => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      localStorage.setItem(KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return { config, setConfig, clearConfig, updateConfig };
}
