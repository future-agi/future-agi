import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {}, develop: { eval: {} } },
}));

import { buildHeatmap } from "../results/heatmap";

describe("buildHeatmap", () => {
  const at = (day, hour) => new Date(2026, 9, day, hour, 30).toISOString();
  const traces = [
    { traceId: "a", time: at(13, 9), answers: { q: "Yes" } },
    { traceId: "b", time: at(13, 9), answers: { q: "No" } },
    { traceId: "c", time: at(13, 9), answers: { q: "__error__" } },
    { traceId: "d", time: at(15, 22), answers: { q: "Yes" } },
  ];

  it("shows every day of a short window, including quiet ones", () => {
    const rows = buildHeatmap({
      traces,
      configId: "q",
      label: "Yes",
      window: [at(13, 0), at(15, 23)],
    });
    expect(rows).toHaveLength(3);
    expect(rows[1].cells.every((cell) => cell === null)).toBe(true);
  });

  it("counts matches against answered traces, not errors", () => {
    const [first] = buildHeatmap({ traces, configId: "q", label: "Yes" });
    expect(first.cells[9]).toMatchObject({
      total: 3,
      matches: 1,
      answered: 2,
      traceIds: ["a"],
    });
  });

  it("falls back to days that have traces when there is no window", () => {
    const rows = buildHeatmap({ traces, configId: "q", label: "Yes" });
    expect(rows.map((row) => row.date.getDate())).toEqual([13, 15]);
  });
});

describe("heatmap cell bounds", () => {
  it("records the first and last trace time of each cell", () => {
    const traces = [
      {
        traceId: "a",
        time: new Date(2026, 9, 13, 9, 10).toISOString(),
        answers: { q: "Yes" },
      },
      {
        traceId: "b",
        time: new Date(2026, 9, 13, 9, 50).toISOString(),
        answers: { q: "Yes" },
      },
    ];
    const cell = buildHeatmap({ traces, configId: "q", label: "Yes" })
      .flatMap((row) => row.cells)
      .find(Boolean);
    expect(cell.first).toBe(Date.parse(traces[0].time));
    expect(cell.last).toBe(Date.parse(traces[1].time));
  });
});
