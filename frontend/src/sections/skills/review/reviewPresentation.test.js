import { describe, expect, it } from 'vitest';
import { deliveryTab, labelTitle, moveLabelTo, releaseTitle, reviewerBeforeMove, unchangedUntilRelease, WAITING_FOR_APPROVAL } from './reviewPresentation';

describe('release wording is built from the request label', () => {
  it('titles first releases, moves and rollbacks', () => {
    expect(releaseTitle({ label: 'staging', expectedVersion: null, version: 1 })).toBe('First release to staging (v1)');
    expect(releaseTitle({ label: 'production', expectedVersion: 1, version: 2 })).toBe('Move production from v1 to v2');
    expect(releaseTitle({ label: 'production', expectedVersion: 2, version: 1 })).toBe('Roll back production to v1');
  });
  it('never hard-codes production', () => {
    expect(moveLabelTo('staging', 1)).toBe('Move staging to v1');
    expect(unchangedUntilRelease('staging')).toBe('Staging stays unchanged until approval and release.');
    expect(reviewerBeforeMove('staging')).toBe('An independent reviewer must approve before staging can move.');
    expect(labelTitle('production')).toBe('Production');
    expect(deliveryTab('agent_context')).toBe('Connected apps');
    expect(deliveryTab('skill')).toBe('Activity');
    expect(WAITING_FOR_APPROVAL).toBe('Waiting for approval');
  });
});
