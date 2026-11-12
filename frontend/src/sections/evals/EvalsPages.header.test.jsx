import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import Evals from "src/pages/dashboard/evals/Evals";
import EvalsWrapper from "./EvalsWrapper";

// FA-EVALS-01: the evals list, usage and groups pages share one header and
// one set of page links, so each page can reach the other two.
const mocks = vi.hoisted(() => ({ trackEvent: vi.fn() }));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/sections/evals/components/EvalsListView", () => ({
  default: () => <div>Evaluation list</div>,
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { evalsUsagetabClicked: "Evals_Usagetab_Clicked" },
  PropertyName: { click: "click" },
  trackEvent: mocks.trackEvent,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

const LIST_DESCRIPTION =
  "Create, test and reuse evals that score responses for accuracy, safety, tone and more.";

const PAGE_LINKS = [
  ["Evals", "/dashboard/evaluations"],
  ["Usage", "/dashboard/evaluations/usage"],
  ["Groups", "/dashboard/evaluations/groups"],
];

const pages = [
  ["evals list", () => <Evals />, "Evals"],
  [
    "usage",
    () => (
      <EvalsWrapper currentTab="usage">
        <div>Usage grid</div>
      </EvalsWrapper>
    ),
    "Usage",
  ],
  [
    "groups",
    () => (
      <EvalsWrapper currentTab="groups">
        <div>Groups grid</div>
      </EvalsWrapper>
    ),
    "Groups",
  ],
];

beforeEach(() => {
  mocks.trackEvent.mockClear();
  window.history.replaceState({}, "", "/dashboard/evaluations");
});
afterEach(cleanup);

describe("evaluation pages header", () => {
  it.each(pages)(
    "the %s page shows the evals list header",
    (_label, renderPage) => {
      render(renderPage());
      expect(
        screen.getByRole("heading", { level: 1, name: "Evaluations" }),
      ).toBeInTheDocument();
      expect(screen.getByText(LIST_DESCRIPTION)).toBeInTheDocument();
      expect(
        screen.queryByText("Create and manage your evaluations"),
      ).not.toBeInTheDocument();
    },
  );

  it.each(pages)(
    "the %s page links to the list, usage and groups pages",
    (_label, renderPage, currentLabel) => {
      render(renderPage());
      for (const [name, href] of PAGE_LINKS) {
        const tab = screen.getByRole("tab", { name });
        expect(tab).toHaveAttribute("href", href);
        if (name === currentLabel) {
          expect(tab).toHaveAttribute("aria-selected", "true");
          expect(tab).toHaveAttribute("aria-current", "page");
        } else {
          expect(tab).not.toHaveAttribute("aria-current");
        }
      }
    },
  );

  it("opens the usage page from the evals list and records the visit", () => {
    render(<Evals />);
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    expect(window.location.pathname).toBe("/dashboard/evaluations/usage");
    expect(mocks.trackEvent).toHaveBeenCalledWith("Evals_Usagetab_Clicked", {
      click: true,
    });
  });

  it.each(pages)(
    "the %s page keeps a docs link that opens in a new tab",
    (_label, renderPage) => {
      render(renderPage());
      const docs = screen.getByRole("link", { name: "View docs" });
      expect(docs).toHaveAttribute(
        "href",
        "https://docs.futureagi.com/docs/cookbook/quickstart/first-eval",
      );
      expect(docs).toHaveAttribute("target", "_blank");
    },
  );
});
