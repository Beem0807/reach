import type { WaveStrategy, WaveMode, WaveFailure, WaveRW, FleetWavePolicy } from '../types';

const SELECT = 'w-full text-xs border border-slate-300 rounded-md px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500';
const RW: WaveRW[] = ['read', 'write'];

// Staged-rollout policy for one scope, as an aligned grid: rows are the two knobs
// (Advance = mode, On failure), columns are Read and Write. "Default" = inherit the
// platform default (read: auto/continue, write: manual/stop), so its On-failure cell is
// blank. Wave size is the fan-out cap, so there's no size control here.
export function WavePolicyRW({ value, onChange }: {
  value: FleetWavePolicy; onChange: (v: FleetWavePolicy) => void;
}) {
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

      {/* Advance (mode) row */}
      <span className="text-xs text-slate-500 whitespace-nowrap">Advance</span>
      {RW.map(rw => (
        <select key={rw} className={SELECT} value={value[rw] ? value[rw]!.mode : 'off'}
                onChange={e => setMode(rw, e.target.value)}>
          <option value="off">Default</option>
          <option value="auto">Auto advance</option>
          <option value="manual">Manual advance</option>
        </select>
      ))}

      {/* On-failure row */}
      <span className="text-xs text-slate-500 whitespace-nowrap">On failure</span>
      {RW.map(rw => value[rw] ? (
        <select key={rw} className={SELECT} value={value[rw]!.on_failure}
                onChange={e => set(rw, { ...value[rw]!, on_failure: e.target.value as WaveFailure })}>
          <option value="stop">Stop</option>
          <option value="continue">Continue</option>
        </select>
      ) : (
        <span key={rw} className="text-xs text-slate-300 pl-2 select-none">-</span>
      ))}
    </div>
  );
}
