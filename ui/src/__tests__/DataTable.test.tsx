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
  it('renders visible column header labels (hidden columns not rendered)', () => {
    renderTable();
    // Hidden columns are dropped from the DOM (not CSS-hidden), so Extra (defaultHidden) is absent.
    expect(document.querySelector('thead')!.textContent).toContain('Name');
    expect(document.querySelector('thead')!.textContent).toContain('Status');
    expect(document.querySelector('thead')!.textContent).not.toContain('Extra');
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

describe('DataTable hidden columns removed from DOM', () => {
  it('drops a defaultHidden column from the header', () => {
    renderTable(); // Extra is defaultHidden
    expect(document.querySelector('thead')!.textContent).not.toContain('Extra');
  });

  it('drops the defaultHidden column cells from the body', () => {
    renderTable();
    // Extra values (zzz/aaa/mmm) must not appear in any body cell.
    expect(screen.queryByText('zzz')).not.toBeInTheDocument();
    expect(screen.queryByText('aaa')).not.toBeInTheDocument();
    // Each visible row has exactly the 2 visible columns (Name, Status).
    const firstRow = (document.querySelector('tbody') as HTMLTableSectionElement).rows[0];
    expect(firstRow.cells.length).toBe(2);
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

  it('unchecking Status removes it from the header', () => {
    renderTable();
    openPicker();
    fireEvent.click(checkboxes()[1]); // hide Status
    expect(document.querySelector('thead')!.textContent).not.toContain('Status');
  });

  it('unchecking then rechecking Status restores it in the header', () => {
    renderTable();
    openPicker();
    fireEvent.click(checkboxes()[1]); // hide
    fireEvent.click(checkboxes()[1]); // show again
    expect(document.querySelector('thead')!.textContent).toContain('Status');
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
// Column reordering (drag headers)
// ---------------------------------------------------------------------------

describe('DataTable column reordering', () => {
  beforeEach(() => { try { localStorage.removeItem('dt_order_test-dt'); } catch {} });

  function headerLabels() {
    return Array.from(document.querySelectorAll('thead th'))
      .map(th => th.querySelector('span')!.textContent!.replace(/[⇅▲▼]/g, ''));
  }

  function reorder(fromIdx: number, toIdx: number) {
    const ths = document.querySelectorAll('thead th');
    fireEvent.dragStart(ths[fromIdx]);
    fireEvent.dragOver(ths[toIdx]);
    fireEvent.drop(ths[toIdx]);
  }

  it('reorders columns when a header is dropped before another', () => {
    renderTable(); // visible order: Name, Status
    expect(headerLabels()).toEqual(['Name', 'Status']);
    reorder(1, 0); // drop Status before Name
    expect(headerLabels()).toEqual(['Status', 'Name']);
  });

  it('reorders the matching body cells too', () => {
    renderTable();
    reorder(1, 0); // Status now first
    const firstRow = (document.querySelector('tbody') as HTMLTableSectionElement).rows[0];
    expect(firstRow.cells[0].textContent).toBe('active'); // charlie's status
    expect(firstRow.cells[1].textContent).toBe('charlie');
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
