import { afterEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ReleaseRequestPanel from './ReleaseRequestPanel';

const receipt = {
  requestId: 'request-metadata', decisionId: 'decision-metadata', executedBy: 'reviewer-metadata',
  currentnessAtAction: { release_revision: 1, packet_fingerprint: 'reviewed-fingerprint' },
  postMoveState: { version: 2, releaseRevision: 2, eventId: 'recorded-event' },
};
afterEach(cleanup);
it('renders a metadata-only receipt without requiring retained packet contents', () => {
  render(<ReleaseRequestPanel scopeKey="org:workspace:reviewer" state="ready" receipt={receipt} />);
  expect(screen.getByText('Release receipt')).toBeVisible();
  // The page title states the move once ("Production moved from v1 to v2"); the receipt itself does not repeat it.
  expect(screen.queryByText('Production moved to v2')).not.toBeInTheDocument();
  expect(screen.queryByText('Recorded preconditions')).not.toBeInTheDocument();
  // Before/after state is the receipt's evidence and is visible without a disclosure.
  expect(screen.getByText('Before the move')).toBeVisible();
  expect(screen.getByText('After the move')).toBeVisible();
  expect(screen.getByText('Production v2')).toBeVisible();
  // Compare-and-swap counters are audit details, not primary copy.
  expect(screen.queryByText(/Currentness|Pointer state/)).not.toBeInTheDocument();
  expect(screen.queryByText(/release revision 1/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Audit details' }));
  expect(screen.getByText(/"release_revision": 1/)).toBeVisible();
  expect(screen.getByText(/Before: release revision 1/)).toBeVisible();
  expect(screen.getByText(/"releaseRevision": 2/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Release to production' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Review is stale or unavailable/)).not.toBeInTheDocument();
});
it.each(['loading', 'error', 'forbidden', 'unavailable'])('does not expose cached metadata in %s state', (state) => {
  render(<ReleaseRequestPanel scopeKey="org:workspace:reviewer" state={state} receipt={receipt} />);
  expect(screen.queryByText('Release receipt')).not.toBeInTheDocument();
  expect(screen.queryByText('request-metadata')).not.toBeInTheDocument();
});
it('does not expose a receipt without the active scope', () => {
  render(<ReleaseRequestPanel state="ready" receipt={receipt} />);
  expect(screen.queryByText('Release receipt')).not.toBeInTheDocument();
});

it('links to delivery observations without claiming the release was delivered', () => {
  render(<ReleaseRequestPanel scopeKey="org:workspace:reviewer" state="ready" receipt={{ ...receipt, subjectKind: 'agent_context', subjectId: 'context-id' }} />);
  expect(screen.getByRole('link', { name: 'Watch delivery' })).toHaveAttribute('href', expect.stringContaining('context-id?tab=consumers'));
  expect(screen.queryByText(/New conversations get|delivered to/)).not.toBeInTheDocument();
});
