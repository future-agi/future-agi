import PropTypes from "prop-types";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import SkillsReviewsPage from "src/pages/dashboard/skills/SkillsReviewsPage";
import ContextReviewPage from "src/pages/dashboard/skills/contexts/ContextReviewPage";
import { ID, draftReview } from "./fixtures";
import ContextReleaseReview from "../../contexts/ContextReleaseReview";
import { useReviewedOperation } from "../../review/ReleaseRequestPanel";

const auth = vi.hoisted(() => ({
  enabled: true,
  identity: "org:workspace:reviewer",
}));
vi.mock("src/api/skills/testing", () => ({ useSkillV11Scope: () => auth }));
afterEach(() => {
  cleanup();
  auth.enabled = true;
});
function Controller({ View, scopeKey, viewState }) {
  return <View scopeKey={scopeKey} state={viewState ?? "empty"} />;
}
Controller.propTypes = {
  View: PropTypes.elementType,
  scopeKey: PropTypes.string,
  viewState: PropTypes.string,
};
function mount(Page, url, props = {}) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/dashboard/skills/reviews"
            element={<Page {...props} />}
          />
          <Route
            path="/dashboard/skills/contexts/:contextId/review"
            element={<Page {...props} />}
          />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

describe("route wrappers and unavailable integration boundary", () => {
  it("fails closed without the API-owning controller rather than inventing a client or success state", () => {
    mount(SkillsReviewsPage, "/dashboard/skills/reviews", { Controller: null });
    expect(screen.getByText(/Release reviews are not available/)).toBeVisible();
    expect(screen.queryByText(/unread assigned/)).not.toBeInTheDocument();
  });
  it("supports inspectable loading/error state URLs through the real wrapper", () => {
    mount(SkillsReviewsPage, "/dashboard/skills/reviews?state=loading", {
      Controller,
    });
    expect(
      screen.getByRole("progressbar", { name: "Loading reviews" }),
    ).toBeVisible();
  });
  it("does not let state preview bypass authenticated scope", () => {
    auth.enabled = false;
    mount(SkillsReviewsPage, "/dashboard/skills/reviews?state=empty", {
      Controller,
    });
    expect(screen.getByText(/don't have access to reviews/)).toBeVisible();
    expect(screen.queryByText("Nothing to review")).not.toBeInTheDocument();
  });
  it("passes only exact context/request identities and no private text in route state", () => {
    const capture = vi.fn(() => null);
    mount(
      ContextReviewPage,
      `/dashboard/skills/contexts/${ID}/review?request=${ID}`,
      { Controller: capture },
    );
    expect(capture.mock.calls[0][0]).toMatchObject({
      scopeKey: auth.identity,
      contextId: ID,
      requestId: ID,
    });
  });
});


const REVIEWED = 'I reviewed these changes against the current published version.';
const publishProps = (extra = {}) => ({ scopeKey: 'scope', state: 'ready', canPublish: true, onPublish: vi.fn().mockResolvedValue({}), onPublishAndRequest: vi.fn().mockResolvedValue({}), onRefresh: vi.fn(), ...extra });

it('has no separate confirm step: ticking the review box enables both publish buttons', () => {
  render(<MemoryRouter><ContextReleaseReview {...publishProps()} draftReview={{ ...draftReview, headAcknowledged: false }} /></MemoryRouter>);
  expect(screen.queryByRole('button', { name: 'Confirm review' })).not.toBeInTheDocument();
  expect(screen.queryByText('Review confirmed')).not.toBeInTheDocument();
  expect(screen.queryByText(/Confirm the review above/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Publish version' })).toHaveAccessibleDescription('Tick the review box to publish.');
  expect(screen.getByRole('button', { name: 'Publish and request review' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: REVIEWED }));
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Publish and request review' })).toBeEnabled();
  expect(screen.queryByText('Tick the review box to publish.')).not.toBeInTheDocument();
});

it('publishes with one click that carries the unacknowledged head and the ticks, and survives the reload remount', async () => {
  const onPublish = vi.fn().mockResolvedValue({});
  const props = publishProps({ onPublish });
  const value = { ...draftReview, headAcknowledged: false, note: '' };
  const { rerender } = render(<MemoryRouter><ContextReleaseReview {...props} draftReview={value} /></MemoryRouter>);
  fireEvent.change(screen.getByRole('textbox', { name: 'Version note' }), { target: { value: 'Cap refunds' } });
  fireEvent.click(screen.getByRole('checkbox', { name: REVIEWED }));
  fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
  await waitFor(() => expect(onPublish).toHaveBeenCalledTimes(1));
  expect(onPublish.mock.calls[0][0]).toMatchObject({ headAcknowledged: false, expectedDigest: value.candidateDigest, reviewFingerprint: value.reviewFingerprint, note: 'Cap refunds', baseVersion: value.baseVersion, acknowledgeWarnings: false, idempotencyKey: expect.any(String) });
  // The head acknowledgment inside that click reloads the review for the same bytes: the ticks and note stay.
  rerender(<MemoryRouter><ContextReleaseReview {...props} draftReview={{ ...value, headAcknowledged: true, revision: 9, reviewFingerprint: 'confirmed' }} /></MemoryRouter>);
  expect(screen.getByRole('checkbox', { name: REVIEWED })).toBeChecked();
  expect(screen.getByRole('textbox', { name: 'Version note' })).toHaveValue('Cap refunds');
});

it('unticks the boxes and says the draft changed when the acknowledgment loses its CAS (409)', async () => {
  const onRefresh = vi.fn();
  const onPublish = vi.fn().mockRejectedValue(Object.assign(new Error('The publication head or candidate changed; review again.'), { statusCode: 409 }));
  const warned = { ...draftReview, headAcknowledged: false, checks: { items: [], summary: { errors: 0, warnings: 1, infos: 0 }, tokens: {} } };
  render(<MemoryRouter><ContextReleaseReview {...publishProps({ onPublish, onRefresh })} draftReview={warned} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('checkbox', { name: REVIEWED }));
  fireEvent.click(screen.getByRole('checkbox', { name: "I've read the check warnings for this version." }));
  fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
  expect(await screen.findByText('The draft changed since you reviewed it. Review the changes again.')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: REVIEWED })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: "I've read the check warnings for this version." })).not.toBeChecked();
  expect(onRefresh).toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  expect(onPublish).toHaveBeenCalledTimes(1);
  // The reviewer ticks again to continue.
  fireEvent.click(screen.getByRole('checkbox', { name: REVIEWED }));
  expect(screen.queryByText('The draft changed since you reviewed it. Review the changes again.')).not.toBeInTheDocument();
});

