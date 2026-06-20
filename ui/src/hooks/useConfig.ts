import { useState, useCallback } from 'react';
import type { Config } from '../types';

const KEY = 'reach_admin_config';

function load(): Config | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as Partial<Config>;
    if (c.apiUrl && c.adminToken) return c as Config;
  } catch {
    // ignore
  }
  return null;
}

export function useConfig() {
  const [config, setConfigState] = useState<Config | null>(load);

  const setConfig = useCallback((c: Config) => {
    const clean = { ...c, apiUrl: c.apiUrl.replace(/\/$/, '') };
    localStorage.setItem(KEY, JSON.stringify(clean));
    setConfigState(clean);
  }, []);

  const clearConfig = useCallback(() => {
    localStorage.removeItem(KEY);
    setConfigState(null);
  }, []);

  return { config, setConfig, clearConfig };
}
