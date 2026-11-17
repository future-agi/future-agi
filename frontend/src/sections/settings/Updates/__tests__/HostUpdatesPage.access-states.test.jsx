import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";

import HostUpdatesPage from "../HostUpdatesPage";

// FA-SET-UPDATES-01: Settings > Updates opened by a direct link, before access
// is known or where the account or deployment has no update reporting, keeps
// the page heading and layout. It never shows report data or a Refresh button
// in those states.

const mocks = vi.hoisted(() => ({ query: {} }));
vi.mock("src/api/hostUpdates/host-update-status", async (importOriginal) => ({
  ...(await importOriginal()),
  useHostUpdateStatus: () => mocks.query,
}));

beforeEach(() => {
  mocks.query = {
    allowed: true,
    pending: false,
    data: undefined,
    isLoading: false,
    isFetching: false,
    canRefresh: true,
    refresh: vi.fn(),
  };
});
afterEach(cleanup);

function renderPage() {
  return render(
    <ThemeProvider theme={createTheme()}>
      <HostUpdatesPage />
    </ThemeProvider>,
  );
}

describe("Updates page without report access", () => {
  it.each([
    ["unresolved", "Loading update reporting access…"],
    [
      "denied",
      "Host update reporting is not available to this account or deployment.",
    ],
    [
      "forbidden",
      "Host update reporting is not available to this account or deployment.",
    ],
  ])("keeps the Updates heading for %s access", (state, message) => {
    if (state === "unresolved") mocks.query.pending = true;
    if (state === "denied") mocks.query.allowed = false;
    if (state === "forbidden") mocks.query.data = { availability: "forbidden" };
    renderPage();
    expect(
      screen.getByRole("heading", { level: 1, name: "Updates" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("host-updates-summary")).toBeNull();
  });
});
