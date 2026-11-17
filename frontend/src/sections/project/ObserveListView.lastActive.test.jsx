import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { render, screen, waitFor } from "src/utils/test-utils";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: {
    project: { projectObserveList: "/tracer/project/list_projects/" },
  },
}));

vi.mock("./TagEditor", () => ({ default: () => <div>tags</div> }));

import ObserveListView from "./ObserveListView";

const theme = createTheme();

// list_projects reads last_active from the hourly trace rollup: it is the
// start (UTC, sent with its offset) of the hour that holds the newest trace.
const HOUR_START = "2026-11-09T14:00:00+00:00";

const respondWith = (row) =>
  axiosGetMock.mockResolvedValue({
    data: {
      status: true,
      result: {
        table: [
          {
            id: "p1",
            name: "Checkout Service",
            issues: 0,
            daily_volume: [],
            activity_query_complete: true,
            ...row,
          },
        ],
        metadata: {
          total_rows: 1,
          total_pages: 1,
          page_number: 0,
          page_size: 25,
        },
      },
    },
  });

const renderLastActiveCellAt = async (now) => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(now));
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <ObserveListView />
    </QueryClientProvider>,
    { theme },
  );
  await waitFor(() =>
    expect(screen.getByText("Checkout Service")).toBeInTheDocument(),
  );
  return container
    .querySelector('[data-id="p1"]')
    ?.querySelector('.MuiDataGrid-cell[data-field="last_active"]');
};

// jsdom's getComputedStyle doesn't resolve emotion classes, so read the
// generated rule for the health dot's own class instead.
const healthDotColor = (cell) => {
  const dot = cell?.firstElementChild?.firstElementChild;
  const cls =
    dot && Array.from(dot.classList).find((c) => c.startsWith("css-"));
  if (!cls) return null;
  const sheets = Array.from(document.querySelectorAll("style"))
    .map((s) => s.textContent)
    .join("\n");
  const rule = sheets.match(new RegExp(`\\.${cls}\\{([^}]*)\\}`));
  const bg = rule?.[1].match(/background-color:([^;]*)/);
  return bg ? bg[1].trim() : null;
};

describe("ObserveListView Last Active is an hour, not a trace time", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("says 'This hour' a minute after a trace sent late in the hour", async () => {
    respondWith({ last_active: HOUR_START });

    const cell = await renderLastActiveCellAt("2026-11-09T14:51:00Z");

    expect(cell).toHaveTextContent(/^This hour$/);
    expect(healthDotColor(cell)).toBe(theme.palette.success.main);
  });

  it("counts from the end of the hour, so a 15-minute-old trace reads as recent with the active dot", async () => {
    respondWith({ last_active: HOUR_START });

    const cell = await renderLastActiveCellAt("2026-11-09T15:05:00Z");

    expect(cell).toHaveTextContent(/^5 minutes ago$/);
    expect(healthDotColor(cell)).toBe(theme.palette.success.main);
  });

  it("measures an older hour from its end", async () => {
    respondWith({ last_active: HOUR_START });

    const cell = await renderLastActiveCellAt("2026-11-09T18:20:00Z");

    expect(cell).toHaveTextContent(/^about 3 hours ago$/);
    expect(healthDotColor(cell)).toBe(theme.palette.warning.main);
  });

  it("keeps the exact updated_at time when the project has no activity yet", async () => {
    respondWith({ last_active: null, updated_at: "2026-11-09T14:40:00Z" });

    const cell = await renderLastActiveCellAt("2026-11-09T14:51:00Z");

    expect(cell).toHaveTextContent(/^11 minutes ago$/);
    expect(healthDotColor(cell)).toBe(theme.palette.success.main);
  });
});
