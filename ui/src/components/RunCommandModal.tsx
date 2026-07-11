import { useState } from 'react';
import type { TenantConfig, Agent, Fleet, FanoutPreview, FanoutResult, JobPreview } from '../types';
import { createJob, fleetFanout, fanoutByTag, ApiError } from '../api';
import { Modal } from './Modal';
import { Spinner } from './Spinner';

// A command run launched from the console. Targets come in two flavours:
//   fixed  - the caller already knows the target (a row/menu action).
//   pooled - the caller passes a pool and the user picks inside the modal
//            (the top-level "Create job" / "New run" launchers).
// Runs (agent) submit a single job directly; fleet/tag fan-outs show a dry-run
// preview (blast radius + wave plan) that must be confirmed before dispatch.
// Callers are responsible for write-access gating (only pass writable targets).
export type RunTarget =
  | { kind: 'agent'; agent: Agent }
  | { kind: 'fleet'; fleet: Fleet }
  | { kind: 'tag'; tags: string[] }
  | { kind: 'agent-pick'; agents: Agent[] }
  | { kind: 'fleet-pick'; fleets: Fleet[] };

type Phase = 'input' | 'preview' | 'done';
type Scope = 'agent' | 'fleet' | 'tag';

const INPUT = 'w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500';
const PICK = 'w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 bg-white';

function PlanRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="text-sm text-slate-700 text-right">{children}</span>
    </div>
  );
}

