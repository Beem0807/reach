import { useState, type FormEvent } from 'react';
import { adminLogin } from '../api';
import type { Config } from '../types';
import { Spinner } from '../components/Spinner';

interface Props {
  onLogin: (c: Config) => void;
  onSwitchToTenant?: () => void;
}

export function LoginPage({ onLogin, onSwitchToTenant }: Props) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const url = window.location.origin.replace(/\/$/, '');
      const token = await adminLogin(url, password);
      onLogin({ apiUrl: url, adminToken: token });
    } catch {
      setError('Invalid password.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
      {/* Subtle grid background */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff08_1px,transparent_1px),linear-gradient(to_bottom,#ffffff08_1px,transparent_1px)] bg-[size:48px_48px]" />

      <div className="relative w-full max-w-sm">
        {/* Logo mark */}
        <div className="flex justify-center mb-8">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500 flex items-center justify-center shadow-lg shadow-indigo-500/30">
              <span className="text-white text-lg font-bold tracking-tight">R</span>
            </div>
            <div>
              <p className="text-white font-bold text-xl leading-none">reach</p>
              <p className="text-slate-400 text-xs mt-0.5">Console</p>
            </div>
          </div>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl shadow-black/40 p-8">
          <h2 className="text-lg font-semibold text-gray-900 mb-1">Sign in</h2>
          <p className="text-sm text-gray-500 mb-6">Enter your credentials to continue</p>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Password</label>
              <input
                type="password"
                required
                autoComplete="current-password"
                placeholder="Enter password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-shadow"
              />
            </div>

            {error && (
              <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
                <svg className="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors shadow-sm mt-1"
            >
              {loading && <Spinner className="h-4 w-4" />}
              {loading ? 'Signing in…' : 'Sign in'}
            </button>

          </form>
        </div>

        {onSwitchToTenant && (
          <p className="text-center mt-5 text-slate-500 text-xs">
            Not a platform admin?{' '}
            <button onClick={onSwitchToTenant} className="text-slate-400 hover:text-white underline underline-offset-2">
              Tenant sign in
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
