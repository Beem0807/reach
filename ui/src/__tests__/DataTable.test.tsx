import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DataTable } from '../components/DataTable';

type Row = { id: string; name: string; status: string; extra: string };

const ROWS: Row[] = [
  { id: '1', name: 'charlie', status: 'active',   extra: 'zzz' },
  { id: '2', name: 'alice',   status: 'inactive', extra: 'aaa' },
  { id: '3', name: 'bob',     status: 'pending',  extra: 'mmm' },
];

function renderTable(rows: Row[] = ROWS) {
  return render(
    <DataTable<Row>
      tableId="test-dt"
      columns={[
        { label: 'Name',   sortValue: r => r.name,   required: true },
        { label: 'Status', sortValue: r => r.status },
        { label: 'Extra',  sortValue: r => r.extra,  defaultHidden: true },
      ]}
      rows={rows}
      renderRow={r => (
        <tr key={r.id}>
          <td>{r.name}</td>
          <td>{r.status}</td>
          <td>{r.extra}</td>
        </tr>
      )}
    />,
  );
}

function openPicker() {
  fireEvent.click(screen.getByRole('button', { name: /columns/i }));
}

// Stable index of checkboxes: 0=Name (required), 1=Status, 2=Extra (defaultHidden)
function checkboxes() {
  return screen.getAllByRole('checkbox');
}

