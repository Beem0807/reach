import { useState } from 'react';
import { tenantChangePassword } from '../api';
import type { TenantConfig } from '../types';
import { Spinner } from '../components/Spinner';

interface Props {
  config: TenantConfig;
  onComplete: () => void;
}

export function PasswordResetPage({ config, onComplete }: Props) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');

  function pwStrength(pw: string): { bars: number; label: string; color: string } | null {
    if (!pw) return null;
    const variety = [/[a-z]/, /[A-Z]/, /\d/, /[^a-zA-Z0-9]/].filter(r => r.test(pw)).length;
    if (pw.length < 8)                              return { bars: 1, label: 'Weak',   color: 'bg-red-500' };
    if (pw.length < 12 && variety < 3)              return { bars: 2, label: 'Fair',   color: 'bg-amber-500' };
    if (pw.length >= 12 && variety >= 3)            return { bars: 4, label: 'Strong', color: 'bg-emerald-500' };
    return                                                 { bars: 3, label: 'Good',   color: 'bg-blue-500' };
  }
  const strength = pwStrength(next);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== confirm) { setError('Passwords do not match.'); return; }
    if (next.length < 8) { setError('Password must be at least 8 characters.'); return; }
    setLoading(true); setError('');
    try {
      await tenantChangePassword(config.apiUrl, config.tenantToken, current, next);
      onComplete();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-8">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500 flex items-center justify-center shadow-lg">
              <span className="text-white font-bold text-lg">R</span>
            </div>
            <div>
              <p className="text-white font-semibold text-xl leading-none">reach</p>
              <p className="text-slate-400 text-xs mt-0.5">Set new password</p>
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-xl">
          <div className="bg-amber-900/30 border border-amber-700/40 rounded-lg px-4 py-3 mb-6">
            <p className="text-amber-300 text-sm font-medium">Password reset required</p>
            <p className="text-amber-400/80 text-xs mt-0.5">You must set a new password before continuing.</p>
          </div>

          {error && (
            <div className="bg-red-900/40 border border-red-700/50 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">
              {error}
            </div>
          )}

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Temporary password</label>
              <input
                type="password"
                value={current}
                onChange={e => setCurrent(e.target.value)}
                placeholder="••••••••"
                autoFocus
                className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3.5 py-2.5 text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">New password</label>
              <input
                type="password"
                value={next}
                onChange={e => setNext(e.target.value)}
                placeholder="At least 8 characters"
                className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3.5 py-2.5 text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              {strength && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex gap-1 flex-1">
                    {[1,2,3,4].map(i => (
                      <div key={i} className={`h-1 flex-1 rounded-full transition-colors ${i <= strength.bars ? strength.color : 'bg-slate-700'}`} />
                    ))}
                  </div>
                  <span className={`text-xs font-medium ${strength.bars === 1 ? 'text-red-400' : strength.bars === 2 ? 'text-amber-400' : strength.bars === 3 ? 'text-blue-400' : 'text-emerald-400'}`}>
                    {strength.label}
                  </span>
                </div>
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Confirm new password</label>
              <input
                type="password"
                value={confirm}
                onChange={e => setConfirm(e.target.value)}
                placeholder="••••••••"
                className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3.5 py-2.5 text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white font-semibold text-sm rounded-lg py-2.5 transition-colors mt-2"
            >
              {loading && <Spinner className="h-4 w-4" />}
              {loading ? 'Saving…' : 'Set new password'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
