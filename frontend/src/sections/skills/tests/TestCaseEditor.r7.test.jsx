import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import TestCaseEditor from './TestCaseEditor';

afterEach(cleanup);
const example = { id: 'case', title: 'Refund', prompt: 'Refund order 7', fixtures: [], expectedTrigger: true, checks: [{ id: 'check', kind: 'contains', critical: true, config: { text: '$25', caseSensitive: false } }], reviewed: true, sourceGroupId: 'retained' };
it('hides skill routing in context mode without deleting stored checks or trigger values', () => {
  const save = vi.fn();
  render(<TestCaseEditor open subjectKind="agent_context" initialCase={example} applicationBehavior={{ skills: [{ id: 'refund', label: 'Refund policy' }] }} onClose={() => {}} onSave={save} />);
  expect(screen.queryByRole('combobox', { name: 'Use this skill?' })).not.toBeInTheDocument();
  expect(screen.getByRole('radiogroup', { name: 'Required skill' })).toBeVisible();
  expect(screen.getByRole('radiogroup', { name: 'Required business action' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Save example' }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ expectedTrigger: true, checks: example.checks }));
});
it('keeps the routing control for skill cases', () => {
  render(<TestCaseEditor open initialCase={example} onClose={() => {}} onSave={() => {}} />);
  expect(screen.getByRole('combobox', { name: 'Use this skill?' })).toBeVisible();
});
