import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Modal } from '../components/Modal';

describe('Modal', () => {
  it('renders the title', () => {
    render(<Modal title="Test modal" onClose={vi.fn()}>content</Modal>);
    expect(screen.getByText('Test modal')).toBeInTheDocument();
  });

  it('renders children', () => {
    render(<Modal title="T" onClose={vi.fn()}><p>Hello world</p></Modal>);
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  it('calls onClose when × button is clicked', () => {
    const onClose = vi.fn();
    render(<Modal title="T" onClose={onClose}>content</Modal>);
    fireEvent.click(screen.getByRole('button'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('calls onClose when Escape key is pressed', () => {
    const onClose = vi.fn();
    render(<Modal title="T" onClose={onClose}>content</Modal>);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('does not call onClose for other keys', () => {
    const onClose = vi.fn();
    render(<Modal title="T" onClose={onClose}>content</Modal>);
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('applies max-w-2xl when wide prop is true', () => {
    const { container } = render(<Modal title="T" onClose={vi.fn()} wide>content</Modal>);
    expect(container.querySelector('.max-w-2xl')).toBeInTheDocument();
  });

  it('applies max-w-lg when wide prop is omitted', () => {
    const { container } = render(<Modal title="T" onClose={vi.fn()}>content</Modal>);
    expect(container.querySelector('.max-w-lg')).toBeInTheDocument();
  });
});
