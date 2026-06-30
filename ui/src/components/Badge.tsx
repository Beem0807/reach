type BadgeConfig = { cls: string; dot: string };

const CONFIG: Record<string, BadgeConfig> = {
  // Agent status
  ACTIVE:    { cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20', dot: 'bg-emerald-500' },
  INACTIVE:  { cls: 'bg-yellow-50 text-yellow-700 ring-yellow-600/20',   dot: 'bg-yellow-400' },
  CREATED:   { cls: 'bg-sky-50 text-sky-700 ring-sky-600/20',            dot: 'bg-sky-500' },
  REVOKED:   { cls: 'bg-red-50 text-red-600 ring-red-600/20',            dot: 'bg-red-500' },
  DELETED:   { cls: 'bg-gray-100 text-gray-500 ring-gray-500/20',        dot: 'bg-gray-400' },
  // Agent type
  k8s:       { cls: 'bg-blue-50 text-blue-700 ring-blue-600/20',         dot: 'bg-blue-500' },
  host:      { cls: 'bg-slate-50 text-slate-600 ring-slate-500/20',      dot: 'bg-slate-400' },
  // Mode
  wild:      { cls: 'bg-orange-50 text-orange-700 ring-orange-600/20',   dot: 'bg-orange-400' },
  readonly:  { cls: 'bg-sky-50 text-sky-700 ring-sky-600/20',            dot: 'bg-sky-400' },
  approved:  { cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',dot: 'bg-emerald-500' },
  // Access level
  open:       { cls: 'bg-gray-50 text-gray-600 ring-gray-500/20',        dot: 'bg-gray-400' },
  elevated:   { cls: 'bg-amber-50 text-amber-700 ring-amber-600/20',     dot: 'bg-amber-400' },
  managed:    { cls: 'bg-violet-50 text-violet-700 ring-violet-600/20',  dot: 'bg-violet-500' },
  restricted: { cls: 'bg-red-50 text-red-600 ring-red-600/20',           dot: 'bg-red-500' },
  // Approval status
  pending:   { cls: 'bg-amber-50 text-amber-700 ring-amber-600/20',      dot: 'bg-amber-400' },
  denied:    { cls: 'bg-red-50 text-red-600 ring-red-600/20',            dot: 'bg-red-500' },
  expired:   { cls: 'bg-gray-50 text-gray-500 ring-gray-500/20',         dot: 'bg-gray-400' },
  // Job status
  PENDING:   { cls: 'bg-amber-50 text-amber-700 ring-amber-600/20',      dot: 'bg-amber-400' },
  RUNNING:   { cls: 'bg-blue-50 text-blue-700 ring-blue-600/20',         dot: 'bg-blue-500 animate-pulse' },
  SUCCEEDED: { cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',dot: 'bg-emerald-500' },
  FAILED:    { cls: 'bg-red-50 text-red-600 ring-red-600/20',            dot: 'bg-red-500' },
  REJECTED:  { cls: 'bg-orange-50 text-orange-700 ring-orange-600/20',   dot: 'bg-orange-400' },
  EXPIRED:   { cls: 'bg-gray-50 text-gray-500 ring-gray-500/20',         dot: 'bg-gray-400' },
};

export function Badge({ value }: { value: string }) {
  const { cls, dot } = CONFIG[value] ?? { cls: 'bg-gray-50 text-gray-600 ring-gray-500/20', dot: 'bg-gray-400' };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${dot}`} />
      {value}
    </span>
  );
}
