import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import ReleaseRequestPanel from './ReleaseRequestPanel';

const receipt = {
  requestId: 'request-metadata', decisionId: 'decision-metadata', executedBy: 'reviewer-metadata',
  currentnessAtAction: { release_revision: 1, packet_fingerprint: 'reviewed-fingerprint' },
  postMoveState: { version: 2, releaseRevision: 2, eventId: 'recorded-event' },
};
afterEach(cleanup);
it('renders a metadata-only receipt without requiring retained packet contents', () => {
  render(<ReleaseRequestPanel scopeKey="org:workspace:reviewer" state="ready" receipt={receipt} />);
  expect(screen.getByText('Durable release receipt')).toBeVisible();
  expect(screen.getByText('Currentness before the move')).toBeVisible();
  expect(screen.getByText('Pointer state after the move')).toBeVisible();
  expect(screen.getByText(/"release_revision": 1/)).toBeVisible();
  expect(screen.getByText(/"releaseRevision": 2/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Release approved packet' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Review is stale or unavailable/)).not.toBeInTheDocument();
});
it.each(['loading', 'error', 'forbidden', 'unavailable'])('does not expose cached metadata in %s state', (state) => {
  render(<ReleaseRequestPanel scopeKey="org:workspace:reviewer" state={state} receipt={receipt} />);
  expect(screen.queryByText('Durable release receipt')).not.toBeInTheDocument();
  expect(screen.queryByText('request-metadata')).not.toBeInTheDocument();
});
it('does not expose a receipt without the active scope', () => {
  render(<ReleaseRequestPanel state="ready" receipt={receipt} />);
  expect(screen.queryByText('Durable release receipt')).not.toBeInTheDocument();
});
