import PropTypes from "prop-types";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import SkillsReviewsPage from "src/pages/dashboard/skills/SkillsReviewsPage";
import ContextReviewPage from "src/pages/dashboard/skills/contexts/ContextReviewPage";
import { ID, draftReview } from "./fixtures";
import ContextReleaseReview from "../../contexts/ContextReleaseReview";

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


it('keeps publication actions in place while confirming the CAS-bound review', async () => {
  const acknowledge = vi.fn().mockResolvedValue({ revision: 9 });
  const publish = vi.fn();
  const props = { scopeKey: 'scope', state: 'ready', canPublish: true, onAcknowledgeHead: acknowledge, onPublish: publish, onPublishAndRequest: vi.fn() };
  const value = { ...draftReview, headAcknowledged: false };
  const { rerender } = render(<MemoryRouter><ContextReleaseReview {...props} draftReview={value} /></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Publish version' })).toHaveAccessibleDescription('Tick the review box to publish.');
  expect(screen.getByRole('button', { name: 'Publish and request review' })).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed these changes against the current published version.' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm review' }));
  await waitFor(() => expect(acknowledge).toHaveBeenCalledWith(expect.objectContaining({ expectedDigest: value.candidateDigest, reviewFingerprint: value.reviewFingerprint })));
  expect(publish).not.toHaveBeenCalled();
  rerender(<MemoryRouter><ContextReleaseReview {...props} draftReview={{ ...value, headAcknowledged: true, revision: 9, reviewFingerprint: 'confirmed' }} /></MemoryRouter>);
  expect(screen.getByText('Review confirmed')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Confirm review' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Publish and request review' })).toBeVisible();
});

it('shows one footer row with the reason beside the publish actions, and Confirm review inside the acknowledgment group', () => {
  const props = { scopeKey: 'scope', state: 'ready', canPublish: true, onAcknowledgeHead: vi.fn(), onPublish: vi.fn(), onPublishAndRequest: vi.fn(), onRefresh: vi.fn() };
  render(<MemoryRouter><ContextReleaseReview {...props} draftReview={{ ...draftReview, headAcknowledged: false }} /></MemoryRouter>);
  expect(screen.queryByRole('navigation', { name: 'Review sections' })).not.toBeInTheDocument();
  expect(screen.queryByRole('tablist', { name: 'Publication sections' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Version note' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Version note' })).toBeVisible();
  expect(screen.queryByText(/without requesting release/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Publishing doesn.t attach or share test results/)).not.toBeInTheDocument();
  // Confirm review sits with the checkboxes it confirms, with its one-line why.
  const group = screen.getByRole('group', { name: 'Review acknowledgments' });
  expect(within(group).getByRole('checkbox', { name: 'I reviewed these changes against the current published version.' })).toBeInTheDocument();
  expect(within(group).getByRole('button', { name: 'Confirm review' })).toBeVisible();
  expect(within(group).getByText("Locks this review to the current draft, so later edits can't be published by mistake.")).toBeVisible();
  // The footer is one row of three buttons; one reason sits between Reload draft and the publish actions, keeping its id.
  const reason = screen.getByText('Tick the review box to publish.');
  expect(screen.getAllByText('Tick the review box to publish.')).toHaveLength(1);
  expect(reason).toHaveAttribute('id', 'publish-review-reason');
  const row = ['Reload draft', 'Publish version', 'Publish and request review'].map((name) => screen.getByRole('button', { name }));
  expect(screen.queryAllByRole('button', { name: 'Confirm review' }).filter((button) => !group.contains(button))).toHaveLength(0);
  expect(row[0].parentElement).toBe(row[2].parentElement.parentElement);
  row.forEach((button, index) => {
    if (index) expect(row[index - 1].compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  expect(row[0].compareDocumentPosition(reason) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(reason.compareDocumentPosition(row[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(row[0]).toHaveClass('MuiButton-text');
  expect(row[1]).toHaveClass('MuiButton-outlined');
  expect(row[2]).toHaveClass('MuiButton-contained');
  // After the box is ticked the reason changes to the publish-only hint.
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed these changes against the current published version.' }));
  expect(screen.getByText('Confirm the review above to publish only.')).toHaveAttribute('id', 'publish-review-reason');
  expect(screen.getByRole('button', { name: 'Publish version' })).toHaveAccessibleDescription('Confirm the review above to publish only.');
});

it('leads with one line (the version it creates, what changes, where production stays) and renames the engineer sections', () => {
  const props = { scopeKey: 'scope', state: 'ready', canPublish: true, onAcknowledgeHead: vi.fn(), onPublish: vi.fn(), onPublishAndRequest: vi.fn(), onRefresh: vi.fn() };
  render(<MemoryRouter><ContextReleaseReview {...props} name="support-agent" draftReview={{ ...draftReview, headAcknowledged: false }} /></MemoryRouter>);
  const lead = screen.getByRole('status');
  expect(lead).toHaveTextContent(/^v7 (changes|has) .*AGENTS\.md.*\. Production stays on v4 until it is reviewed and released\.$/);
  expect(lead).not.toHaveTextContent(/Publishing creates/);
  expect(screen.getByText('What publishing creates')).toBeVisible();
  ['Technical details', 'Linked skill versions', 'Permission changes'].forEach((name) => expect(screen.getByRole('button', { name })).toBeVisible());
  ['Exact identities', 'Locked versions', 'Permissions comparison', /^Raw /].forEach((name) => expect(screen.queryByRole('button', { name })).not.toBeInTheDocument());
});
