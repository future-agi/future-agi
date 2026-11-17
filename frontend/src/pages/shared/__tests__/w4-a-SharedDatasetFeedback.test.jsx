// The public page's Feedback tab shows each human label the way the grid's
// annotation cells do (stars, chips, thumbs, text), never raw JSON (W4-6).
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
const { feedbackLabel, feedbackMeta } = await import("../sharedDatasetModel");

const data = {
  name: "Support agent - golden set",
  version: { number: 6, tags: [] },
  shared_by: { name: "Maya Chen" },
  expires_at: "2026-10-29T12:00:00Z",
  columns: [{ id: "c1", name: "customer_message", data_type: "text" }],
  rows: {
    total: 2,
    page: 1,
    page_size: 50,
    items: [
      { row_id: "r1", n: 1, values: { c1: "Hi" } },
      { row_id: "r2", n: 2, values: { c1: "Hello" } },
    ],
  },
  feedback: {
    by_row: {
      r1: [
        {
          label: "Correct?",
          label_type: "thumbs_up_down",
          value: { value: "up" },
          notes: null,
          by: "Meera Iyer",
        },
        {
          label: "Quality",
          label_type: "star",
          value: { rating: 4 },
          notes: "Clear and kind",
          by: "Meera Iyer",
        },
        {
          label: "Overall preference",
          label_type: "categorical",
          value: { selected: ["A"] },
          notes: null,
          by: "Ravi Menon",
        },
      ],
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

describe("public Feedback tab", () => {
  it("words around a value", () => {
    expect(feedbackLabel({ label: "Quality" })).toBe("Quality:");
    expect(feedbackLabel({ label: null })).toBe("Label:");
    expect(feedbackLabel({ label: "Correct?" })).toBe("Correct?");
    expect(feedbackMeta({ notes: "Clear and kind", by: "Meera Iyer" })).toBe(
      "“Clear and kind” · Meera Iyer",
    );
    expect(feedbackMeta({ notes: null, by: null })).toBe("");
  });

  it("renders values like the grid's annotation cells, with no raw JSON", () => {
    renderView(data);
    fireEvent.click(screen.getByRole("tab", { name: "Feedback" }));
    const tab = screen.getByTestId("shared-feedback");
    const entries = screen.getAllByTestId("shared-feedback-entry");
    expect(entries).toHaveLength(3);
    expect(tab.textContent).not.toMatch(/[{}[\]"]/);
    expect(entries[1].textContent).toContain("Quality:");
    expect(entries[1].textContent).toContain("“Clear and kind” · Meera Iyer");
    expect(entries[2].textContent).toContain("A");
    expect(tab.textContent).toContain("Row 1");
    expect(tab.textContent).not.toContain("Row 2");
  });
});
