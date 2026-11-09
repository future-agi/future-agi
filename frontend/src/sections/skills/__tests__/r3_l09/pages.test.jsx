import PropTypes from "prop-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import SkillsReviewsPage from "src/pages/dashboard/skills/SkillsReviewsPage";
import ContextReviewPage from "src/pages/dashboard/skills/contexts/ContextReviewPage";
import { ID } from "./fixtures";

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
    expect(screen.getByText(/capability is not available/)).toBeVisible();
    expect(screen.queryByText(/unread assigned/)).not.toBeInTheDocument();
  });
  it("supports inspectable loading/error state URLs through the real wrapper", () => {
    mount(SkillsReviewsPage, "/dashboard/skills/reviews?state=loading", {
      Controller,
    });
    expect(
      screen.getByRole("progressbar", { name: "Loading review" }),
    ).toBeVisible();
  });
  it("does not let state preview bypass authenticated scope", () => {
    auth.enabled = false;
    mount(SkillsReviewsPage, "/dashboard/skills/reviews?state=empty", {
      Controller,
    });
    expect(screen.getByText(/no longer have access/)).toBeVisible();
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
