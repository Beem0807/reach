import { describe, it, expect, vi, beforeEach } from 'vitest';
import { relTime, tenantInitials, tenantPalette, userInitials } from '../utils';

// ---------------------------------------------------------------------------
// relTime
// ---------------------------------------------------------------------------

describe('relTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T12:00:00Z'));
  });

  it('returns - for missing input', () => {
    expect(relTime()).toBe('-');
    expect(relTime(undefined)).toBe('-');
  });

  it('formats seconds ago', () => {
    const iso = new Date(Date.now() - 30_000).toISOString();
    expect(relTime(iso)).toBe('30s ago');
  });

  it('formats minutes ago', () => {
    const iso = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(relTime(iso)).toBe('5m ago');
  });

  it('formats hours ago', () => {
    const iso = new Date(Date.now() - 3 * 3_600_000).toISOString();
    expect(relTime(iso)).toBe('3h ago');
  });

  it('formats days ago', () => {
    const iso = new Date(Date.now() - 2 * 86_400_000).toISOString();
    expect(relTime(iso)).toBe('2d ago');
  });
});

// ---------------------------------------------------------------------------
// tenantInitials
// ---------------------------------------------------------------------------

describe('tenantInitials', () => {
  it('takes first letter of each word (up to 2)', () => {
    expect(tenantInitials('Acme Corp')).toBe('AC');
  });

  it('uppercases letters', () => {
    expect(tenantInitials('acme corp')).toBe('AC');
  });

  it('handles single word', () => {
    expect(tenantInitials('Acme')).toBe('A');
  });

  it('ignores extra words beyond two', () => {
    expect(tenantInitials('Acme Corp Ltd')).toBe('AC');
  });

  it('returns ? for empty string', () => {
    expect(tenantInitials('')).toBe('?');
  });
});

// ---------------------------------------------------------------------------
// userInitials
// ---------------------------------------------------------------------------

describe('userInitials', () => {
  it('takes first letter of first two alphabetic words', () => {
    expect(userInitials('Alice Smith')).toBe('AS');
  });

  it('skips non-alphabetic words like (revoked)', () => {
    expect(userInitials('Carol (revoked)')).toBe('C');
  });

  it('handles name with only non-alpha parenthetical', () => {
    expect(userInitials('Bob (admin) Jones')).toBe('BJ');
  });

  it('uppercases', () => {
    expect(userInitials('alice')).toBe('A');
  });

  it('returns ? for empty string', () => {
    expect(userInitials('')).toBe('?');
  });
});

// ---------------------------------------------------------------------------
// tenantPalette
// ---------------------------------------------------------------------------

describe('tenantPalette', () => {
  it('returns an array of 4 class strings', () => {
    const result = tenantPalette('tenant_abc123');
    expect(result).toHaveLength(4);
    result.forEach(cls => expect(typeof cls).toBe('string'));
  });

  it('is deterministic - same id always returns same palette', () => {
    expect(tenantPalette('tenant_abc')).toEqual(tenantPalette('tenant_abc'));
  });

  it('different ids can return different palettes', () => {
    const palettes = new Set(
      ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map(id => tenantPalette(id)[0])
    );
    expect(palettes.size).toBeGreaterThan(1);
  });
});
