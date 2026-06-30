import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { K8sPermissionsView } from '../components/K8sPermissionsView';
import type { K8sPermissions } from '../types';

const perms: K8sPermissions = {
  hash: 'h1',
  incomplete: false,
  cluster_wide: [
    { verbs: ['get', 'list', 'watch'], api_groups: [''], resources: ['pods'] },
    { verbs: ['get', 'list', 'create', 'delete'], api_groups: [''], resources: ['secrets'] },
    { verbs: ['*'], api_groups: ['*'], resources: ['*'] },
  ],
};

describe('K8sPermissionsView', () => {
  it('translates verbs into readable access labels', () => {
    render(<K8sPermissionsView permissions={perms} />);
    expect(screen.getByText('Read')).toBeInTheDocument();
    expect(screen.getByText('Read + Write + Delete')).toBeInTheDocument();
    expect(screen.getByText('Full control')).toBeInTheDocument();
    expect(screen.getByText('pods')).toBeInTheDocument();
  });

  it('shows the exact verbs alongside the readable label', () => {
    render(<K8sPermissionsView permissions={perms} />);
    expect(screen.getByText(/verbs: get, list, watch/)).toBeInTheDocument();
    expect(screen.getByText(/verbs: get, list, create, delete/)).toBeInTheDocument();
  });

  it('flags dangerous grants from the raw rules', () => {
    render(<K8sPermissionsView permissions={perms} />);
    expect(screen.getByText(/secrets write/)).toBeInTheDocument();
    expect(screen.getByText(/full cluster access/)).toBeInTheDocument();
  });

  it('shows a partial badge when the review is incomplete', () => {
    render(<K8sPermissionsView permissions={{ hash: 'h2', incomplete: true, cluster_wide: [] }} />);
    expect(screen.getByText(/could not fully evaluate/)).toBeInTheDocument();
  });

  it('shows a distinct truncated badge when size-capped', () => {
    render(<K8sPermissionsView permissions={{ hash: 'h2t', incomplete: false, truncated: true, cluster_wide: [] }} />);
    expect(screen.getByText(/truncated/)).toBeInTheDocument();
    expect(screen.queryByText(/could not fully evaluate/)).toBeNull();
  });

  it('renders the cluster-wide baseline plus per-namespace deltas', () => {
    const multi: K8sPermissions = {
      hash: 'h3',
      incomplete: false,
      cluster_wide: [{ verbs: ['get'], resources: ['pods'] }],
      namespaces: [
        { namespace: 'team-a', resource_rules: [{ verbs: ['delete'], resources: ['deployments'] }] },
        { namespace: 'team-b', resource_rules: [{ verbs: ['create'], resources: ['configmaps'] }] },
      ],
    };
    render(<K8sPermissionsView permissions={multi} />);
    expect(screen.getByText(/Effective in every namespace/)).toBeInTheDocument();
    expect(screen.getByText(/Additionally in team-a/)).toBeInTheDocument();
    expect(screen.getByText(/Additionally in team-b/)).toBeInTheDocument();
  });

  it('handles no cluster-wide baseline: per-namespace sections, no empty header', () => {
    const noBaseline: K8sPermissions = {
      hash: 'h4',
      incomplete: false,
      cluster_wide: [],
      namespaces: [
        { namespace: 'team-a', resource_rules: [{ verbs: ['get'], resources: ['pods'] }] },
        { namespace: 'team-b', resource_rules: [{ verbs: ['delete'], resources: ['secrets'] }] },
      ],
    };
    render(<K8sPermissionsView permissions={noBaseline} />);
    expect(screen.queryByText(/Effective in every namespace/)).toBeNull();
    expect(screen.getByText('In team-a')).toBeInTheDocument();
    expect(screen.getByText('In team-b')).toBeInTheDocument();
  });

  it('shows "no permissions" when there are none at all', () => {
    render(<K8sPermissionsView permissions={{ hash: 'h5', incomplete: false, cluster_wide: [] }} />);
    expect(screen.getByText(/No permissions reported/)).toBeInTheDocument();
  });

  it('shows acknowledge only on drift and fires the callback', () => {
    const onAck = vi.fn();
    const { rerender } = render(
      <K8sPermissionsView permissions={perms} drift={false} onAcknowledge={onAck} />
    );
    expect(screen.queryByText('Acknowledge')).toBeNull();

    rerender(<K8sPermissionsView permissions={perms} drift onAcknowledge={onAck} />);
    expect(screen.getByText(/needs acknowledgement/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Acknowledge'));
    expect(onAck).toHaveBeenCalledOnce();
  });

  // ----- drift diff (current vs acknowledged) -----
  const ackedBase: K8sPermissions = {
    hash: 'a1', incomplete: false,
    cluster_wide: [
      { verbs: ['get', 'list', 'watch'], api_groups: [''], resources: ['pods'] },
      { verbs: ['get'], api_groups: [''], resources: ['secrets'] },
    ],
  };

  it('shows added rules in the diff when drifted', () => {
    const current: K8sPermissions = {
      ...ackedBase, hash: 'c1',
      cluster_wide: [
        ...ackedBase.cluster_wide,
        { verbs: ['get', 'list'], api_groups: ['apps'], resources: ['deployments'] },
      ],
    };
    render(<K8sPermissionsView permissions={current} acked={ackedBase} drift />);
    expect(screen.getByText('Changes since acknowledged')).toBeInTheDocument();
    expect(screen.getByText(/\+ Every namespace: deployments/)).toBeInTheDocument();
  });

  it('shows removed rules in the diff', () => {
    const current: K8sPermissions = {
      ...ackedBase, hash: 'c2',
      cluster_wide: [{ verbs: ['get', 'list', 'watch'], api_groups: [''], resources: ['pods'] }],
    };
    render(<K8sPermissionsView permissions={current} acked={ackedBase} drift />);
    expect(screen.getByText(/− Every namespace: secrets/)).toBeInTheDocument();
  });

  it('shows a verb change as "changed" (from -> to)', () => {
    const current: K8sPermissions = {
      ...ackedBase, hash: 'c3',
      cluster_wide: [
        { verbs: ['get', 'list', 'watch', 'delete'], api_groups: [''], resources: ['pods'] },
        { verbs: ['get'], api_groups: [''], resources: ['secrets'] },
      ],
    };
    render(<K8sPermissionsView permissions={current} acked={ackedBase} drift />);
    expect(screen.getByText(/~ Every namespace: pods/)).toBeInTheDocument();
  });

  it('marks the diff partial and hides the raw view when a snapshot is truncated', () => {
    const current: K8sPermissions = { ...ackedBase, hash: 'c4', truncated: true };
    render(<K8sPermissionsView permissions={current} acked={ackedBase} drift />);
    // Clear truncation notice, and the raw side-by-side is not offered.
    expect(screen.getByText(/partial - snapshot truncated/)).toBeInTheDocument();
    expect(screen.getByText(/raw side-by-side view is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText('View raw')).toBeNull();
  });

  it('does not show a diff when not drifted', () => {
    render(<K8sPermissionsView permissions={perms} acked={ackedBase} drift={false} />);
    expect(screen.queryByText('Changes since acknowledged')).toBeNull();
  });


  it('offers a raw side-by-side diff via "View raw"', () => {
    const current: K8sPermissions = {
      ...ackedBase, hash: 'c5',
      cluster_wide: [
        ...ackedBase.cluster_wide,
        { verbs: ['get', 'list'], api_groups: ['apps'], resources: ['deployments'] },
      ],
    };
    render(<K8sPermissionsView permissions={current} acked={ackedBase} drift />);
    // Summary is the default; raw is hidden until toggled.
    expect(screen.queryByText('Acknowledged')).toBeNull();
    fireEvent.click(screen.getByText('View raw'));
    // Side-by-side headers appear.
    expect(screen.getByText('Acknowledged')).toBeInTheDocument();
    expect(screen.getByText('Current')).toBeInTheDocument();
    // The added rule's resources line shows in the raw YAML.
    expect(screen.getByText(/resources: \["deployments"\]/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Hide raw'));
    expect(screen.queryByText('Acknowledged')).toBeNull();
  });

});
