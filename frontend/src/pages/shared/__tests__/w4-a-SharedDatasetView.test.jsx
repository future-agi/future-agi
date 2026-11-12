// The public dataset page (W4-6, A3): only the included sections, read-only.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { sharedLinks: { resolve: (t) => `/tracer/shared/${t}/` } },
}));
const { default: SharedDatasetView } = await import("../SharedDatasetView");
const { headerParts, tabsFor, pageInfo, metricText, rangeLine } = await import(
  "../sharedDatasetModel"
);

const data = {
  name: "Support agent - golden set",
  version: { number: 6, tags: [] },
  shared_by: { name: "Maya Chen" },
  expires_at: "2026-10-29T12:00:00Z",
  columns: [
    { id: "c1", name: "customer_message", role: "input", data_type: "text" },
    { id: "c2", name: "expected_reply", role: "expected", data_type: "text" },
  ],
  rows: {
    total: 120,
    page: 1,
    page_size: 50,
    items: [
      {
        row_id: "r1",
        n: 1,
        values: {
          c1: "Hi, order A-3920 was due Monday",
          c2: "Sorry about the delay",
        },
      },
      {
        row_id: "r2",
        n: 2,
        values: { c1: "I returned the headphones", c2: null },
      },
    ],
  },
  experiments: {
    runs: [
      {
        run_id: "x",
        label: "#4A",
        name: "Support Reply V4 × gpt-4o-mini",
        status: "Completed",
        scored: 248,
        total: 248,
        metrics: [
          { key: "k", name: "Correctness", pass_rate: 0.82, avg: 0.82 },
        ],
      },
    ],
    results: {
      r1: [
        {
          run_id: "x",
          label: "#4A",
          output: "We are sorry",
          status: "completed",
          evals: [{ name: "Correctness", display: "Pass" }],
        },
      ],
      r2: [],
    },
  },
};
const renderView = (d) =>
  render(
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>
        <SharedDatasetView
          token="tok"
          shared={{ resource_type: "dataset", data: d }}
        />
      </QueryClientProvider>
    </HelmetProvider>,
  );

afterEach(cleanup);

describe("sharedDatasetModel", () => {
  it("header, tabs, paging and metrics", () => {
    expect(headerParts(data)).toEqual([
      "Support agent - golden set",
      "v6",
      "Shared by Maya Chen",
      "Read-only",
      expect.stringMatching(/^Expires \w{3} 29 Oct$/),
    ]);
    expect(tabsFor(data).map((t) => t.label)).toEqual(["Rows", "Experiments"]);
    expect(
      tabsFor({
        ...data,
        experiments: undefined,
        feedback: { by_row: {} },
      }).map((t) => t.label),
    ).toEqual(["Rows", "Feedback"]);
    expect(pageInfo(data.rows)).toEqual({
      pages: 3,
      from: 1,
      to: 50,
      total: 120,
    });
    expect(rangeLine(data.rows)).toBe("1–50 of 120 rows");
    expect(metricText({ name: "Correctness", pass_rate: 0.82 })).toBe(
      "Correctness 82%",
    );
    expect(
      metricText({ name: "Groundedness", pass_rate: null, avg: 0.714 }),
    ).toBe("Groundedness 0.71");
  });
});

describe("SharedDatasetView", () => {
  it("shows the header, rows and only the included tabs, with no edit, run or download control", () => {
    renderView(data);
    expect(screen.getByTestId("shared-dataset-header").textContent).toMatch(
      /^Support agent - golden set · v6 · Shared by Maya Chen · Read-only · Expires/,
    );
    expect(screen.getByRole("tab", { name: "Rows" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Experiments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Feedback" })).toBeNull();
    expect(screen.getByText("1–50 of 120 rows")).toBeTruthy();
    const labels = screen.getAllByRole("button").map((b) => b.textContent);
    expect(
      labels.filter((l) => /edit|run|download|copy|delete|save/i.test(l)),
    ).toEqual([]);
    fireEvent.click(screen.getByText("Hi, order A-3920 was due Monday"));
    expect(screen.getByTestId("shared-row-detail").textContent).toContain(
      "Sorry about the delay",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Experiments" }));
    expect(screen.getByText("Correctness 82%")).toBeTruthy();
    expect(screen.getByText("We are sorry")).toBeTruthy();
  });
});
