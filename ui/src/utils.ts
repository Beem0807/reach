import type { Agent, Fleet } from './types';

export function relTime(iso?: string): string {
  if (!iso) return '-';
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function tenantInitials(name: string): string {
  return name.split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('') || '?';
}

export function userInitials(name: string): string {
  return name.split(/\s+/)
    .filter(w => /^[a-zA-Z]/.test(w))
    .slice(0, 2)
    .map(w => w[0].toUpperCase())
    .join('') || '?';
}

const PALETTE = [
  ['bg-indigo-500',  'bg-indigo-600/10',  'border-indigo-200',  'text-indigo-700'],
  ['bg-violet-500',  'bg-violet-600/10',  'border-violet-200',  'text-violet-700'],
  ['bg-emerald-500', 'bg-emerald-600/10', 'border-emerald-200', 'text-emerald-700'],
  ['bg-sky-500',     'bg-sky-600/10',     'border-sky-200',     'text-sky-700'],
  ['bg-orange-500',  'bg-orange-600/10',  'border-orange-200',  'text-orange-700'],
  ['bg-rose-500',    'bg-rose-600/10',    'border-rose-200',    'text-rose-700'],
  ['bg-teal-500',    'bg-teal-600/10',    'border-teal-200',    'text-teal-700'],
  ['bg-amber-500',   'bg-amber-600/10',   'border-amber-200',   'text-amber-700'],
];

export function tenantPalette(id: string): string[] {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// ---------- Fleet grant-mismatch helpers (shared by Fleets + Agents pages) ----------
// A compact signature of the (member grants, fleet grants) pair; an accepted member
// exception stores this, so acceptance auto-invalidates (re-flags) if EITHER the fleet's
// grants OR the member's own grants change afterwards - it's scoped to the exact
// divergence the operator accepted.
export function grantsSignature(agent: Agent, fleet: Fleet): string {
  const b = (x?: boolean) => (x ? '1' : '0');
  return `${b(agent.grant_service_mgmt)}${b(agent.grant_docker)}-${b(fleet.grant_service_mgmt)}${b(fleet.grant_docker)}`;
}

// A member's grants *mismatch* the fleet when they differ from the fleet's desired grants.
export function memberGrantsMismatched(agent: Agent, fleet: Fleet): boolean {
  return !!agent.grant_service_mgmt !== !!fleet.grant_service_mgmt
      || !!agent.grant_docker !== !!fleet.grant_docker;
}

// True when the operator has *accepted* this member's mismatch for its current grants vs
// the fleet's current grants (an intentional exception, not a fix). Any later change on
// either side re-flags it.
export function memberMismatchAccepted(agent: Agent, fleet: Fleet): boolean {
  return !!agent.grants_exception && agent.grants_exception === grantsSignature(agent, fleet);
}

// A member is *flagged* (needs resolving: reconcile or accept) when it mismatches the
// fleet and the divergence hasn't been accepted.
export function memberMismatchFlagged(agent: Agent, fleet: Fleet): boolean {
  return memberGrantsMismatched(agent, fleet) && !memberMismatchAccepted(agent, fleet);
}
