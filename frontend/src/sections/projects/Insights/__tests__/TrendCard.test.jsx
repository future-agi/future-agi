import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import TrendCard from "../TrendCard";
import { buildSegments, runTops } from "../insightsDistribution";

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
    taxonomy_mode: "discover",
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
    taxonomy_mode: "classify",
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
    taxonomy_mode: "discover",
    created_at: "2026-10-28T05:28:00Z",
    finished_at: "2026-10-28T05:31:00Z",
    analysed_count: 12,
    distribution: [
      top("a", "Tool failure reported as success", 9, 12),
      top("b", "Forgot password loops", 3, 12),
    ],
  },
];

const renderCard = (runId, list = runs) =>
  render(
    <ThemeProvider theme={theme}>
      <TrendCard
        runs={list}
        runId={runId}
        order={["a", "b", "c", "d"]}
        onSelectRun={() => {}}
        onCategory={() => {}}
      />
    </ThemeProvider>,
  );

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

describe("TrendCard chart", () => {
  it("breaks the chart where a run found its categories afresh", () => {
    const { container } = renderCard("run-3");
    const breaks = container.querySelectorAll("[data-break]");
    expect([...breaks].map((line) => line.dataset.break)).toEqual(["run-3"]);
    expect(breaks[0]).toHaveAttribute("stroke-dasharray", "4 3");
    expect(
      screen.getByText("Categories were found afresh at the dashed line"),
    ).toBeInTheDocument();
  });

  it("labels runs minutes apart without overlapping, the open one kept", () => {
    const close = runs.map((run) =>
      run.id === "run-2"
        ? {
            ...run,
            created_at: "2026-10-28T06:18:00Z",
            finished_at: "2026-10-28T06:18:30Z",
          }
        : run,
    );
    const { container } = renderCard("run-2", close);
    const labels = [...container.querySelectorAll("svg text")]
      .filter((text) => !text.textContent.endsWith("%"))
      .map((text) => text.textContent);
    // 6:18 and 6:19 sit a few pixels apart: only the open run's is shown.
    expect(labels).toHaveLength(2);
    expect(labels[1]).toBe(
      new Date("2026-10-28T06:18:30Z").toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
      }),
    );
  });
});

describe("TrendCard with one completed run", () => {
  const single = (list) =>
    render(
      <ThemeProvider theme={theme}>
        <TrendCard
          runs={list}
          runId="run-1"
          order={["a", "b"]}
          currentSegments={buildSegments(runTops(runs[2]), {
            order: ["a", "b"],
          })}
          onSelectRun={() => {}}
          onRunNow={() => {}}
        />
      </ThemeProvider>,
    );

  it("says so of a report's only run", () => {
    single([runs[2]]);
    expect(screen.getByText(/^One run so far\./)).toBeInTheDocument();
  });

  it("doesn't call it the only run beside a pager counting a stopped one", () => {
    const stopped = {
      id: "run-2",
      status: "failed",
      error_code: "stopped",
      created_at: "2026-10-28T06:08:00Z",
      finished_at: "2026-10-28T06:09:00Z",
      analysed_count: 8,
      distribution: [],
    };
    single([stopped, runs[2]]);
    expect(
      screen.getByText(/^Only one of 2 runs completed so far\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/One run so far/)).toBeNull();
    expect(screen.getByText(/Run 1 of 2/)).toBeInTheDocument();
  });
});