beforeEach(() => {
  vi.restoreAllMocks();
  try { localStorage.removeItem('dt_hidden_test-dt'); } catch {}
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe('DataTable rendering', () => {
  it('renders all column header labels', () => {
    renderTable();
    // All th text is in the DOM (CSS hides Extra via style tag but JSDOM doesn't apply CSS)
    expect(document.querySelector('thead')!.textContent).toContain('Name');
    expect(document.querySelector('thead')!.textContent).toContain('Status');
    expect(document.querySelector('thead')!.textContent).toContain('Extra');
  });

  it('renders row data in tbody', () => {
    renderTable();
    expect(screen.getByText('charlie')).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
  });

  it('renders fallback row when rows is empty', () => {
    render(
      <DataTable<string>
        tableId="empty-dt"
        columns={[{ label: 'Name', sortValue: r => r, required: true }]}
        rows={[]}
        renderRow={r => <tr key={r}><td>{r}</td></tr>}
        fallback="No items yet"
      />,
    );
    expect(screen.getByText('No items yet')).toBeInTheDocument();
  });

  it('renders Columns toolbar button', () => {
    renderTable();
    expect(screen.getByRole('button', { name: /columns/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Hidden columns - style tag injection
// ---------------------------------------------------------------------------

describe('DataTable hidden column style injection', () => {
  it('injects a style tag when a column is defaultHidden', () => {
    renderTable(); // Extra (index 2) is defaultHidden → nth-child(3)
    const styleEls = Array.from(document.querySelectorAll('style'));
    const injected = styleEls.some(s => s.textContent?.includes('nth-child(3)'));
    expect(injected).toBe(true);
  });

  it('style tag targets both thead th and tbody td', () => {
    renderTable();
    const styleEls = Array.from(document.querySelectorAll('style'));
    const text = styleEls.find(s => s.textContent?.includes('nth-child(3)'))?.textContent ?? '';
    expect(text).toContain('thead th');
    expect(text).toContain('tbody td');
  });

  it('no style tag when all columns visible', () => {
    render(
      <DataTable<string>
        tableId="nodft"
        columns={[{ label: 'X', sortValue: r => r, required: true }]}
        rows={['a']}
        renderRow={r => <tr key={r}><td>{r}</td></tr>}
      />,
    );
    const styleEls = Array.from(document.querySelectorAll('style'));
    const hasHide = styleEls.some(s => s.textContent?.includes('nth-child'));
    expect(hasHide).toBe(false);
  });

  it('shows column count badge (visible/total) when any column hidden', () => {
    renderTable(); // 2 visible, 3 total
    expect(screen.getByText('2/3')).toBeInTheDocument();
  });

  it('no badge when all columns visible', () => {
    render(
      <DataTable<string>
        tableId="nohide"
        columns={[{ label: 'A', sortValue: r => r, required: true }, { label: 'B', sortValue: r => r }]}
        rows={['x']}
        renderRow={r => <tr key={r}><td>{r}</td><td>{r}</td></tr>}
      />,
    );
    // No badge text like "1/2"
    expect(screen.queryByText(/\/2/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Column picker - open / toggle / restore
// ---------------------------------------------------------------------------

describe('DataTable column picker', () => {
  it('opens picker with checkboxes for each column', () => {
    renderTable();
    openPicker();
    expect(checkboxes().length).toBe(3);
  });

  it('required column checkbox is disabled', () => {
    renderTable();
    openPicker();
    expect(checkboxes()[0]).toBeDisabled();
  });

  it('required column label shows "required" hint', () => {
    renderTable();
    openPicker();
    expect(screen.getByText('required')).toBeInTheDocument();
  });

  it('non-required visible column checkbox is checked', () => {
    renderTable();
    openPicker();
    expect(checkboxes()[1]).toBeChecked(); // Status - visible
  });

  it('defaultHidden column checkbox is unchecked', () => {
    renderTable();
    openPicker();
    expect(checkboxes()[2]).not.toBeChecked(); // Extra - defaultHidden
  });

  it('unchecking Status injects nth-child(2) style', () => {
    renderTable();
    openPicker();
    fireEvent.click(checkboxes()[1]); // hide Status (col index 1 → nth-child 2)
    const styleEls = Array.from(document.querySelectorAll('style'));
    const injected = styleEls.some(s => s.textContent?.includes('nth-child(2)'));
    expect(injected).toBe(true);
  });

  it('unchecking then rechecking Status removes nth-child(2) style', () => {
    renderTable();
    openPicker();
    fireEvent.click(checkboxes()[1]); // hide
    fireEvent.click(checkboxes()[1]); // show again
    const styleEls = Array.from(document.querySelectorAll('style'));
    const stillHidden = styleEls.some(s => s.textContent?.includes('nth-child(2)'));
    expect(stillHidden).toBe(false);
  });

  it('"Show all" button appears when columns are hidden', () => {
    renderTable();
    openPicker();
    expect(screen.getByText('Show all')).toBeInTheDocument();
  });

  it('"Show all" resets all hidden columns - style tag removed', () => {
    renderTable();
    openPicker();
    fireEvent.click(screen.getByText('Show all'));
    const styleEls = Array.from(document.querySelectorAll('style'));
    const anyHide = styleEls.some(s => s.textContent?.includes('nth-child'));
    expect(anyHide).toBe(false);
  });

  it('"Show all" marks all checkboxes as checked', () => {
    renderTable();
    openPicker();
    fireEvent.click(screen.getByText('Show all'));
    checkboxes().forEach(cb => expect(cb).toBeChecked());
  });

  it('closing picker (click button again) hides checkboxes', () => {
    renderTable();
    openPicker();
    expect(checkboxes().length).toBeGreaterThan(0);
    openPicker(); // toggle off
    expect(screen.queryAllByRole('checkbox').length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

describe('DataTable sorting', () => {
  function nameColumnSpan() {
    const ths = document.querySelectorAll('thead th');
    return ths[0].querySelector('span')!;
  }

  function statusColumnSpan() {
    const ths = document.querySelectorAll('thead th');
    return ths[1].querySelector('span')!;
  }

  // Use DOM table APIs to avoid index arithmetic errors with hidden cols
  function cellAtRow(rowIndex: number, colIndex: number) {
    const tbody = document.querySelector('tbody')!;
    return (tbody as HTMLTableSectionElement).rows[rowIndex].cells[colIndex];
  }

  it('initially renders rows in original order', () => {
    renderTable();
    expect(cellAtRow(0, 0).textContent).toBe('charlie');
    expect(cellAtRow(1, 0).textContent).toBe('alice');
    expect(cellAtRow(2, 0).textContent).toBe('bob');
  });

  it('sorts ascending on first header click', () => {
    renderTable();
    fireEvent.click(nameColumnSpan());
    expect(cellAtRow(0, 0).textContent).toBe('alice');
    expect(cellAtRow(1, 0).textContent).toBe('bob');
    expect(cellAtRow(2, 0).textContent).toBe('charlie');
  });

  it('sorts descending on second header click', () => {
    renderTable();
    fireEvent.click(nameColumnSpan());
    fireEvent.click(nameColumnSpan());
    expect(cellAtRow(0, 0).textContent).toBe('charlie');
    expect(cellAtRow(1, 0).textContent).toBe('bob');
    expect(cellAtRow(2, 0).textContent).toBe('alice');
  });

  it('shows ascending indicator ▲ after first click', () => {
    renderTable();
    fireEvent.click(nameColumnSpan());
    expect(document.querySelector('thead')!.textContent).toContain('▲');
  });

  it('shows descending indicator ▼ after second click', () => {
    renderTable();
    fireEvent.click(nameColumnSpan());
    fireEvent.click(nameColumnSpan());
    expect(document.querySelector('thead')!.textContent).toContain('▼');
  });

  it('shows ⇅ on unsorted sortable columns', () => {
    renderTable();
    expect(document.querySelector('thead')!.textContent).toContain('⇅');
  });

  it('switches sort column when different header clicked', () => {
    renderTable();
    fireEvent.click(nameColumnSpan());   // sort by Name asc
    fireEvent.click(statusColumnSpan()); // switch to Status asc
    // Status ascending: active, inactive, pending
    expect(cellAtRow(0, 1).textContent).toBe('active');
    expect(cellAtRow(1, 1).textContent).toBe('inactive');
    expect(cellAtRow(2, 1).textContent).toBe('pending');
  });

  it('returns to original order when same column clicked back to original via three clicks... just two clicks changes direction', () => {
    renderTable();
    fireEvent.click(nameColumnSpan());
    fireEvent.click(nameColumnSpan());
    // After two clicks: descending - charlie, bob, alice
    expect(cellAtRow(0, 0).textContent).toBe('charlie');
  });
});
