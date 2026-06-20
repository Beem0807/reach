import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentInfo } from '../components/AgentInfo';

describe('AgentInfo', () => {
  it('shows agentId as primary text when hostname is absent', () => {
    render(<AgentInfo agentId="agent_abc" />);
    expect(screen.getByText('agent_abc')).toBeInTheDocument();
  });

  it('shows hostname as primary text when provided', () => {
    render(<AgentInfo agentId="agent_abc" hostname="prod-01.local" />);
    expect(screen.getByText('prod-01.local')).toBeInTheDocument();
  });

  it('shows agentId as secondary text when hostname is provided', () => {
    render(<AgentInfo agentId="agent_abc" hostname="prod-01.local" />);
    expect(screen.getByText('agent_abc')).toBeInTheDocument();
  });

  it('does not render secondary agentId line when hostname is absent', () => {
    const { container } = render(<AgentInfo agentId="agent_abc" />);
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(1);
  });

  it('renders two text elements when hostname is provided', () => {
    const { container } = render(<AgentInfo agentId="agent_abc" hostname="host" />);
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(2);
  });

  it('treats null hostname the same as absent', () => {
    const { container } = render(<AgentInfo agentId="agent_abc" hostname={null} />);
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(1);
    expect(screen.getByText('agent_abc')).toBeInTheDocument();
  });

  it('secondary agentId uses monospace font class', () => {
    render(<AgentInfo agentId="agent_abc" hostname="host" />);
    const secondary = screen.getAllByText('agent_abc')[0];
    expect(secondary.className).toContain('font-mono');
  });
});
