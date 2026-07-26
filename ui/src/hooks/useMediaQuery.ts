import { useState, useEffect } from 'react';

/** Reactive CSS media-query match (e.g. `useMediaQuery('(min-width: 768px)')`).
 * Falls back to `false` where `matchMedia` is unavailable (older jsdom, SSR). */
export function useMediaQuery(query: string): boolean {
  const supported = typeof window !== 'undefined' && typeof window.matchMedia === 'function';
  const [matches, setMatches] = useState(() => (supported ? window.matchMedia(query).matches : false));
  useEffect(() => {
    if (!supported) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query, supported]);
  return matches;
}