it('also unticks when the 409 arrives after the head step, once the review has remounted with carried ticks', async () => {
  const operation = { current: null };
  function Host() {
    const held = useReviewedOperation();
    operation.current = held;
    return <MemoryRouter><ContextReleaseReview {...publishProps({ onPublish: vi.fn(), publishOperation: held })} draftReview={host.review} /></MemoryRouter>;
  }
  const host = { review: { ...draftReview, headAcknowledged: false, note: '' } };
  const onPublish = vi.fn().mockRejectedValue(Object.assign(new Error('stale'), { statusCode: 409 }));
  const { rerender } = render(<Host />);
  fireEvent.click(screen.getByRole('checkbox', { name: REVIEWED }));
  // The click records the ticks, runs the chain, and the controller-held operation outlives the remount.
  fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
  expect(operation.current).not.toBeNull();
  host.review = { ...host.review, headAcknowledged: true, revision: 9, reviewFingerprint: 'confirmed' };
  rerender(<Host />);
  expect(screen.getByRole('checkbox', { name: REVIEWED })).toBeChecked();
  await act(async () => { await operation.current.run(onPublish, { revision: 9 }); });
  expect(await screen.findByText('The draft changed since you reviewed it. Review the changes again.')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: REVIEWED })).not.toBeChecked();
});

it('shows one sticky decision bar: checkbox, reason, outlined Publish version, then the contained primary, with Reload draft beside the lead line', () => {
  const props = publishProps();
  render(<MemoryRouter><ContextReleaseReview {...props} draftReview={{ ...draftReview, headAcknowledged: false }} /></MemoryRouter>);
  expect(screen.queryByRole('navigation', { name: 'Review sections' })).not.toBeInTheDocument();
  expect(screen.queryByRole('tablist', { name: 'Publication sections' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Version note' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Version note' })).toBeVisible();
  expect(screen.queryByText(/without requesting release/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Publishing doesn.t attach or share test results/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Locks this review to the current draft/)).not.toBeInTheDocument();
  const group = screen.getByRole('group', { name: 'Review acknowledgments' });
  const checkbox = within(group).getByRole('checkbox', { name: REVIEWED });
  const reason = screen.getByText('Tick the review box to publish.');
  expect(screen.getAllByText('Tick the review box to publish.')).toHaveLength(1);
  expect(reason).toHaveAttribute('id', 'publish-review-reason');
  const publish = screen.getByRole('button', { name: 'Publish version' });
  const request = screen.getByRole('button', { name: 'Publish and request review' });
  const follows = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(follows(checkbox, reason)).toBe(true);
  expect(follows(reason, publish)).toBe(true);
  expect(follows(publish, request)).toBe(true);
  expect(publish).toHaveClass('MuiButton-outlined');
  expect(request).toHaveClass('MuiButton-contained');
  // One bar: the group, the reason and both buttons share the sticky footer, which the page measures.
  const bar = publish.closest('[data-sticky-footer]');
  expect(bar).not.toBeNull();
  expect(bar).toContainElement(group);
  expect(bar).toContainElement(request);
  // Reload draft is a small text button next to the lead line, outside the bar.
  const reload = screen.getByRole('button', { name: 'Reload draft' });
  expect(reload).toHaveClass('MuiButton-text');
  expect(bar).not.toContainElement(reload);
  expect(reload.parentElement).toContainElement(screen.getByRole('status'));
  // After the box is ticked the reason goes away; nothing else waits.
  fireEvent.click(checkbox);
  expect(screen.queryByText('Tick the review box to publish.')).not.toBeInTheDocument();
  expect(publish).not.toHaveAccessibleDescription('Tick the review box to publish.');
});

it('leads with one line (the version it creates, what changes, where production stays) and renames the engineer sections', () => {
  const props = { scopeKey: 'scope', state: 'ready', canPublish: true, onPublish: vi.fn(), onPublishAndRequest: vi.fn(), onRefresh: vi.fn() };
  render(<MemoryRouter><ContextReleaseReview {...props} name="support-agent" draftReview={{ ...draftReview, headAcknowledged: false }} /></MemoryRouter>);
  const lead = screen.getByRole('status');
  expect(lead).toHaveTextContent(/^v7 (changes|has) .*AGENTS\.md.*\. Production stays on v4 until it is reviewed and released\.$/);
  expect(lead).not.toHaveTextContent(/Publishing creates/);
  expect(screen.getByText('What publishing creates')).toBeVisible();
  ['Technical details', 'Linked skill versions', 'Permission changes'].forEach((name) => expect(screen.getByRole('button', { name })).toBeVisible());
  ['Exact identities', 'Locked versions', 'Permissions comparison', /^Raw /].forEach((name) => expect(screen.queryByRole('button', { name })).not.toBeInTheDocument());
});