export function RunCommandModal({ config, target, onClose }: {
  config: TenantConfig; target: RunTarget; onClose: () => void;
}) {
  const { apiUrl, tenantToken } = config;

  // Eligible pools (defensive write filtering on top of the caller's). Agents keep the
  // inactive ones for context (shown disabled) - only writable ones are listed at all;
  // active-first so the default selection is runnable.
  const pickAgents = target.kind === 'agent-pick'
    ? [...target.agents.filter(a => a.writable)].sort(
        (a, b) => Number(b.status === 'ACTIVE') - Number(a.status === 'ACTIVE')) : [];
  const pickFleets = target.kind === 'fleet-pick'
    ? target.fleets.filter(f => f.writable !== false && f.status === 'ACTIVE') : [];
  const tagOptions = target.kind === 'tag' ? target.tags : [];

  const scope: Scope =
    target.kind === 'agent' || target.kind === 'agent-pick' ? 'agent'
    : target.kind === 'fleet' || target.kind === 'fleet-pick' ? 'fleet'
    : 'tag';
  const isFanout = scope !== 'agent';
  const canPick = target.kind === 'agent-pick' || target.kind === 'fleet-pick' || target.kind === 'tag';

  const [command, setCommand] = useState('');
  const [maxTargets, setMaxTargets] = useState('');            // fleet only
  const [agentId, setAgentId] = useState(target.kind === 'agent' ? target.agent.agent_id
    : target.kind === 'agent-pick' ? (pickAgents.find(a => a.status === 'ACTIVE')?.agent_id ?? '') : '');
  const [fleetId, setFleetId] = useState(target.kind === 'fleet' ? target.fleet.fleet_id
    : target.kind === 'fleet-pick' ? (pickFleets[0]?.fleet_id ?? '') : '');
  const [tag, setTag] = useState(target.kind === 'tag' ? (target.tags[0] ?? '') : '');
  const [type, setType] = useState('');                        // tag only: '', 'host', 'k8s'
  const [phase, setPhase] = useState<Phase>('input');
  const [preview, setPreview] = useState<FanoutPreview | JobPreview | null>(null);
  const [result, setResult] = useState<FanoutResult | { job_id: string; status: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetLabel =
    scope === 'agent'
      ? (target.kind === 'agent' ? (target.agent.hostname || target.agent.agent_id)
         : (pickAgents.find(a => a.agent_id === agentId)?.hostname || agentId || '(pick an agent)'))
    : scope === 'fleet'
      ? (target.kind === 'fleet' ? target.fleet.name
         : (pickFleets.find(f => f.fleet_id === fleetId)?.name || '(pick a fleet)'))
    : (tag || '(pick a tag)');

  const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : 'Request failed');

  // Step 1: fetch the dry-run preview (blast radius + wave plan for fan-outs; the
  // command classification for a single agent). Every scope previews before dispatch.
  const doPreview = async () => {
    setBusy(true); setError(null);
    try {
      const mt = maxTargets.trim() ? Number(maxTargets) : undefined;
      if (scope === 'fleet' && mt !== undefined && (!Number.isInteger(mt) || mt < 1)) {
        setError('Max targets must be a whole number ≥ 1'); setBusy(false); return;
      }
      const p = scope === 'agent'
        ? await createJob(apiUrl, tenantToken, agentId, command.trim(), { dry_run: true })
        : scope === 'fleet'
        ? await fleetFanout(apiUrl, tenantToken, fleetId, { command: command.trim(), max_targets: mt, dry_run: true })
        : await fanoutByTag(apiUrl, tenantToken, { tag, command: command.trim(), type: type || undefined, dry_run: true });
      setPreview(p);
      setPhase('preview');
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  // Step 2 (fan-out) / only step (agent): dispatch for real.
  const doRun = async () => {
    setBusy(true); setError(null);
    try {
      if (scope === 'agent') {
        setResult(await createJob(apiUrl, tenantToken, agentId, command.trim()));
      } else if (scope === 'fleet') {
        const mt = maxTargets.trim() ? Number(maxTargets) : undefined;
        setResult(await fleetFanout(apiUrl, tenantToken, fleetId, { command: command.trim(), max_targets: mt }));
      } else {
        setResult(await fanoutByTag(apiUrl, tenantToken, { tag, command: command.trim(), type: type || undefined }));
      }
      setPhase('done');
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const targetChosen = scope === 'agent' ? !!agentId : scope === 'fleet' ? !!fleetId : !!tag;
  const canSubmit = command.trim().length > 0 && targetChosen && !busy;

  return (
    <Modal
      wide
      onClose={onClose}
      title={<span>Run command · <span className="font-normal text-slate-500">{scope} {targetLabel}</span></span>}
    >
      {/* ---- Input phase ---- */}
      {phase === 'input' && (
        <div className="space-y-4">
          {/* Target picker (pooled targets only) */}
          {target.kind === 'agent-pick' && (
            <label className="block">
              <span className="text-xs font-semibold text-slate-600">Agent</span>
              <select className={PICK} value={agentId} onChange={e => setAgentId(e.target.value)}>
                {pickAgents.length === 0 && <option value="">(no writable agents)</option>}
                {pickAgents.map(a => {
                  const inactive = a.status !== 'ACTIVE';
                  return (
                    <option key={a.agent_id} value={a.agent_id} disabled={inactive}
                      className={inactive ? 'text-slate-300 italic' : ''}>
                      {a.hostname || a.agent_id}{inactive ? ` - ${a.status.toLowerCase()} (can't run)` : ''}
                    </option>
                  );
                })}
              </select>
              {pickAgents.some(a => a.status !== 'ACTIVE') && (
                <span className="text-[11px] text-slate-400">Inactive agents are shown for reference but can’t receive jobs.</span>
              )}
            </label>
          )}
          {target.kind === 'fleet-pick' && (
            <label className="block">
              <span className="text-xs font-semibold text-slate-600">Fleet</span>
              <select className={PICK} value={fleetId} onChange={e => setFleetId(e.target.value)}>
                {pickFleets.length === 0 && <option value="">(no writable, active fleets)</option>}
                {pickFleets.map(f => <option key={f.fleet_id} value={f.fleet_id}>{f.name}</option>)}
              </select>
            </label>
          )}
          {target.kind === 'tag' && (
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs font-semibold text-slate-600">Tag</span>
                <select className={PICK} value={tag} onChange={e => setTag(e.target.value)}>
                  {tagOptions.length === 0 && <option value="">(no tags)</option>}
                  {tagOptions.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-semibold text-slate-600">Agent type <span className="font-normal text-slate-400">(optional)</span></span>
                <select className={PICK} value={type} onChange={e => setType(e.target.value)}>
                  <option value="">Auto (must be all one type)</option>
                  <option value="host">host</option>
                  <option value="k8s">k8s</option>
                </select>
              </label>
            </div>
          )}

          <label className="block">
            <span className="text-xs font-semibold text-slate-600">Command</span>
            <textarea
              className={INPUT} rows={3} value={command} autoFocus
              placeholder={scope === 'tag' && type === 'k8s' ? 'kubectl get pods' : 'uptime'}
              onChange={e => setCommand(e.target.value)}
            />
          </label>

          {scope === 'fleet' && (
            <label className="block max-w-[200px]">
              <span className="text-xs font-semibold text-slate-600">Max targets <span className="font-normal text-slate-400">(optional)</span></span>
              <input
                type="number" min={1} className={PICK} value={maxTargets}
                placeholder="all members" onChange={e => setMaxTargets(e.target.value)}
              />
            </label>
          )}

          <p className="text-[11px] text-slate-400">
            {isFanout
              ? 'You’ll see a preview of the blast radius and wave plan before anything runs.'
              : 'You’ll see a preview (read vs write, agent mode) and confirm before it runs.'}
          </p>

          {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg">{error}</div>}

          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={doPreview}
              disabled={!canSubmit}
              className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors shadow-sm"
            >
              {busy ? <Spinner className="w-4 h-4" /> : null}
              Preview
            </button>
            <button onClick={onClose} className="text-sm font-medium text-slate-500 hover:text-slate-800 px-2 py-2">Cancel</button>
            {canPick && !targetChosen && <span className="text-xs text-slate-400">Pick a target above.</span>}
          </div>
        </div>
      )}

      {/* ---- Preview phase (fan-out only) ---- */}
      {phase === 'preview' && preview && scope === 'agent' && (() => {
        const jp = preview as JobPreview;
        return (
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-200 px-4 py-3">
              <PlanRow label="Command"><span className="font-mono bg-slate-100 px-1.5 py-0.5 rounded text-slate-700 break-all">{jp.command}</span></PlanRow>
              <PlanRow label="Agent">{targetLabel}</PlanRow>
              <PlanRow label="Command type">
                {jp.is_write ? <span className="text-amber-600 font-medium">write</span> : <span className="text-slate-500">read</span>}
                {jp.type !== 'k8s' && <span className="text-slate-400 ml-1">· best-effort (host heuristic)</span>}
              </PlanRow>
              <PlanRow label="Agent mode">{jp.mode}</PlanRow>
              {jp.approval_required && (
                <PlanRow label="Approval"><span className="text-amber-600 font-medium">will be queued (not pre-approved)</span></PlanRow>
              )}
            </div>
            {/* On a host, read/write is a regex heuristic; the agent's Landlock sandbox is the
                real gate for readonly/approved - but wild mode is unsandboxed, so nothing on
                the agent blocks a write (and a write the heuristic misses would just run). */}
            {jp.mode === 'wild' && jp.type !== 'k8s' && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                <span className="font-semibold">Wild mode</span> - this host runs commands unsandboxed. Writes aren’t
                blocked on the agent, and the read/write label above is a best-effort guess.
              </p>
            )}
            {jp.mode !== 'wild' && jp.type !== 'k8s' && jp.is_write && !jp.approval_required && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                Classified as a <span className="font-semibold">write</span>. On this host the agent’s sandbox
                (Landlock) is the authoritative gate for <span className="font-mono">{jp.mode}</span> mode.
              </p>
            )}

            {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg">{error}</div>}

            <div className="flex items-center gap-3">
              <button
                onClick={doRun}
                disabled={busy}
                className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors shadow-sm"
              >
                {busy ? <Spinner className="w-4 h-4" /> : null}
                Confirm &amp; run
              </button>
              <button onClick={() => { setPhase('input'); setError(null); }} disabled={busy}
                className="text-sm font-medium text-slate-500 hover:text-slate-800 px-2 py-2 disabled:opacity-40">Back</button>
            </div>
          </div>
        );
      })()}

      {phase === 'preview' && preview && scope !== 'agent' && (() => {
        const fp = preview as FanoutPreview;
        return (
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-200 px-4 py-3">
              <PlanRow label="Command"><span className="font-mono bg-slate-100 px-1.5 py-0.5 rounded text-slate-700 break-all">{fp.command}</span></PlanRow>
              <PlanRow label={scope === 'fleet' ? 'Fleet' : 'Tag'}>{targetLabel}{fp.type ? ` · ${fp.type}` : ''}</PlanRow>
              <PlanRow label="Matched agents"><span className="font-semibold">{fp.matched}</span></PlanRow>
              <PlanRow label="Command type">
                {fp.is_write ? <span className="text-amber-600 font-medium">write</span> : <span className="text-slate-500">read</span>}
              </PlanRow>
              <PlanRow label="Wave size">{fp.wave_size}</PlanRow>
              <PlanRow label="Strategy">{fp.wave_strategy.toUpperCase()}</PlanRow>
              <PlanRow label="On failure">{fp.failure_policy.toUpperCase()}</PlanRow>
              {fp.approval_required && (
                <PlanRow label="Approval"><span className="text-amber-600 font-medium">required (approved mode)</span></PlanRow>
              )}
            </div>

            {fp.matched > 0 && fp.wave_total > 1 && (
              <p className="text-xs text-slate-500">
                This will create <span className="font-semibold text-slate-700">{fp.matched}</span> child jobs,
                released <span className="font-semibold text-slate-700">{fp.wave_size}</span> per wave
                over <span className="font-semibold text-slate-700">{fp.wave_total}</span> waves.
              </p>
            )}
            {fp.matched === 0 && (
              <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                No agents match - nothing will run.
              </p>
            )}
            {fp.skipped.length > 0 && (
              <p className="text-[11px] text-slate-400">{fp.skipped.length} agent(s) will be skipped (mode / access / not active).</p>
            )}

            {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg">{error}</div>}

            <div className="flex items-center gap-3">
              <button
                onClick={doRun}
                disabled={busy || fp.matched === 0}
                className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors shadow-sm"
              >
                {busy ? <Spinner className="w-4 h-4" /> : null}
                Confirm &amp; run
              </button>
              <button onClick={() => { setPhase('input'); setError(null); }} disabled={busy}
                className="text-sm font-medium text-slate-500 hover:text-slate-800 px-2 py-2 disabled:opacity-40">Back</button>
            </div>
          </div>
        );
      })()}

      {/* ---- Done phase ---- */}
      {phase === 'done' && result && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-emerald-700">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
            <span className="text-sm font-semibold">Dispatched</span>
          </div>
          {'job_id' in result ? (
            <div className="rounded-xl border border-slate-200 px-4 py-3">
              <PlanRow label="Job"><span className="font-mono text-xs">{result.job_id}</span></PlanRow>
              <PlanRow label="Status">{result.status}</PlanRow>
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 px-4 py-3">
              <PlanRow label="Run"><span className="font-mono text-xs">{result.run_id ?? '-'}</span></PlanRow>
              <PlanRow label="Dispatched now"><span className="font-semibold">{result.dispatched}</span> of {result.total}</PlanRow>
              {result.wave_total > 1 && <PlanRow label="Waves">{result.wave_total} (later waves held)</PlanRow>}
              {result.skipped.length > 0 && <PlanRow label="Skipped">{result.skipped.length}</PlanRow>}
            </div>
          )}
          <p className="text-[11px] text-slate-400">Track progress under Jobs{isFanout ? ' / Runs' : ''}.</p>
          <div className="flex items-center gap-3">
            <button onClick={onClose} className="bg-slate-800 hover:bg-slate-700 text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors">Done</button>
            <button
              onClick={() => { setResult(null); setPreview(null); setCommand(''); setPhase('input'); }}
              className="text-sm font-medium text-slate-500 hover:text-slate-800 px-2 py-2"
            >Run another</button>
          </div>
        </div>
      )}
    </Modal>
  );
}
