import type { Approval, K8sRule } from '../types';

// A wildcard field is shown muted ("any") since it matches everything.
function RuleField({ label, value }: { label: string; value: string }) {
  const wild = value === '*' || value === '';
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-[10px] uppercase tracking-wider text-gray-400">{label}</span>
      <span className={wild ? 'text-gray-400 italic' : 'font-mono text-gray-800'}>
        {wild ? 'any' : value}
      </span>
    </span>
  );
}

export function K8sRuleChips({ rule }: { rule: K8sRule }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <RuleField label="verb" value={rule.verb} />
      <RuleField label="resource" value={rule.resource} />
      <RuleField label="namespace" value={rule.namespace} />
      <RuleField label="name" value={rule.name} />
    </span>
  );
}

// Renders an approval's target appropriately: a structured rule for k8s agents,
// or the raw command string for host agents.
export function ApprovalTarget({ approval }: { approval: Approval }) {
  if (approval.k8s_rule) {
    return <K8sRuleChips rule={approval.k8s_rule} />;
  }
  return (
    <span className="font-mono text-sm text-gray-800 bg-gray-100 px-2 py-0.5 rounded block truncate">
      {approval.command}
    </span>
  );
}

export function isK8sApproval(a: Approval): boolean {
  return !!a.k8s_rule || a.agent_type === 'k8s';
}

// Free-text match across an approval's command, k8s rule fields, agent, and
// requester. Used to search the full (kind-filtered) set before pagination, so
// results are never limited to the current page.
export function approvalMatchesQuery(a: Approval, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const parts = [a.command ?? '', a.agent_hostname ?? '', a.requester_name ?? '', a.requested_by ?? ''];
  if (a.k8s_rule) {
    const r = a.k8s_rule;
    parts.push(r.verb, r.resource, r.namespace, r.name);
  }
  return parts.some(p => p.toLowerCase().includes(q));
}
