import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Badge } from '../components/Badge';

describe('Badge', () => {
  it('renders the value as text', () => {
    render(<Badge value="ACTIVE" />);
    expect(screen.getByText('ACTIVE')).toBeInTheDocument();
  });

  it('applies emerald classes for ACTIVE status', () => {
    const { container } = render(<Badge value="ACTIVE" />);
    expect(container.firstChild).toHaveClass('text-emerald-700');
  });

  it('applies red classes for REVOKED status', () => {
    const { container } = render(<Badge value="REVOKED" />);
    expect(container.firstChild).toHaveClass('text-red-600');
  });

  it('applies amber classes for INACTIVE status', () => {
    const { container } = render(<Badge value="INACTIVE" />);
    expect(container.firstChild).toHaveClass('text-yellow-700');
  });

  it('applies amber classes for PENDING job status', () => {
    const { container } = render(<Badge value="PENDING" />);
    expect(container.firstChild).toHaveClass('text-amber-700');
  });

  it('applies emerald classes for SUCCEEDED job status', () => {
    const { container } = render(<Badge value="SUCCEEDED" />);
    expect(container.firstChild).toHaveClass('text-emerald-700');
  });

  it('renders unknown values with fallback gray classes', () => {
    const { container } = render(<Badge value="UNKNOWN_VALUE" />);
    expect(container.firstChild).toHaveClass('text-gray-600');
    expect(screen.getByText('UNKNOWN_VALUE')).toBeInTheDocument();
  });

  it('includes a coloured dot element', () => {
    const { container } = render(<Badge value="ACTIVE" />);
    const dot = container.querySelector('span > span');
    expect(dot).toBeInTheDocument();
    expect(dot).toHaveClass('rounded-full');
  });
});
