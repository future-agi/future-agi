import React from "react";
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import meUser from "../__fixtures__/me-user.json";
import HubPublishersPage from "../HubPublishersPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// The Hub's own tab row is the first thing on a Hub page, above the page's
// title, as on Publish and Settings, so moving between the Hub's pages never
// moves the row.

const PAGES = [
  {
    page: "Publishers",
    at: "/dashboard/hub/publishers",
    element: <HubPublishersPage />,
  },
  {
    page: "My submissions",
    at: "/dashboard/hub/submissions",
    element: <HubSubmissionsPage />,
  },
];

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  respond([
    [
      "get",
      "/api/capabilities/",
      capabilitiesAnswer({ parts: [OMEGA_PART.HUB_PUBLISH] }),
    ],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/publishers", { count: 0, results: [] }],
    ["get", "/omega-hub/v1/submissions", []],
  ]);
});
afterEach(() => vi.unstubAllEnvs());

describe("The Hub's tab row sits above the page title", () => {
  it.each(PAGES)(
    "on $page, with its own tab selected",
    async ({ page, at, element }) => {
      renderAt(element, { at });
      const title = await screen.findByRole("heading", {
        level: 1,
        name: page,
      });
      const nav = screen.getByTestId("hub-nav-main");
      expect(
        nav.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(within(nav).getByRole("tab", { name: page })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    },
  );
});
