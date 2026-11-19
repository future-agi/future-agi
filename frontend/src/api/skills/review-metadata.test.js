import { expect, it } from 'vitest';
import { enrichReviewDisplay } from './review-metadata';

const request = { id: 'request', revision: 2, authorId: 'author', subjectId: 'skill', subjectKind: 'skill', recipients: [{ id: 'reviewer' }], packet: { transition: { expectedVersion: 1, version: 2 } } };
const receipt = { requestId: 'request', revision: 2, executedBy: 'executor' };
const members = [{ id: 'author', name: 'Alex Author' }, { id: 'reviewer', name: 'Robin Reviewer' }, { id: 'executor', name: 'Erin Releaser' }];

it('uses authorized directory names and resource metadata without changing authority', () => {
  const result = enrichReviewDisplay(request, receipt, members, { name: 'Support agent', permissions: { canPromote: true } });
  expect(result.request).toMatchObject({ name: 'Support agent', authorName: 'Alex Author', recipients: [{ id: 'reviewer', name: 'Robin Reviewer' }] });
  expect(result.receipt).toMatchObject({ executedByName: 'Erin Releaser', name: 'Support agent', previousVersion: 1, subjectId: 'skill', subjectKind: 'skill' });
  expect(result.request.permissions).toBeUndefined();
  expect(result.request.packet).toBe(request.packet);
  expect(request.name).toBeUndefined();
});
it('does not infer who approved or when from assignment, execution or current time', () => {
  const result = enrichReviewDisplay(request, receipt, members);
  expect(result.request.decidedByName).toBeUndefined();
  expect(result.receipt.approvedByName).toBeUndefined();
  expect(result.receipt.recordedAt).toBeUndefined();
});
it('keeps server-recorded names and the explicit unassigned previous version', () => {
  const result = enrichReviewDisplay({ ...request, authorName: 'Recorded author' }, { ...receipt, executedByName: 'Recorded executor', previousVersion: null }, members);
  expect(result.request.authorName).toBe('Recorded author');
  expect(result.receipt.executedByName).toBe('Recorded executor');
  expect(result.receipt.previousVersion).toBeNull();
});
it('never borrows transition or subject metadata from a different request or revision', () => {
  for (const different of [{ ...receipt, requestId: 'other' }, { ...receipt, revision: 1 }, { ...receipt, revision: undefined }]) {
    const result = enrichReviewDisplay(request, different, members, { name: 'Support agent' });
    expect(result.receipt.previousVersion).toBeUndefined();
    expect(result.receipt.subjectId).toBeUndefined();
    expect(result.receipt.name).toBeUndefined();
  }
});
it('keeps the review usable when optional display reads are unavailable', () => {
  expect(enrichReviewDisplay(request, receipt).request.packet).toBe(request.packet);
  expect(enrichReviewDisplay(undefined, undefined)).toEqual({ request: undefined, receipt: undefined });
});
