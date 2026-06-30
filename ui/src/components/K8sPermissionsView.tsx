import type { K8sPermissions, K8sResourceRule } from '../types';

// Translate raw RBAC verbs into a readable access label. RBAC is additive, so we
// describe what the rule grants rather than naming a built-in role.
function describeVerbs(verbs: string[]): { label: string; level: 'read' | 'write' | 'full' } {
  const v = new Set(verbs.map(x => x.toLowerCase()));
  if (v.has('*')) return { label: 'Full control', level: 'full' };
  const parts: string[] = [];
  if (['get', 'list', 'watch'].some(x => v.has(x))) parts.push('Read');
  if (['create', 'update', 'patch'].some(x => v.has(x))) parts.push('Write');
  if (v.has('delete') || v.has('deletecollection')) parts.push('Delete');
  const level: 'read' | 'write' = parts.includes('Write') || parts.includes('Delete') ? 'write' : 'read';
  return { label: parts.join(' + ') || verbs.join(', '), level };
}

// Dangerous capabilities surfaced straight from the rule, independent of the
// access label - these catch grants you'd never have thought to look for.
function dangerFlags(rule: K8sResourceRule): string[] {
  const verbs = new Set(rule.verbs.map(x => x.toLowerCase()));
  const writes = verbs.has('*') || ['create', 'update', 'patch', 'delete', 'deletecollection'].some(x => verbs.has(x));
  const resources = (rule.resources ?? []).map(r => r.toLowerCase());
  const flags: string[] = [];
  if (resources.includes('*') && verbs.has('*')) flags.push('full cluster access');
  if (writes && resources.includes('secrets')) flags.push('secrets write');
  if (writes && resources.some(r => r.includes('rolebindings'))) flags.push('RBAC escalation');
  if (resources.some(r => r === 'pods/exec' || r === 'pods/attach')) flags.push('pod exec');
  return flags;
}

function levelClass(level: 'read' | 'write' | 'full'): string {
  if (level === 'full') return 'bg-red-50 text-red-700 ring-red-600/20';
  if (level === 'write') return 'bg-amber-50 text-amber-700 ring-amber-600/20';
  return 'bg-sky-50 text-sky-700 ring-sky-600/20';
}

function fmtResources(rule: K8sResourceRule): string {
  const res = rule.resources ?? [];
  if (res.length === 0) return '(no resources)';
  const label = res.includes('*') ? 'all resources' : res.join(', ');
  if (rule.resource_names && rule.resource_names.length) {
    return `${label} [${rule.resource_names.join(', ')}]`;
  }
  return label;
}

function RuleRow({ rule }: { rule: K8sResourceRule }) {
  const { label, level } = describeVerbs(rule.verbs);
  const flags = dangerFlags(rule);
  return (
    <div className="px-3.5 py-2.5 flex items-start gap-3">
      <span className={`shrink-0 inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${levelClass(level)}`}>
        {label}
      </span>
      <div className="min-w-0">
        <div className="text-xs text-gray-800 font-mono break-words">{fmtResources(rule)}</div>
        {rule.api_groups && rule.api_groups.some(g => g) && (
          <div className="text-[10px] text-gray-400 font-mono">
            {rule.api_groups.map(g => g || 'core').join(', ')}
          </div>
        )}
        {flags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {flags.map(f => (
              <span key={f} className="inline-flex items-center gap-0.5 text-[10px] font-medium bg-red-50 text-red-700 ring-1 ring-inset ring-red-600/20 rounded px-1.5 py-0.5">
                ⚠ {f}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RuleSection({ title, rules }: { title: string; rules: K8sResourceRule[] }) {
  return (
    <div>
      <div className="px-3.5 py-1.5 bg-gray-50/70 border-b border-gray-100">
        <span className="text-[11px] font-semibold text-gray-600">{title}</span>
      </div>
      <div className="divide-y divide-gray-100">
        {rules.length === 0 && (
          <div className="px-3.5 py-3 text-xs text-gray-400">No resource permissions.</div>
        )}
        {rules.map((rule, i) => <RuleRow key={i} rule={rule} />)}
      </div>
    </div>
  );
}

export function K8sPermissionsView({
  permissions,
  drift,
  onAcknowledge,
}: {
  permissions: K8sPermissions;
  drift?: boolean;
  onAcknowledge?: () => void;
}) {
  const clusterWide = permissions.cluster_wide ?? [];
  const namespaces = (permissions.namespaces ?? []).filter(ns => (ns.resource_rules ?? []).length > 0);
  const hasBaseline = clusterWide.length > 0;
  const nothing = !hasBaseline && namespaces.length === 0;
  return (
    <div className="rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-gray-50 border-b border-gray-200">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-bold text-gray-700 uppercase tracking-wider">Cluster permissions</span>
          {permissions.incomplete && (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20 rounded-full px-2 py-0.5">
              partial - could not fully evaluate
            </span>
          )}
          {permissions.truncated && (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20 rounded-full px-2 py-0.5">
              truncated - too large, some entries omitted
            </span>
          )}
          {drift && (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium bg-red-50 text-red-700 ring-1 ring-inset ring-red-600/20 rounded-full px-2 py-0.5">
              ⚠ changed - needs acknowledgement
            </span>
          )}
        </div>
        {drift && onAcknowledge && (
          <button
            onClick={onAcknowledge}
            className="text-[11px] font-semibold text-indigo-600 hover:text-indigo-700 border border-indigo-200 hover:bg-indigo-50 rounded-md px-2 py-1 transition-colors"
          >
            Acknowledge
          </button>
        )}
      </div>

      <div className="divide-y divide-gray-200">
        {nothing && (
          <div className="px-3.5 py-3 text-xs text-gray-400">No permissions reported.</div>
        )}
        {hasBaseline && <RuleSection title="Effective in every namespace" rules={clusterWide} />}
        {namespaces.map(ns => (
          <RuleSection
            key={ns.namespace}
            title={`${hasBaseline ? 'Additionally in' : 'In'} ${ns.namespace}`}
            rules={ns.resource_rules}
          />
        ))}
      </div>
    </div>
  );
}
