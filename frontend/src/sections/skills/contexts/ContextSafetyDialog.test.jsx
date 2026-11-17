import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import ContextSafetyDialog from './ContextSafetyDialog';
import { PROTECTED_LABEL } from '../utils/terms';

// UXF-09: a protected label is explained where it appears; the restore
// acknowledgment itself is unchanged.

afterEach(cleanup);

it('explains a protected label in the restore line and keeps the acknowledgment', () => {
  render(<ContextSafetyDialog scopeKey="scope" open onClose={() => {}} action="unarchive" canAct resource={{ id: 'skill', name: 'refund-policy' }}
    review={{ subjectId: 'skill', releaseRevision: 3, reviewFingerprint: `sha256:${'a'.repeat(64)}`, labels: [{ name: 'production', version: 2, isProtected: true }] }} onApply={async () => ({})} />);
  expect(screen.getByText(`Production will point at v2 again · ${PROTECTED_LABEL}. Apps following production pick it up on their next fetch.`)).toBeVisible();
  expect(document.body.textContent).not.toMatch(/· protected\b/);
  expect(screen.getByRole('checkbox', { name: "I've checked which labels come back with this skill." })).toBeVisible();
});
