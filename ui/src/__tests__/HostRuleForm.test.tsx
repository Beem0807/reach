import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { HostRuleForm, EMPTY_HOST_RULE, parseHostRule, hostRuleToText, ruleHasMisplacedRest } from '../components/HostRuleForm';
import type { HostRule } from '../types';

describe('parseHostRule', () => {
  it('splits a command pattern into bin + args (first token is the bin)', () => {
    expect(parseHostRule('systemctl restart *')).toEqual({ bin: 'systemctl', args: ['restart', '*'] });
    expect(parseHostRule('  df   -h ')).toEqual({ bin: 'df', args: ['-h'] });
    expect(parseHostRule('uptime')).toEqual({ bin: 'uptime', args: [] });
    expect(parseHostRule('')).toEqual({ bin: '', args: [] });
    expect(parseHostRule('helm list ...')).toEqual({ bin: 'helm', args: ['list', '...'] });  // trailing variadic preserved
  });
  it('round-trips via hostRuleToText', () => {
    const r: HostRule = { bin: 'systemctl', args: ['restart', '*'] };
    expect(parseHostRule(hostRuleToText(r))).toEqual(r);
  });
});

describe('HostRuleForm', () => {
  it('typing the whole command parses into a structured {bin, args} rule (not one giant bin)', () => {
    const onChange = vi.fn();
    render(<HostRuleForm value={EMPTY_HOST_RULE} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Command pattern'), { target: { value: 'systemctl restart nginx' } });
    // The footgun is gone: bin is just "systemctl", not the whole string.
    expect(onChange).toHaveBeenLastCalledWith({ bin: 'systemctl', args: ['restart', 'nginx'] });
  });

  it('shows a live chip preview with * as "any"', () => {
    render(<HostRuleForm value={{ bin: 'systemctl', args: ['restart', '*'] }} onChange={() => {}} />);
    expect(screen.getByText('systemctl')).toBeInTheDocument();
    expect(screen.getByText('restart')).toBeInTheDocument();
    expect(screen.getByText('any')).toBeInTheDocument();
  });

  it('prefills the input from an existing rule', () => {
    render(<HostRuleForm value={{ bin: 'df', args: ['-h'] }} onChange={() => {}} />);
    expect(screen.getByLabelText('Command pattern')).toHaveValue('df -h');
  });

  it('renders a trailing "..." as "any args"', () => {
    render(<HostRuleForm value={{ bin: 'helm', args: ['list', '...'] }} onChange={() => {}} />);
    expect(screen.getByText('helm')).toBeInTheDocument();
    expect(screen.getByText('list')).toBeInTheDocument();
    expect(screen.getByText('any args')).toBeInTheDocument();
  });

  it('warns when "..." is not the last token', () => {
    render(<HostRuleForm value={{ bin: 'helm', args: ['...', 'list'] }} onChange={() => {}} />);
    expect(screen.getByText(/only works as the/i)).toBeInTheDocument();
  });
});

describe('ruleHasMisplacedRest', () => {
  it('flags "..." anywhere but the last position', () => {
    expect(ruleHasMisplacedRest({ bin: 'helm', args: ['list', '...'] })).toBe(false);
    expect(ruleHasMisplacedRest({ bin: 'helm', args: ['...'] })).toBe(false);
    expect(ruleHasMisplacedRest({ bin: 'helm', args: ['...', 'list'] })).toBe(true);
    expect(ruleHasMisplacedRest({ bin: 'helm', args: ['list', '...', 'x'] })).toBe(true);
  });
});
