import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { CopyButton, TokenBox } from '../components/CopyButton';

const writeText = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    writable: true,
    configurable: true,
  });
  writeText.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('CopyButton', () => {
  it('renders "Copy" label by default', () => {
    render(<CopyButton text="hello" />);
    expect(screen.getByRole('button')).toHaveTextContent('Copy');
  });

  it('calls clipboard.writeText with the provided text on click', async () => {
    render(<CopyButton text="my-secret" />);
    await act(async () => { fireEvent.click(screen.getByRole('button')); });
    expect(writeText).toHaveBeenCalledWith('my-secret');
  });

  it('shows "Copied" after clicking', async () => {
    render(<CopyButton text="x" />);
    await act(async () => { fireEvent.click(screen.getByRole('button')); });
    expect(screen.getByRole('button')).toHaveTextContent('Copied');
  });

  it('reverts to "Copy" after 1500ms', async () => {
    render(<CopyButton text="x" />);
    await act(async () => { fireEvent.click(screen.getByRole('button')); });
    expect(screen.getByRole('button')).toHaveTextContent('Copied');
    await act(async () => { vi.advanceTimersByTime(1500); });
    expect(screen.getByRole('button')).toHaveTextContent('Copy');
  });

  it('applies extra className to the button', () => {
    render(<CopyButton text="x" className="my-extra" />);
    expect(screen.getByRole('button')).toHaveClass('my-extra');
  });

  it('applies emerald classes when in copied state', async () => {
    render(<CopyButton text="x" />);
    await act(async () => { fireEvent.click(screen.getByRole('button')); });
    expect(screen.getByRole('button')).toHaveClass('text-emerald-700');
  });
});

describe('TokenBox', () => {
  it('renders the label', () => {
    render(<TokenBox label="API Token" value="tok_abc" />);
    expect(screen.getByText('API Token')).toBeInTheDocument();
  });

  it('renders the token value', () => {
    render(<TokenBox label="Token" value="tok_abc" />);
    expect(screen.getByText('tok_abc')).toBeInTheDocument();
  });

  it('includes a copy button', () => {
    render(<TokenBox label="Token" value="tok_abc" />);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('copies value when copy button is clicked', async () => {
    render(<TokenBox label="Token" value="tok_secret" />);
    await act(async () => { fireEvent.click(screen.getByRole('button')); });
    expect(writeText).toHaveBeenCalledWith('tok_secret');
  });
});
