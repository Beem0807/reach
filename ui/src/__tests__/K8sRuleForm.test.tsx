import { describe, it, expect } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { K8sRuleForm, EMPTY_RULE } from '../components/K8sRuleForm';
import type { K8sRule } from '../types';

// The form is controlled, so drive it through a stateful harness that mirrors
// how the approvals page wires value/onChange.
function Harness({ initial = EMPTY_RULE }: { initial?: K8sRule }) {
  const [rule, setRule] = useState<K8sRule>(initial);
  return <K8sRuleForm value={rule} onChange={setRule} />;
}

const resourceInput = () => screen.getByPlaceholderText(/pods, deployments/i) as HTMLInputElement;
const namespaceInput = () => screen.getByPlaceholderText(/team-a or/i) as HTMLInputElement;
const nameInput = () => screen.getByPlaceholderText(/specific object or/i) as HTMLInputElement;

describe('K8sRuleForm wildcard fields', () => {
  it('defaults resource/namespace/name to "*"', () => {
    render(<Harness />);
    expect(resourceInput()).toHaveValue('*');
    expect(namespaceInput()).toHaveValue('*');
    expect(nameInput()).toHaveValue('*');
  });

  it('can be cleared while editing - a keystroke does not force "*" back', () => {
    render(<Harness />);
    const input = resourceInput();
    fireEvent.change(input, { target: { value: '' } });
    expect(input).toHaveValue(''); // stays empty, not coerced on change
  });

  it('falls back to "*" on blur when left empty', () => {
    render(<Harness />);
    const input = resourceInput();
    fireEvent.change(input, { target: { value: '' } });
    expect(input).toHaveValue('');
    fireEvent.blur(input);
    expect(input).toHaveValue('*');
  });

  it('keeps a typed value on blur and trims surrounding space', () => {
    render(<Harness />);
    const input = resourceInput();
    fireEvent.change(input, { target: { value: '  deployments  ' } });
    fireEvent.blur(input);
    expect(input).toHaveValue('deployments');
  });

  it('applies the same clear/blur behavior to namespace and name', () => {
    render(<Harness />);
    for (const input of [namespaceInput(), nameInput()]) {
      fireEvent.change(input, { target: { value: '' } });
      expect(input).toHaveValue('');
      fireEvent.blur(input);
      expect(input).toHaveValue('*');
    }
  });
});

describe('K8sRuleForm resource dropdown', () => {
  it('is a datalist-backed combobox that still allows free text (CRDs)', () => {
    render(<Harness />);
    const input = resourceInput();
    expect(input).toHaveAttribute('list', 'k8s-resource-options');

    const dl = document.getElementById('k8s-resource-options');
    expect(dl?.tagName).toBe('DATALIST');
    const values = Array.from(dl!.querySelectorAll('option')).map(o => o.getAttribute('value'));
    expect(values).toContain('deployments');
    expect(values).toContain('*');
    expect(values).toContain('pods/exec');

    // Free text: a CRD not in the suggestions is accepted and kept on blur.
    fireEvent.change(input, { target: { value: 'virtualservices' } });
    fireEvent.blur(input);
    expect(input).toHaveValue('virtualservices');
  });
});
