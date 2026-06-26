/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithRouter, screen, userEvent } from "src/utils/test-utils";

// Capture the payload the save mutation receives so we can assert the encoded
// `sub_tab`. The grouping (trace vs span) is read from the `selectedTab` URL
// param and encoded into config.sub_tab — span must survive as "spans".
const { createMock } = vi.hoisted(() => ({ createMock: vi.fn() }));

vi.mock("src/api/project/saved-views", () => ({
  SAVED_VIEWS_KEY: "saved-views",
  useGetWorkspaceSavedViews: () => ({ data: { custom_views: [] } }),
  useCreateWorkspaceSavedView: () => ({ mutate: createMock }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useDeleteWorkspaceSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => ({
    getViewConfig: () => ({ display: {} }),
    setActiveViewConfig: vi.fn(),
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ getQueryData: vi.fn() }),
}));

// Stub the save dialog to expose onSave directly (skip the popover interaction).
vi.mock("src/components/traceDetail/SaveViewDialog", () => ({
  default: ({ onSave }) => (
    <button
      type="button"
      data-testid="save-confirm"
      onClick={() => onSave("My View")}
    >
      save
    </button>
  ),
}));

vi.mock("src/components/observe-tabs/FixedTab", () => ({
  default: () => null,
}));
vi.mock("src/components/observe-tabs/CustomViewTab", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));

import UserDetailTabBar from "../UserDetailTabBar";

const savedConfigFor = async (route, activeTab) => {
  createMock.mockClear();
  renderWithRouter(
    <UserDetailTabBar activeTab={activeTab} onTabChange={vi.fn()} />,
    { route },
  );
  await userEvent.click(screen.getByTestId("save-confirm"));
  return createMock.mock.calls[0][0].config;
};

describe("UserDetailTabBar — saved-view grouping (sub_tab)", () => {
  beforeEach(() => createMock.mockClear());

  it("encodes the span grouping as sub_tab=spans", async () => {
    const config = await savedConfigFor(
      "/dashboard/users/bob?userTab=traces&selectedTab=spans",
      "traces",
    );
    expect(config.sub_tab).toBe("spans");
  });

  it("encodes the trace grouping as sub_tab=traces", async () => {
    const config = await savedConfigFor(
      "/dashboard/users/bob?userTab=traces&selectedTab=trace",
      "traces",
    );
    expect(config.sub_tab).toBe("traces");
  });

  it("encodes the sessions tab as sub_tab=sessions", async () => {
    const config = await savedConfigFor(
      "/dashboard/users/bob?userTab=sessions",
      "sessions",
    );
    expect(config.sub_tab).toBe("sessions");
  });
});
