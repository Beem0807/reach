# reach web console (UI)

The tenant + platform-admin console. Vite + React + TypeScript, built into the backend image and
served under `/ui` (Vite `base: './'`). Tests run on Vitest.

## Develop

```bash
npm ci            # install exactly from package-lock.json
npm run dev       # local dev server
npm run build     # tsc + vite build -> dist/  (what the backend image bundles)
npm run test      # vitest run
```

Assets (e.g. the logo mark) are **imported** from `src/assets/` - not referenced as absolute
`/foo.png` paths - so Vite rewrites their URLs correctly under the `/ui` base. Adding a `*.png`
import relies on the `vite/client` types in `src/vite-env.d.ts`.

## Lockfile

Commit `package-lock.json` whenever you change a dependency (`npm install` regenerates it) - CI's
`npm ci` is a strict lock↔package sync check, so a stale lock fails the build.

**Use a current npm (11+).** CI installs `npm@11` before `npm ci`, and a newer npm reads a lock
written by any older npm, so the lock's *shape* never breaks CI - just keep it in sync. If you're
on the older **npm 10** locally and hit an `EBADPLATFORM` error on an `@esbuild/*` package during
`npm ci` (npm 10 can't read a newer lock's optional-platform deps), upgrade: `npm i -g npm@11`. Node
stays **20** (see `.nvmrc`), matching CI and the backend image.
