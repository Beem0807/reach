import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Spinner } from '../components/Spinner';

describe('Spinner', () => {
  it('renders an svg element', () => {
    const { container } = render(<Spinner />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('has animate-spin class by default', () => {
    const { container } = render(<Spinner />);
    expect(container.querySelector('svg')).toHaveClass('animate-spin');
  });

  it('has default size classes h-5 w-5', () => {
    const { container } = render(<Spinner />);
    expect(container.querySelector('svg')).toHaveClass('h-5', 'w-5');
  });

  it('applies a custom className', () => {
    const { container } = render(<Spinner className="h-4 w-4 text-red-600" />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveClass('h-4', 'w-4', 'text-red-600');
  });

  it('still has animate-spin when custom className is provided', () => {
    const { container } = render(<Spinner className="text-white" />);
    expect(container.querySelector('svg')).toHaveClass('animate-spin');
  });
});
