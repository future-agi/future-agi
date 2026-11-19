import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import HumanCheckReview from './HumanCheckReview';
import { HUMAN_CHECK } from '../utils/terms';

// UXF-14: a person's assessment of a result is a "Human check"; "review" is only
// the second person's decision on a release request.

afterEach(cleanup);

it('names the section and its actions as a human check', () => {
  render(<HumanCheckReview scopeKey="scope" state="ready" reference={{ source: 'skills_text', sourceId: 'run', checkId: 'check', attemptId: 'attempt', annotationRevision: 0 }}
    machineVerdict="passed" canAnnotate onAppend={vi.fn()} />);
  expect(screen.getByText(HUMAN_CHECK)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add check' })).toBeDisabled();
  expect(screen.getByText('No human checks yet.')).toBeVisible();
  expect(document.body.textContent).not.toMatch(/Human review|Append review/);
});
