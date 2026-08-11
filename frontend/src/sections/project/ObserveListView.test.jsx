import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

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

const minutesAgo = (n) => new Date(Date.now() - n * 60_000).toISOString();

const project = (overrides) => ({
  id: "p1",
  name: "Checkout Service",
  issues: 0,
  daily_volume: [],
  ...overrides,
});

const respondWith = (table) =>
  axiosGetMock.mockResolvedValue({
    data: { result: { table, metadata: { total_rows: table.length } } },
  });

const renderList = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ObserveListView />
    </QueryClientProvider>,
    { theme },
  );
};

const lastActiveCell = (container, id) =>
  container
    .querySelector(`[data-id="${id}"]`)
    ?.querySelector('.MuiDataGrid-cell[data-field="last_active"]');

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

describe("ObserveListView", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
  });

  describe("Last Active cell", () => {
    it("still renders the row when last_active is an unusable zero date", async () => {
      respondWith([
        project({
          name: "Zero Date Project",
          last_active: "0000-00-00 00:00:00",
        }),
      ]);

      const { container } = renderList();

      await waitFor(() =>
        expect(screen.getByText("Zero Date Project")).toBeInTheDocument(),
      );
      expect(lastActiveCell(container, "p1")).toHaveTextContent("");
    });

    it("still renders the row when last_active is unparseable text", async () => {
      respondWith([
        project({ name: "Garbage Date Project", last_active: "not-a-date" }),
      ]);

      const { container } = renderList();

      await waitFor(() =>
        expect(screen.getByText("Garbage Date Project")).toBeInTheDocument(),
      );
      expect(lastActiveCell(container, "p1")).toHaveTextContent("");
    });

    it("still renders the row when last_active is blank", async () => {
      respondWith([project({ name: "Blank Date Project", last_active: "" })]);

      const { container } = renderList();

      await waitFor(() =>
        expect(screen.getByText("Blank Date Project")).toBeInTheDocument(),
      );
      expect(lastActiveCell(container, "p1")).toHaveTextContent("");
    });

    it("still renders the row when the updated_at fallback is unparseable", async () => {
      respondWith([
        project({
          name: "Fallback Date Project",
          last_active: "",
          updated_at: "not-a-date",
        }),
      ]);

      const { container } = renderList();

      await waitFor(() =>
        expect(screen.getByText("Fallback Date Project")).toBeInTheDocument(),
      );
      expect(lastActiveCell(container, "p1")).toHaveTextContent("");
    });

    it("renders a relative label for a readable date", async () => {
      respondWith([
        project({ name: "Healthy Project", last_active: minutesAgo(2) }),
      ]);

      renderList();

      await waitFor(() =>
        expect(screen.getByText("Healthy Project")).toBeInTheDocument(),
      );
      expect(screen.getByText("2 minutes ago")).toBeInTheDocument();
    });

    it("falls back to a valid updated_at when last_active is a truthy but unparseable string", async () => {
      respondWith([
        project({
          name: "Zero Date With Valid Fallback",
          last_active: "0000-00-00 00:00:00",
          updated_at: minutesAgo(9 * 24 * 60),
        }),
      ]);

      const { container } = renderList();

      await waitFor(() =>
        expect(
          screen.getByText("Zero Date With Valid Fallback"),
        ).toBeInTheDocument(),
      );
      expect(lastActiveCell(container, "p1")).toHaveTextContent("9 days ago");
    });
  });

  describe("health colour", () => {
    it("gives an unreadable date no health dot while a fresh row still gets the active colour", async () => {
      respondWith([
        project({ id: "broken", last_active: "0000-00-00 00:00:00" }),
        project({ id: "fresh", name: "Fresh", last_active: minutesAgo(2) }),
      ]);

      const { container } = renderList();

      await waitFor(() =>
        expect(screen.getByText("Fresh")).toBeInTheDocument(),
      );
      expect(healthDotColor(lastActiveCell(container, "broken"))).toBeNull();
      expect(healthDotColor(lastActiveCell(container, "fresh"))).toBe(
        theme.palette.success.main,
      );
    });

    it("gives a stale date the disabled colour, not the active one", async () => {
      respondWith([
        project({
          id: "stale",
          name: "Stale",
          last_active: minutesAgo(60 * 24 * 5),
        }),
      ]);

      const { container } = renderList();

      await waitFor(() =>
        expect(screen.getByText("Stale")).toBeInTheDocument(),
      );
      expect(healthDotColor(lastActiveCell(container, "stale"))).toBe(
        theme.palette.text.disabled,
      );
    });
  });

  describe("failed request", () => {
    it("still says there are no projects when the request succeeds and is empty", async () => {
      respondWith([]);

      renderList();

      await waitFor(() =>
        expect(screen.getByText("No projects found")).toBeInTheDocument(),
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("says the load failed instead of claiming there are no projects", async () => {
      axiosGetMock.mockRejectedValue(
        new Error("Request failed with status 500"),
      );

      renderList();

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Request failed with status 500");
      expect(screen.queryByText("No projects found")).not.toBeInTheDocument();
    });

    it("refetches when Retry is clicked", async () => {
      axiosGetMock.mockRejectedValue(
        new Error("Request failed with status 500"),
      );

      renderList();

      await screen.findByRole("alert");
      expect(axiosGetMock).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByRole("button", { name: /retry/i }));

      await waitFor(() => expect(axiosGetMock).toHaveBeenCalledTimes(2));
    });
  });
});
