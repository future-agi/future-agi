import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import TrendCard from "../TrendCard";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });

const top = (id, name, count, total) => ({
  category_id: id,
  name,
  count,
  share: count / total,
  status: "active",
});

// Two runs sorted into the saved categories (the second found nothing of
// "Forgot password"), then one that found its categories afresh.
const runs = [
  {
    id: "run-3",
    status: "completed",
    created_at: "2026-10-28T06:17:00Z",
    finished_at: "2026-10-28T06:19:00Z",
    analysed_count: 9,
    distribution: [
      top("c", "Agent reports success after a tool call failed", 5, 9),
      top("d", "Agent repeats the same advice", 4, 9),
    ],
  },
  {
    id: "run-2",
    status: "completed",
    created_at: "2026-10-28T06:08:00Z",
    finished_at: "2026-10-28T06:09:00Z",
    analysed_count: 12,
    distribution: [
      top("a", "Tool failure reported as success", 12, 12),
      top("b", "Forgot password loops", 0, 12),
    ],
  },
  {
    id: "run-1",
    status: "completed",
    created_at: "2026-10-28T05:28:00Z",
    finished_at: "2026-10-28T05:31:00Z",
    analysed_count: 12,
    distribution: [
      top("a", "Tool failure reported as success", 9, 12),
      top("b", "Forgot password loops", 3, 12),
    ],
  },
];

const legend = (runId) => {
  render(
    <ThemeProvider theme={theme}>
      <TrendCard
        runs={runs}
        runId={runId}
        order={["a", "b", "c", "d"]}
        onSelectRun={() => {}}
        onCategory={() => {}}
      />
    </ThemeProvider>,
  );
  return within(screen.getByRole("list"))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
};

describe("TrendCard legend", () => {
  it("doesn't read a rediscovered run's earlier categories as 0%", () => {
    expect(legend("run-3")).toEqual([
      "Agent reports success after a tool call failed56%",
      "Agent repeats the same advice44%",
      "Tool failure reported as successnot in this run",
      "Forgot password loopsnot in this run",
    ]);
  });

  it("keeps 0% for a category the run sorted into and found nothing of", () => {
    expect(legend("run-2")).toEqual([
      "Tool failure reported as success100%",
      "Forgot password loops0%",
      "Agent reports success after a tool call failednot in this run",
      "Agent repeats the same advicenot in this run",
    ]);
  });

  it("offers only this run's categories to open", () => {
    legend("run-3");
    expect(
      screen.getByRole("button", { name: "Agent repeats the same advice" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Forgot password loops" }),
    ).toBeNull();
  });
});
