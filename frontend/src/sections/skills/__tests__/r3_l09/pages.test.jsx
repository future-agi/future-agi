import PropTypes from "prop-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  expect(screen.getByRole('button', { name: 'Publish version' })).toHaveAccessibleDescription('Confirm the review first');
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

it('shows one action row and one reason line above it, with no anchor tabs, no repeated sentences and no Version note heading', () => {
  const props = { scopeKey: 'scope', state: 'ready', canPublish: true, onAcknowledgeHead: vi.fn(), onPublish: vi.fn(), onPublishAndRequest: vi.fn(), onRefresh: vi.fn() };
  render(<MemoryRouter><ContextReleaseReview {...props} draftReview={{ ...draftReview, headAcknowledged: false }} /></MemoryRouter>);
  expect(screen.queryByRole('navigation', { name: 'Review sections' })).not.toBeInTheDocument();
  expect(screen.queryByRole('tablist', { name: 'Publication sections' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Version note' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Version note' })).toBeVisible();
  expect(screen.queryByText(/without requesting release/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Publishing doesn.t attach or share test results/)).not.toBeInTheDocument();
  // One reason, above the row, keeping its id.
  const reason = screen.getByText('Confirm the review first');
  expect(screen.getAllByText('Confirm the review first')).toHaveLength(1);
  expect(reason).toHaveAttribute('id', 'publish-review-reason');
  const row = ['Reload draft', 'Confirm review', 'Publish version', 'Publish and request review'].map((name) => screen.getByRole('button', { name }));
  expect(row[0].parentElement.parentElement).toBe(row[3].parentElement.parentElement.parentElement);
  row.forEach((button, index) => {
    if (index) expect(row[index - 1].compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  expect(reason.compareDocumentPosition(row[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Reload draft' })).toHaveClass('MuiButton-text');
});
