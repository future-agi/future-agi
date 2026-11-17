import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ReleaseInbox from './ReleaseInbox';
import { RETIRED_TERMS } from '../utils/terms';

// UXF-12: the whole row opens the request; "Open request" stays inline as a
// small ghost button and leads the ⋯ menu with "Mark this revision seen".
// UXF-14: "Open request" and "Request release", never "Review" as the object.

afterEach(cleanup);

const row = { id: 'request', revision: 2, status: 'pending', name: 'support-agent', subjectKind: 'agent_context', unread: true, authorName: 'Alex Author',
  updatedAt: '2026-11-16T10:00:00Z', recipients: [{ id: 'me', name: 'Robin Reviewer' }], transition: { label: 'production', expectedVersion: 1, version: 2 } };
const data = { results: [row], page: 1, pageSize: 25, count: 1, pendingCount: 1 };
const renderInbox = (props = {}) => render(<ReleaseInbox scopeKey="scope" state="ready" data={data} onStatusChange={vi.fn()} onPageChange={vi.fn()} onOpen={vi.fn()} onCompose={vi.fn()} currentUserId="me" {...props} />);

describe('ReleaseInbox', () => {
  it('opens a request from its row and keeps one ghost "Open request"', () => {
    const onOpen = vi.fn();
    renderInbox({ onOpen });
    const open = screen.getByRole('button', { name: 'Open request' });
    expect(open).toHaveClass('MuiButton-text');
    expect(open).not.toHaveClass('MuiButton-outlined');
    fireEvent.click(screen.getByText(/support-agent · Move production/).closest('tr'));
    expect(onOpen).toHaveBeenCalledWith(row);
    fireEvent.click(open);
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it('keeps "Mark this revision seen" in the ⋯ menu, bound to the row revision', async () => {
    const onSeen = vi.fn().mockResolvedValue({});
    const onOpen = vi.fn();
    renderInbox({ onSeen, onOpen });
    fireEvent.click(screen.getByRole('button', { name: 'Actions for support-agent' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Open request', 'Mark this revision seen']);
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Mark this revision seen' }));
    await waitFor(() => expect(onSeen).toHaveBeenCalled());
    expect(onSeen.mock.calls[0][0]).toMatchObject({ requestId: 'request', revision: 2 });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('uses the release request names', () => {
    renderInbox();
    expect(screen.getByRole('button', { name: 'Request release' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
    const text = document.body.textContent;
    for (const retired of RETIRED_TERMS) expect(text).not.toContain(retired);
  });
});
