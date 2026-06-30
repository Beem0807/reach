import type { ChangeEvent } from 'react';
import type { K8sRule } from '../types';

// Write verbs an operator can approve. MUST mirror the backend's approvable
// writes: _K8S_WRITE_VERBS + _K8S_COMPOUND_WRITES (backend/shared/policy.py). The
// backend gates submissions on those, so a write missing here cannot be
// pre-approved. "double verbs" (rollout/auth) carry the sub-subcommand in the
// verb itself, e.g. "rollout restart", so reads like "rollout status" are never
// approved and writes are distinguished. "*" means "any write verb"; reads are
// always allowed and never approved. A backend parity test guards this list.
export const K8S_WRITE_VERBS = [
  '*', 'create', 'apply', 'delete', 'edit', 'patch', 'replace', 'scale',
  'autoscale', 'expose', 'run', 'label', 'annotate', 'taint', 'cordon',
  'uncordon', 'drain', 'exec', 'attach', 'cp', 'port-forward', 'proxy', 'debug',
  // Double verbs - sub-subcommand is part of the verb.
  'rollout restart', 'rollout undo', 'rollout pause', 'rollout resume',
  'auth reconcile', 'apply set-last-applied', 'apply edit-last-applied',
  'set image', 'set env', 'set resources', 'set selector',
  'set serviceaccount', 'set subject', 'certificate approve', 'certificate deny',
];

// Common resources offered as dropdown suggestions, grouped roughly by area.
// Not exhaustive - the field stays free-text so CRDs (e.g. certificates,
// virtualservices) and other subresources can still be typed in.
export const K8S_COMMON_RESOURCES = [
  '*',
  // Workloads
  'pods', 'deployments', 'statefulsets', 'daemonsets', 'replicasets',
  'replicationcontrollers', 'jobs', 'cronjobs', 'controllerrevisions',
  // Networking
  'services', 'ingresses', 'ingressclasses', 'networkpolicies',
  'endpoints', 'endpointslices',
  // Config & storage
  'configmaps', 'secrets', 'persistentvolumeclaims', 'persistentvolumes',
  'storageclasses', 'volumeattachments', 'resourcequotas', 'limitranges',
  // Cluster
  'namespaces', 'nodes', 'events', 'serviceaccounts', 'componentstatuses',
  // RBAC
  'roles', 'rolebindings', 'clusterroles', 'clusterrolebindings',
  // Autoscaling, availability & scheduling
  'horizontalpodautoscalers', 'poddisruptionbudgets', 'priorityclasses',
  'runtimeclasses',
  // Coordination, API extensions & admission
  'leases', 'customresourcedefinitions', 'apiservices',
  'mutatingwebhookconfigurations', 'validatingwebhookconfigurations',
  'certificatesigningrequests',
  // Common subresources
  'pods/exec', 'pods/log', 'pods/portforward',
  'deployments/scale', 'statefulsets/scale', 'replicasets/scale',
];

export const EMPTY_RULE: K8sRule = { verb: 'create', resource: '*', namespace: '*', name: '*' };

type WildField = 'resource' | 'namespace' | 'name';

// A structured editor for a k8s approval rule. resource/namespace/name default
// to "*" (match anything). While editing they can be cleared (backspace removes
// the "*"); an empty field falls back to "*" on blur, so leaving a box blank
// still means "any".
export function K8sRuleForm({ value, onChange }: { value: K8sRule; onChange: (r: K8sRule) => void }) {
  const set = (patch: Partial<K8sRule>) => onChange({ ...value, ...patch });
  const field = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent';

  // Raw edit while focused; on blur, trim and fall back to "*" when empty.
  const edit = (key: WildField) => (e: ChangeEvent<HTMLInputElement>) => set({ [key]: e.target.value } as Partial<K8sRule>);
  const wildOnBlur = (key: WildField) => () => {
    const v = (value[key] ?? '').trim() || '*';
    if (v !== value[key]) set({ [key]: v } as Partial<K8sRule>);
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">Verb</label>
          <select value={value.verb} onChange={e => set({ verb: e.target.value })} className={`${field} bg-white font-mono`}>
            {K8S_WRITE_VERBS.map(v => <option key={v} value={v}>{v === '*' ? '* (any write)' : v}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">Resource</label>
          <input
            list="k8s-resource-options"
            value={value.resource}
            onChange={edit('resource')}
            onBlur={wildOnBlur('resource')}
            placeholder="pods, deployments, * …"
            className={`${field} font-mono`}
          />
          <datalist id="k8s-resource-options">
            {K8S_COMMON_RESOURCES.map(r => <option key={r} value={r} />)}
          </datalist>
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">Namespace</label>
          <input value={value.namespace} onChange={edit('namespace')} onBlur={wildOnBlur('namespace')} placeholder="team-a or *" className={`${field} font-mono`} />
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">Name</label>
          <input value={value.name} onChange={edit('name')} onBlur={wildOnBlur('name')} placeholder="specific object or *" className={`${field} font-mono`} />
        </div>
      </div>
      <p className="text-xs text-gray-400">
        <span className="font-mono">*</span> matches anything for that field. Example: <span className="font-mono">delete · pods · team-a · *</span> permits deleting any pod in <span className="font-mono">team-a</span>.
      </p>
    </div>
  );
}
