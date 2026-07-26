import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { formatTs, currentZone, useTimezone, TimezoneProvider, TimezoneToggle } from '../timezone';

// A fixed instant: 04:06 UTC. In UTC display that's "04:06"; local depends on the runner.
const ISO = '2026-07-26T04:06:00+00:00';
const HM: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };

function renderToggle() {
  return render(<TimezoneProvider><TimezoneToggle /></TimezoneProvider>);
}

describe('timezone preference', () => {
  beforeEach(() => {
    // The test env's localStorage is incomplete; provide a real in-memory one so persistence
    // is exercised (the module itself degrades gracefully when storage is unavailable).
    const store: Record<string, string> = {};
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
      clear: () => { for (const k of Object.keys(store)) delete store[k]; },
    });
    // Reset the module mirror to the default (Local), then unmount so each test starts with a
    // single provider/toggle in the DOM.
    const r = renderToggle();
    fireEvent.click(screen.getByRole('button', { name: 'Local' }));
    r.unmount();
  });

  it('formatTs returns a dash for empty input', () => {
    expect(formatTs(undefined)).toBe('-');
    expect(formatTs(null)).toBe('-');
  });

  it('renders in UTC when the toggle is set to UTC', () => {
    renderToggle();
    fireEvent.click(screen.getByRole('button', { name: 'UTC' }));
    expect(currentZone()).toBe('UTC');
    expect(formatTs(ISO, HM)).toBe('04:06');   // UTC wall-clock for that instant
  });

  it('renders in the local zone when set to Local', () => {
    renderToggle();
    fireEvent.click(screen.getByRole('button', { name: 'Local' }));
    expect(currentZone()).toBe('local');
    // Local rendering equals the browser's own local formatting of the same instant.
    const expected = new Date(ISO).toLocaleString(undefined, HM);
    expect(formatTs(ISO, HM)).toBe(expected);
  });

  it('persists the choice to localStorage', () => {
    renderToggle();
    fireEvent.click(screen.getByRole('button', { name: 'UTC' }));
    expect(localStorage.getItem('reach.timezone')).toBe('UTC');
  });

  it('re-renders a subscribed page when the zone toggles (the reflow fix)', () => {
    // A page that subscribes via useTimezone() must reflow its formatTs output on toggle,
    // even though it's passed as `children` to the provider (React would otherwise bail out).
    function DemoPage() {
      useTimezone();
      return <div data-testid="ts">{formatTs(ISO, HM)}</div>;
    }
    render(<TimezoneProvider><TimezoneToggle /><DemoPage /></TimezoneProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Local' }));
    const localText = screen.getByTestId('ts').textContent;
    fireEvent.click(screen.getByRole('button', { name: 'UTC' }));
    expect(screen.getByTestId('ts').textContent).toBe('04:06');   // reflowed to UTC
    // And back to local reflows again.
    fireEvent.click(screen.getByRole('button', { name: 'Local' }));
    expect(screen.getByTestId('ts').textContent).toBe(localText);
  });
});
