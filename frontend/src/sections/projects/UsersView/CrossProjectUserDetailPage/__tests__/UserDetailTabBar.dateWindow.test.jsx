/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  renderWithRouter,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";

const { savedViewsState, headerState, queryClientState } = vi.hoisted(() => ({
  savedViewsState: { data: { custom_views: [] } },
  headerState: {
    getViewConfig: () => ({ display: {} }),
    setActiveViewConfig: () => {},
  },
  queryClientState: { getQueryData: () => undefined },
}));

vi.mock("src/api/project/saved-views", () => ({
  SAVED_VIEWS_KEY: "saved-views",
  useGetWorkspaceSavedViews: () => ({ data: savedViewsState.data }),
  useCreateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useDeleteWorkspaceSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => headerState,
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => queryClientState,
}));

vi.mock("src/components/traceDetail/SaveViewDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/observe-tabs/FixedTab", () => ({
  default: ({ tabKey, label, onClick }) => (
    <button type="button" onClick={() => onClick(tabKey)}>
      {label}
    </button>
  ),
}));
vi.mock("src/components/observe-tabs/CustomViewTab", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));

import UserDetailTabBar from "../UserDetailTabBar";

const sessionWindow = {
  dateFilter: ["2026-11-02 00:00:00", "2026-11-09 23:59:59"],
  dateOption: "7D",
};
const traceWindow = {
  dateFilter: ["2026-10-01 00:00:00", "2026-10-31 23:59:59"],
  dateOption: "Custom",
};

const currentParams = () => new URLSearchParams(window.location.search);

describe("UserDetailTabBar — switching fixed tabs keeps the date window", () => {
  beforeEach(() => {
    savedViewsState.data = { custom_views: [] };
  });

  it.each([
    { from: "sessions", to: "traces", label: "Trace" },
    { from: "traces", to: "sessions", label: "Sessions" },
  ])(
    "keeps both tabs' windows when going from $from to $to",
    async ({ from, to, label }) => {
      const onTabChange = vi.fn();
      const params = new URLSearchParams({
        userTab: from,
        sessionDateFilter: JSON.stringify(sessionWindow),
        primaryTraceDateFilter: JSON.stringify(traceWindow),
      });
      renderWithRouter(
        <UserDetailTabBar activeTab={from} onTabChange={onTabChange} />,
        { route: `/dashboard/users/bob?${params.toString()}` },
      );

      await userEvent.click(screen.getByRole("button", { name: label }));

      await waitFor(() => expect(currentParams().get("userTab")).toBe(to));
      expect(currentParams().get("sessionDateFilter")).toBe(
        JSON.stringify(sessionWindow),
      );
      expect(currentParams().get("primaryTraceDateFilter")).toBe(
        JSON.stringify(traceWindow),
      );
      expect(onTabChange).toHaveBeenCalledWith(to, to);
    },
  );

  it("does not invent a window for a tab that had none", async () => {
    const params = new URLSearchParams({
      userTab: "sessions",
      sessionDateFilter: JSON.stringify(sessionWindow),
    });
    renderWithRouter(
      <UserDetailTabBar activeTab="sessions" onTabChange={vi.fn()} />,
      { route: `/dashboard/users/bob?${params.toString()}` },
    );

    await userEvent.click(screen.getByRole("button", { name: "Trace" }));

    await waitFor(() => expect(currentParams().get("userTab")).toBe("traces"));
    expect(currentParams().get("sessionDateFilter")).toBe(
      JSON.stringify(sessionWindow),
    );
    expect(currentParams().has("primaryTraceDateFilter")).toBe(false);
  });

  it("drops a saved view's window when leaving it for a fixed tab", async () => {
    savedViewsState.data = {
      custom_views: [
        {
          id: "v1",
          name: "Last month",
          config: {
            sub_tab: "sessions",
            display: { dateFilter: traceWindow },
          },
        },
      ],
    };
    const params = new URLSearchParams({
      userTab: "view-v1",
      sessionDateFilter: JSON.stringify(traceWindow),
    });
    renderWithRouter(
      <UserDetailTabBar activeTab="view-v1" onTabChange={vi.fn()} />,
      { route: `/dashboard/users/bob?${params.toString()}` },
    );

    await userEvent.click(screen.getByRole("button", { name: "Sessions" }));

    await waitFor(() =>
      expect(currentParams().get("userTab")).toBe("sessions"),
    );
    expect(currentParams().has("sessionDateFilter")).toBe(false);
    expect(currentParams().has("primaryTraceDateFilter")).toBe(false);
  });
});
