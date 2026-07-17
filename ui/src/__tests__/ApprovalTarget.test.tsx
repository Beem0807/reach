import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ApprovalTarget, approvalMatchesQuery } from '../components/ApprovalTarget';
import type { Approval } from '../types';

const base: Approval = {
  approval_id: 'appr_1', agent_id: 'agent_1', tenant_id: 't1',
  command: '', status: 'approved', created_at: '2026-06-20T10:00:00Z',
};

describe('ApprovalTarget', () => {
  it('renders a host rule as structured chips (bin + args, * as "any")', () => {
    const a: Approval = { ...base, command: 'systemctl restart *',
      host_rule: { bin: 'systemctl', args: ['restart', '*'] } };
    render(<ApprovalTarget approval={a} />);
    expect(screen.getByText('systemctl')).toBeInTheDocument();
    expect(screen.getByText('restart')).toBeInTheDocument();
    expect(screen.getByText('any')).toBeInTheDocument();   // the "*" arg
    expect(screen.getByText('bin')).toBeInTheDocument();    // field label
  });

  it('renders a trailing "..." host-rule arg as "any args"', () => {
    const a: Approval = { ...base, command: 'helm list ...',
      host_rule: { bin: 'helm', args: ['list', '...'] } };
    render(<ApprovalTarget approval={a} />);
    expect(screen.getByText('helm')).toBeInTheDocument();
    expect(screen.getByText('list')).toBeInTheDocument();
    expect(screen.getByText('any args')).toBeInTheDocument();   // the "..." arg
  });

  it('renders a k8s rule as chips (unchanged)', () => {
    const a: Approval = { ...base, command: 'kubectl delete pods',
      k8s_rule: { verb: 'delete', resource: 'pods', namespace: 'team-a', name: '*' } };
    render(<ApprovalTarget approval={a} />);
    expect(screen.getByText('delete')).toBeInTheDocument();
    expect(screen.getByText('team-a')).toBeInTheDocument();
  });

  it('falls back to the raw command string for a legacy host approval', () => {
    const a: Approval = { ...base, command: 'docker restart app' };
    render(<ApprovalTarget approval={a} />);
    expect(screen.getByText('docker restart app')).toBeInTheDocument();
  });

  it('search matches host-rule bin and args', () => {
    const a: Approval = { ...base, command: 'systemctl restart *',
      host_rule: { bin: 'systemctl', args: ['restart', '*'] } };
    expect(approvalMatchesQuery(a, 'systemctl')).toBe(true);
    expect(approvalMatchesQuery(a, 'restart')).toBe(true);
    expect(approvalMatchesQuery(a, 'nginx')).toBe(false);
  });
});
