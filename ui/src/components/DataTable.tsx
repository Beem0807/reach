import { useState, useRef, useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';

export interface ColDef<T> {
  label: string;
  sortValue?: (row: T) => string | number;
  defaultHidden?: boolean;
  required?: boolean;
}

interface Props<T> {
  columns: ColDef<T>[];
  rows: T[];
  renderRow: (row: T) => React.ReactNode;
  fallback?: React.ReactNode;
  tableId?: string;
}

export function DataTable<T>({ columns, rows, renderRow, fallback, tableId }: Props<T>) {
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [minWidths, setMinWidths] = useState<number[]>(() => columns.map(() => 0));
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dropdownPos, setDropdownPos] = useState<{ top: number; right: number } | null>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const dragging = useRef<{ col: number; startX: number; startW: number } | null>(null);
  const tableUid = useRef(`dt-${Math.random().toString(36).slice(2, 8)}`).current;

  const [hiddenCols, setHiddenCols] = useState<Set<number>>(() => {
    const defaults = new Set<number>(
      columns.map((c, i) => (c.defaultHidden ? i : -1)).filter(i => i >= 0)
    );
    if (tableId) {
      try {
        const saved = localStorage.getItem(`dt_hidden_${tableId}`);
        if (saved) return new Set<number>(JSON.parse(saved));
      } catch {}
    }
    return defaults;
  });

  // Close picker on outside click
  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const inButton = pickerRef.current?.contains(target);
      const inDropdown = dropdownRef.current?.contains(target);
      if (!inButton && !inDropdown) setPickerOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [pickerOpen]);

  const toggleCol = (i: number) => {
    if (columns[i]?.required) return;
    setHiddenCols(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      if (tableId) {
        try { localStorage.setItem(`dt_hidden_${tableId}`, JSON.stringify([...next])); } catch {}
      }
      return next;
    });
  };

  const handleSort = (i: number) => {
    if (!columns[i].sortValue) return;
    if (sortCol === i) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(i);
      setSortDir('asc');
    }
  };

  const sorted = sortCol !== null && columns[sortCol]?.sortValue
    ? [...rows].sort((a, b) => {
        const fn = columns[sortCol].sortValue!;
        const av = fn(a), bv = fn(b);
        if (av < bv) return sortDir === 'asc' ? -1 : 1;
        if (av > bv) return sortDir === 'asc' ? 1 : -1;
        return 0;
      })
    : rows;

  const onResizeStart = useCallback((e: React.MouseEvent, col: number) => {
    e.preventDefault();
    const th = (e.currentTarget as HTMLElement).closest('th') as HTMLElement;
    const startW = th.offsetWidth;
    dragging.current = { col, startX: e.clientX, startW };

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const { col: c, startX, startW: sw } = dragging.current;
      const newW = Math.max(60, sw + ev.clientX - startX);
      setMinWidths(ws => { const n = [...ws]; n[c] = newW; return n; });
    };
    const onUp = () => {
      dragging.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, []);

  // Pickable columns = columns with a non-empty label
  const pickable = columns.map((c, i) => ({ ...c, i })).filter(c => c.label);
  const visibleCount = columns.length - hiddenCols.size;

  return (
    <div>
      {/* Column picker toolbar - outside overflow-x-auto so the dropdown isn't clipped */}
      <div className="flex justify-end items-center px-3 py-2 border-b border-gray-100 bg-gray-50/60">
        <div className="relative" ref={pickerRef}>
          <button
            ref={btnRef}
            onClick={() => {
              if (!pickerOpen && btnRef.current) {
                const r = btnRef.current.getBoundingClientRect();
                setDropdownPos({ top: r.bottom + 6, right: window.innerWidth - r.right });
              }
              setPickerOpen(v => !v);
            }}
            className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-colors ${
              pickerOpen
                ? 'bg-gray-800 text-white border-gray-800'
                : 'text-gray-500 border-gray-200 bg-white hover:bg-gray-50 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 4.5v15m6-15v15M3.75 9h16.5M3.75 15h16.5" />
            </svg>
            Columns
            {hiddenCols.size > 0 && (
              <span className={`text-[10px] font-bold px-1 rounded ${pickerOpen ? 'bg-white/20' : 'bg-gray-200 text-gray-600'}`}>
                {visibleCount}/{columns.length}
              </span>
            )}
          </button>

          {pickerOpen && dropdownPos && createPortal(
            <div
              ref={dropdownRef}
              style={{ position: 'fixed', top: dropdownPos.top, right: dropdownPos.right, zIndex: 9999 }}
              className="bg-white border border-gray-200 rounded-xl shadow-xl py-2 min-w-[180px]"
            >
              <div className="px-3 pb-2 mb-1 border-b border-gray-100 flex items-center justify-between">
                <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Columns</span>
                {hiddenCols.size > 0 && (
                  <button
                    onClick={() => {
                      setHiddenCols(new Set());
                      if (tableId) { try { localStorage.removeItem(`dt_hidden_${tableId}`); } catch {} }
                    }}
                    className="text-[10px] text-indigo-600 hover:text-indigo-800"
                  >
                    Show all
                  </button>
                )}
              </div>
              {pickable.map(col => (
                <label
                  key={col.i}
                  className={`flex items-center gap-2.5 px-3 py-1.5 ${col.required ? 'opacity-50 cursor-not-allowed' : 'hover:bg-gray-50 cursor-pointer'}`}
                >
                  <input
                    type="checkbox"
                    checked={!hiddenCols.has(col.i)}
                    onChange={() => toggleCol(col.i)}
                    disabled={col.required}
                    className="w-3.5 h-3.5 rounded disabled:cursor-not-allowed"
                  />
                  <span className="text-sm text-gray-700">{col.label}</span>
                  {col.required && <span className="ml-auto text-[10px] text-gray-400">required</span>}
                </label>
              ))}
            </div>,
            document.body
          )}
        </div>
      </div>

      {hiddenCols.size > 0 && (
        <style>{[...hiddenCols].map(i =>
          `.${tableUid} thead th:nth-child(${i + 1}), .${tableUid} tbody td:nth-child(${i + 1}) { display: none; }`
        ).join(' ')}</style>
      )}
      <div className="overflow-x-auto">
      <table className={`w-full text-sm ${tableUid}`}>
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50/80">
            {columns.map((col, i) => {
              const sortable = !!col.sortValue;
              const active = sortCol === i;
              return (
                <th
                  key={i}
                  className="relative text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap select-none"
                  style={minWidths[i] ? { minWidth: minWidths[i] } : undefined}
                >
                  <span
                    className={sortable ? 'cursor-pointer hover:text-gray-800 inline-flex items-center gap-1' : ''}
                    onClick={sortable ? () => handleSort(i) : undefined}
                  >
                    {col.label}
                    {sortable && (
                      <span className="text-[10px] text-gray-300">
                        {active ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
                      </span>
                    )}
                  </span>
                  <div
                    onMouseDown={e => onResizeStart(e, i)}
                    className="absolute right-0 top-0 h-full w-1 cursor-col-resize hover:bg-indigo-300 transition-colors"
                  />
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.length === 0 && fallback !== undefined ? (
            <tr>
              <td colSpan={columns.length} className="text-center py-14 text-gray-400 text-sm">
                {fallback}
              </td>
            </tr>
          ) : (
            sorted.map(row => renderRow(row))
          )}
        </tbody>
      </table>
      </div>
    </div>
  );
}
