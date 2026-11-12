/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes, useNavigate } from "react-router-dom";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { useLLMTracingStore } from "src/sections/projects/LLMTracing/states";

// The detail page is real; its heavy views and saved-view plumbing are
// stubbed so the test sees only where Back takes the user. Each view stub
// pushes a new user-detail entry, like the real group-by switch does.
const { setActiveViewConfig } = vi.hoisted(() => ({
  setActiveViewConfig: vi.fn(),
}));

vi.mock("src/sections/project/context/ObserveHeaderContextProvider", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => ({ setActiveViewConfig }),
}));
vi.mock("src/api/project/saved-views", () => ({
  useGetWorkspaceSavedViews: () => ({ data: { custom_views: [] } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../UserDetailTabBar", () => ({ default: () => null }));
vi.mock("src/sections/projects/LLMTracing/LLMTracingView", async () => {
  const { useNavigate: useStubNavigate } = await import("react-router-dom");
  const LLMTracingViewStub = ({ userIdForUserMode }) => {
    const navigate = useStubNavigate();
    return (
      <div data-testid="user-traces-view">
        <button
          type="button"
          onClick={() =>
            navigate({
              pathname: `/dashboard/users/${encodeURIComponent(
                userIdForUserMode,
              )}`,
              search: "?userTab=sessions",
            })
          }
        >
          Group by session
        </button>
      </div>
    );
  };
  return { default: LLMTracingViewStub };
});
vi.mock("src/sections/projects/SessionsView/Sessions-view", async () => {
  const { useNavigate: useStubNavigate } = await import("react-router-dom");
  const SessionsViewStub = ({ userIdForUserMode }) => {
    const navigate = useStubNavigate();
    return (
      <button
        type="button"
        onClick={() =>
          navigate({
            pathname: `/dashboard/users/${encodeURIComponent(
              userIdForUserMode,
            )}`,
            search: "?userTab=traces",
          })
        }
      >
        Group by trace
      </button>
    );
  };
  return { default: SessionsViewStub };
});

import CrossProjectUserDetailPage from "../CrossProjectUserDetailPage";

const OriginPage = ({ testId }) => {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={() => navigate("/dashboard/users/bob")}
    >
      Open user
    </button>
  );
};

const mountAt = (startUrl) => {
  // A fresh entry with no router state, so BrowserRouter numbers it idx 0.
  window.history.replaceState(null, "", startUrl);
  return render(
    <HelmetProvider>
      <Routes>
        <Route
          path="/dashboard/users"
          element={<OriginPage testId="all-users-list" />}
        />
        <Route
          path="/dashboard/users/:userId"
          element={<CrossProjectUserDetailPage />}
        />
        <Route
          path="/dashboard/observe/:observeId/users"
          element={<OriginPage testId="project-users-list" />}
        />
      </Routes>
    </HelmetProvider>,
  );
};

const currentUrl = () => `${window.location.pathname}${window.location.search}`;

const dateFilter = JSON.stringify({
  dateFilter: ["2026-10-10 00:00:00", "2026-11-09 00:00:00"],
  dateOption: "30D",
});

const projectUsersUrl = `/dashboard/observe/p1/users?${new URLSearchParams({
  userDateFilter: dateFilter,
})}`;

const savedViewUsersUrl = `/dashboard/users?${new URLSearchParams({
  usersTab: "view-abc",
  userDateFilter: dateFilter,
})}`;

const clickBack = () =>
  userEvent.click(screen.getByRole("button", { name: "Back" }));

// Opens and closes a trace drawer through the real store setter, the one the
// trace grid's row click and the drawer's onClose call. It rewrites the URL in
// place and keeps the router's history.state (key and idx) on that entry.
const openAndCloseTraceDrawer = () => {
  const { setTraceDetailDrawerOpen } = useLLMTracingStore.getState();
  act(() => setTraceDetailDrawerOpen({ traceId: "t1", projectId: "p1" }));
  act(() => setTraceDetailDrawerOpen(null));
};

describe("CrossProjectUserDetailPage — Back button", () => {
  it.each([
    {
      origin: "a project Users tab",
      startUrl: projectUsersUrl,
      listTestId: "project-users-list",
    },
    {
      origin: "the all-projects Users list on a saved view",
      startUrl: savedViewUsersUrl,
      listTestId: "all-users-list",
    },
  ])(
    "returns to $origin with its date range and view intact",
    async ({ startUrl, listTestId }) => {
      mountAt(startUrl);
      await userEvent.click(screen.getByTestId(listTestId));
      await screen.findByRole("button", { name: "Back" });
      expect(window.location.pathname).toBe("/dashboard/users/bob");

      await clickBack();

      await waitFor(() => expect(currentUrl()).toBe(startUrl));
      expect(screen.getByTestId(listTestId)).toBeInTheDocument();
      // History went back to the origin entry rather than pushing a new one.
      expect(window.history.state?.idx).toBe(0);
    },
  );

  it("leaves the page even after the views pushed their own entries", async () => {
    mountAt(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await screen.findByTestId("user-traces-view");
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=traces");

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    expect(window.history.state?.idx).toBe(0);
  });

  it("falls back to the all-projects Users list when opened from a direct link", async () => {
    mountAt("/dashboard/users/bob");
    await screen.findByRole("button", { name: "Back" });

    await clickBack();

    expect(await screen.findByTestId("all-users-list")).toBeInTheDocument();
    expect(currentUrl()).toBe("/dashboard/users");
  });

  it("leaves the page in one click after a trace drawer rewrote the URL", async () => {
    mountAt(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await screen.findByTestId("user-traces-view");
    openAndCloseTraceDrawer();
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=traces");
    // The drawer keeps the router's index on the entry it rewrote.
    expect(window.history.state?.idx).toBe(2);

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    expect(window.history.state?.idx).toBe(0);
  });

  it("returns to the list when a trace drawer was opened there first", async () => {
    mountAt(projectUsersUrl);
    openAndCloseTraceDrawer();
    expect(currentUrl()).toBe(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await screen.findByRole("button", { name: "Back" });
    // Entries the router pushes after the drawer keep a usable index.
    expect(window.history.state?.idx).toBe(1);
    const historyLength = window.history.length;

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    // History went back to the list entry rather than pushing a new one.
    expect(window.history.length).toBe(historyLength);
  });

  it("returns to the list after the browser's own Back inside the page", async () => {
    mountAt(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await screen.findByTestId("user-traces-view");

    window.history.back();
    // The sessions view comes back only once the page has rendered the pop.
    await screen.findByRole("button", { name: "Group by trace" });
    expect(currentUrl()).toBe("/dashboard/users/bob");

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    expect(window.history.state?.idx).toBe(0);
  });

  it("returns to the list in one click after the browser's Back and Forward over a drawer-rewritten entry", async () => {
    mountAt(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await screen.findByTestId("user-traces-view");
    openAndCloseTraceDrawer();

    window.history.back();
    await screen.findByRole("button", { name: "Group by trace" });
    window.history.forward();
    await screen.findByTestId("user-traces-view");
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=traces");
    // The entry the browser came forward to still carries the router's index.
    expect(window.history.state?.idx).toBe(2);

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    expect(window.history.state?.idx).toBe(0);
  });

  it("returns to the list in one click after the browser's Forward onto a drawer-rewritten entry deeper in the page", async () => {
    mountAt(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by session" }),
    );
    await screen.findByRole("button", { name: "Group by trace" });
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=sessions");
    openAndCloseTraceDrawer();

    window.history.back();
    await screen.findByTestId("user-traces-view");
    window.history.forward();
    await screen.findByRole("button", { name: "Group by trace" });
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=sessions");
    expect(window.history.state?.idx).toBe(3);

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    expect(window.history.state?.idx).toBe(0);
  });

  // OBS-SW-173: equal neighboring Trace entries (Trace, Sessions, Trace) left
  // the page unable to tell Forward from Back onto a drawer-touched entry, so
  // one click of Back stopped on an earlier Trace entry inside the page.
  it("returns to the list in one click after Back and Forward onto a drawer-touched entry between equal trace entries", async () => {
    mountAt(projectUsersUrl);
    await userEvent.click(screen.getByTestId("project-users-list"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by session" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Group by trace" }),
    );
    await screen.findByTestId("user-traces-view");
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=traces");
    openAndCloseTraceDrawer();

    window.history.back();
    await screen.findByRole("button", { name: "Group by trace" });
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=sessions");
    window.history.forward();
    await screen.findByTestId("user-traces-view");
    expect(currentUrl()).toBe("/dashboard/users/bob?userTab=traces");

    await clickBack();

    await waitFor(() => expect(currentUrl()).toBe(projectUsersUrl));
    expect(screen.getByTestId("project-users-list")).toBeInTheDocument();
    expect(window.history.state?.idx).toBe(0);
  });
});
