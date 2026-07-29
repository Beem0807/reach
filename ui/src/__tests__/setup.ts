import '@testing-library/jest-dom';
import { configure } from '@testing-library/react';

// CI runners are slower and more contended than dev machines, so the default 1000ms
// findBy*/waitFor timeout can miss multi-step async flows (e.g. load tenants -> select tenant
// -> load users -> render the empty state). Give async assertions more headroom so they don't
// flake on CI (a different timing-sensitive test was failing each run otherwise).
configure({ asyncUtilTimeout: 5000 });

// jsdom has no matchMedia. Default to a desktop viewport (min-width queries match) so
// components using useMediaQuery render their desktop layout in tests.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string) => ({
    matches: /min-width/.test(query),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}
