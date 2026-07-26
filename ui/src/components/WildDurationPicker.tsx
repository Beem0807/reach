import { useEffect, useState } from 'react';

// Presets offered when arming a temporary wild window. `value` is the duration string the
// API expects (`<n>h`/`<n>d`/`<n>w`), plus the two specials `permanent` and `custom`.
export const WILD_DURATIONS: { value: string; label: string }[] = [
  { value: 'permanent', label: 'Permanent' },
  { value: '1h', label: '1 hour' },
  { value: '2h', label: '2 hours' },
  { value: '4h', label: '4 hours' },
  { value: '1d', label: '1 day' },
  { value: '7d', label: '1 week' },
  { value: 'custom', label: 'Custom' },
];

// The amber "wild is unrestricted - set a duration to auto-revert" block shown when wild is
// the selected mode. Owns the preset/custom state and reports the effective duration
// ('permanent' | '1h' | a custom value like '3d'), whether the custom entry is invalid, and
// whether the choice arms a temporary window. Shared by the agent + fleet set-mode UIs.
export function WildDurationPicker({ revertTo, onChange }: {
  revertTo: string;
  onChange: (effectiveDuration: string, invalid: boolean, isTemporary: boolean) => void;
}) {
  const [durPreset, setDurPreset] = useState('permanent');
  const [customDur, setCustomDur] = useState('');

  const effective = durPreset === 'custom' ? customDur.trim().toLowerCase() : durPreset;
  const invalid = durPreset === 'custom' && !/^\d+[hdw]$/.test(customDur.trim().toLowerCase());
  const isTemporary = !!effective && effective !== 'permanent';

  useEffect(() => { onChange(effective, invalid, isTemporary); }, [effective, invalid, isTemporary]);

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-3 space-y-2">
      <p className="text-xs font-semibold text-amber-800">Wild is unrestricted - set a duration to auto-revert</p>
      <div className="flex flex-wrap gap-1.5">
        {WILD_DURATIONS.map(d => (
          <button key={d.value} type="button" onClick={() => setDurPreset(d.value)}
            className={`text-xs font-medium px-2.5 py-1 rounded-md border transition-colors ${durPreset === d.value ? 'bg-amber-600 text-white border-amber-600' : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'}`}>
            {d.label}
          </button>
        ))}
      </div>
      {durPreset === 'custom' && (
        <input value={customDur} onChange={e => setCustomDur(e.target.value)} placeholder="e.g. 3d, 12h, 2w"
          className={`w-full sm:w-40 border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 ${invalid ? 'border-red-300 focus:ring-red-400' : 'border-gray-300 focus:ring-amber-400'}`} />
      )}
      <p className="text-[11px] text-amber-700">
        {isTemporary
          ? <>Reverts to <span className="font-semibold">{revertTo}</span> when the window ends.</>
          : 'Stays wild until changed (no auto-revert).'}
        {invalid && <span className="text-red-600"> Use a number + h/d/w (e.g. 12h, 3d, 2w).</span>}
      </p>
    </div>
  );
}
