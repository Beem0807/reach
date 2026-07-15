import type { Approval, K8sRule, HostRule } from '../types';

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

// A host approval rule {bin, args[]}: the binary plus positional args, each a literal
// or "*" (shown muted as "any"). Mirrors K8sRuleChips.
export function HostRuleChips({ rule }: { rule: HostRule }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <RuleField label="bin" value={rule.bin} />
      {rule.args.length === 0
        ? <span className="text-[10px] uppercase tracking-wider text-gray-400 italic">no args</span>
        : rule.args.map((a, i) => <RuleField key={i} label={`arg ${i + 1}`} value={a} />)}
    </span>
  );
}

// Renders an approval's target appropriately: a structured rule for k8s agents, a
// structured host rule for host agents, or the raw command string (legacy host approval).
export function ApprovalTarget({ approval }: { approval: Approval }) {
  if (approval.k8s_rule) {
    return <K8sRuleChips rule={approval.k8s_rule} />;
  }
  if (approval.host_rule) {
    return <HostRuleChips rule={approval.host_rule} />;
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

// Renders which agent or fleet an approval applies to. Fleet-scoped approvals
// apply to every member of the fleet, so they're badged distinctly.
export function ApprovalScope({ approval }: { approval: Approval }) {
  const isFleet = approval.scope === 'fleet' || (!!approval.fleet_id && !approval.agent_id);
  if (isFleet) {
    return (
      <div className="flex items-center gap-1.5">
        <div className="w-5 h-5 rounded bg-violet-100 flex items-center justify-center shrink-0">
          <svg className="w-3 h-3 text-violet-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
          </svg>
        </div>
        <span className="text-sm text-gray-700 font-medium whitespace-nowrap">
          {approval.fleet_name ?? approval.fleet_id}
        </span>
        <span className="text-[10px] uppercase tracking-wider text-violet-600 bg-violet-50 px-1.5 py-0.5 rounded">fleet</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-5 h-5 rounded bg-indigo-100 flex items-center justify-center shrink-0">
        <svg className="w-3 h-3 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3" />
        </svg>
      </div>
      <span className="text-sm text-gray-700 font-medium whitespace-nowrap">
        {approval.agent_hostname ?? approval.agent_id}
      </span>
    </div>
  );
}

// Free-text match across an approval's command, k8s rule fields, agent, and
// requester. Used to search the full (kind-filtered) set before pagination, so
// results are never limited to the current page.
export function approvalMatchesQuery(a: Approval, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const parts = [a.command ?? '', a.agent_hostname ?? '', a.fleet_name ?? '', a.requester_name ?? '', a.requested_by ?? ''];
  if (a.k8s_rule) {
    const r = a.k8s_rule;
    parts.push(r.verb, r.resource, r.namespace, r.name);
  }
  if (a.host_rule) {
    parts.push(a.host_rule.bin, ...a.host_rule.args);
  }
  return parts.some(p => p.toLowerCase().includes(q));
}
