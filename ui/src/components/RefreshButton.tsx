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
    ? 'bg-gray-100 hover:bg-gray-200 text-gray-600'
    : 'bg-white/10 hover:bg-white/20 text-white';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      title={title}
      aria-label="Refresh"
      className={`inline-flex items-center justify-center w-9 h-9 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0 ${tone}`}
    >
      {/* Clean single-loop circular-arrow (rotate) icon */}
      <svg className={`w-[18px] h-[18px] ${loading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 12a7.5 7.5 0 11-2.197-5.303" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 3.75v3h-3" />
      </svg>
    </button>
  );
}
