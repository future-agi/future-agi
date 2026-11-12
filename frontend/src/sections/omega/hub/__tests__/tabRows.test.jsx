import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import meMaintainer from "../__fixtures__/me-maintainer.json";
import HubNav from "../components/HubNav";
import { SortTabsLite } from "../components/SortTabsLite";
import { TAB_ROW_PROPS } from "../components/tabRow";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// MUI 5.15 decides whether a scrollable tab row overflows with an IntersectionObserver on its
// first and last tab (jsdom has no layout). This stand-in reports every tab whose label is in
// `hidden` as cut off, so a test can put a row in the state a 390 px phone puts it in.
let hidden = new Set();
class FakeIntersectionObserver {
  constructor(callback) {
    this.callback = callback;
  }

  observe(el) {
    this.callback([
      { target: el, isIntersecting: !hidden.has(el.textContent) },
    ]);
  }

  disconnect() {}

  unobserve() {}
}

const scrollButtons = (container) => [
  ...container.querySelectorAll(".MuiTabs-scrollButtons"),
];

beforeEach(() => {
  hidden = new Set();
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
});
afterEach(() => vi.unstubAllGlobals());

describe("HUB-22: a Hub tab row that does not fit says so", () => {
  it("the shared row settings show the chevrons only while the row overflows, on phones too", () => {
    expect(TAB_ROW_PROPS).toEqual({
      variant: "scrollable",
      scrollButtons: "auto",
      allowScrollButtonsMobile: true,
    });
  });

  it("the Hub nav cut at the edge shows an end chevron that scrolls, and a dimmed start one", async () => {
    hidden = new Set(["Settings", "Review"]);
    respond([["get", "/omega-hub/v1/me", meMaintainer]]);
    const { container } = renderAt(<HubNav />, { at: "/dashboard/hub" });
    await screen.findByTestId("hub-nav-review");
    await waitFor(() => expect(scrollButtons(container)).toHaveLength(2));
    const [start, end] = scrollButtons(container);
    expect(start).toHaveClass("Mui-disabled");
    expect(end).not.toHaveClass("Mui-disabled");
    fireEvent.click(end); // reaches MUI's own scroll handler without throwing
  });

  it("the Hub nav that fits shows no chevrons", async () => {
    respond([["get", "/omega-hub/v1/me", meMaintainer]]);
    const { container } = renderAt(<HubNav />, { at: "/dashboard/hub" });
    await screen.findByTestId("hub-nav-review");
    expect(scrollButtons(container)).toHaveLength(0);
  });

  // The browse sort is a select now (one control row on the Hub home), so it
  // has no tab row to cut; the small filter rows still do.
  it("the small filter rows follow the same rule", () => {
    hidden = new Set(["Verified"]);
    const lite = renderAt(
      <SortTabsLite
        value=""
        onChange={() => {}}
        options={[
          ["", "All"],
          ["verified", "Verified"],
        ]}
      />,
    );
    expect(scrollButtons(lite.container)).toHaveLength(2);
  });
});
