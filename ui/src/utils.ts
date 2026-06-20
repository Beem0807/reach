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
