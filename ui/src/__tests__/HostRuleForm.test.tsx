import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { HostRuleForm, EMPTY_HOST_RULE, parseHostRule, hostRuleToText } from '../components/HostRuleForm';
import type { HostRule } from '../types';

describe('parseHostRule', () => {
  it('splits a command pattern into bin + args (first token is the bin)', () => {
    expect(parseHostRule('systemctl restart *')).toEqual({ bin: 'systemctl', args: ['restart', '*'] });
    expect(parseHostRule('  df   -h ')).toEqual({ bin: 'df', args: ['-h'] });
    expect(parseHostRule('uptime')).toEqual({ bin: 'uptime', args: [] });
    expect(parseHostRule('')).toEqual({ bin: '', args: [] });
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
});
