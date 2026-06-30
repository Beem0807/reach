// A small refresh control for page headers. Spins its icon while a load is in
// flight and is disabled so you can't stack requests. `variant` picks styling for
// a colored header ("onColor", default) vs a light background ("onLight").
export function RefreshButton({ onClick, loading = false, title = 'Refresh', variant = 'onColor' }: {
  onClick: () => void;
  loading?: boolean;
  title?: string;
  variant?: 'onColor' | 'onLight';
}) {
  const tone = variant === 'onLight'
    ? 'bg-white hover:bg-gray-50 border-gray-300 text-gray-600'
    : 'bg-white/15 hover:bg-white/25 border-white/20 text-white';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      title={title}
      aria-label="Refresh"
      className={`inline-flex items-center justify-center w-9 h-9 rounded-lg border transition-colors disabled:opacity-60 disabled:cursor-not-allowed shrink-0 ${tone}`}
    >
      <svg className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992V4.356M2.985 19.644v-4.992h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
      </svg>
    </button>
  );
}
