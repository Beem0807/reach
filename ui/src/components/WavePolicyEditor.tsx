import type { WaveStrategy, WaveMode, WaveFailure, WaveRW, FleetWavePolicy } from '../types';

const SELECT = 'w-full text-xs border border-slate-300 rounded-md px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500';
const RW: WaveRW[] = ['read', 'write'];

// The platform default wave policy (backend shared/waves.py DEFAULT_WAVE_POLICY): a read
// rollout advances automatically and keeps going on failure; a write rollout pauses after
// every wave and stops on the first failure. This is what "Default" resolves to unless a
// caller passes a more specific `inherited` (e.g. a tenant's own fleet-scope override).
const PLATFORM_DEFAULT: Record<WaveRW, { mode: WaveMode; on_failure: WaveFailure }> = {
  read:  { mode: 'auto',   on_failure: 'continue' },
  write: { mode: 'manual', on_failure: 'stop' },
};

const MODE_LABEL: Record<WaveMode, string> = { auto: 'auto advance', manual: 'manual advance' };

// Staged-rollout policy for one scope, as an aligned grid: rows are the two knobs
// (Advance = mode, On failure), columns are Read and Write. "Default" inherits `inherited`
// (defaults to the platform default), and the inherited value is shown inline so a reader
// knows what "Default" actually does. Wave size is the fan-out cap, so there's no size
// control here.
export function WavePolicyRW({ value, onChange, inherited }: {
  value: FleetWavePolicy;
  onChange: (v: FleetWavePolicy) => void;
  // The effective default each scope falls back to (mode + on_failure). Omit to show the
  // platform default; pass a tenant's fleet-scope policy to reflect a tenant override.
  inherited?: Partial<Record<WaveRW, { mode: WaveMode; on_failure: WaveFailure }>>;
}) {
  const inh = (rw: WaveRW) => inherited?.[rw] ?? PLATFORM_DEFAULT[rw];
  const set = (rw: WaveRW, strat?: WaveStrategy) => {
    const next = { ...value };
    if (strat) next[rw] = strat; else delete next[rw];
    onChange(next);
  };
  const setMode = (rw: WaveRW, m: string) => {
    if (m === 'off') set(rw, undefined);
    // Preserve any stored concurrency (set via API) even though it's not edited here.
    else set(rw, { ...value[rw], mode: m as WaveMode, on_failure: value[rw]?.on_failure ?? 'stop' });
  };

  return (
    <div className="grid grid-cols-[auto_1fr_1fr] items-center gap-x-3 gap-y-2">
      {/* header row */}
      <span />
      <span className="text-[11px] font-semibold text-slate-600 px-1">Read</span>
      <span className="text-[11px] font-semibold text-red-600 px-1">Write</span>

      {/* Advance (mode) row - the Default option names what it inherits */}
      <span className="text-xs text-slate-500 whitespace-nowrap">Advance</span>
      {RW.map(rw => (
        <select key={rw} className={SELECT} value={value[rw] ? value[rw]!.mode : 'off'}
                onChange={e => setMode(rw, e.target.value)}>
          <option value="off">Default ({MODE_LABEL[inh(rw).mode]})</option>
          <option value="auto">Auto advance</option>
          <option value="manual">Manual advance</option>
        </select>
      ))}

      {/* On-failure row - when inheriting, show the inherited value (dimmed) instead of a blank */}
      <span className="text-xs text-slate-500 whitespace-nowrap">On failure</span>
      {RW.map(rw => value[rw] ? (
        <select key={rw} className={SELECT} value={value[rw]!.on_failure}
                onChange={e => set(rw, { ...value[rw]!, on_failure: e.target.value as WaveFailure })}>
          <option value="stop">Stop</option>
          <option value="continue">Continue</option>
        </select>
      ) : (
        <span key={rw} className="text-xs text-slate-400 pl-2 capitalize select-none"
              title="Inherited from the default">
          {inh(rw).on_failure} <span className="text-slate-300 normal-case">(default)</span>
        </span>
      ))}
    </div>
  );
}
