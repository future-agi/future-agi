import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import { MockAgGrid, fixedHeaders, renderPage } from "./__tests__/helpers";
import FeedbackSummaryCard from "./FeedbackSummaryCard";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get } };
});
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

describe("Guardrails > Feedback", () => {
  // A block body: a function returned from beforeEach runs as its cleanup.
  beforeEach(() => {
    get.mockReset();
  });

  it("lists reviews per check in a grid, with accuracy from the counts", async () => {
    get.mockResolvedValue({
      data: {
        result: [
          {
            check_name: "pii-detection",
            total_feedback: 4,
            correct_count: 2,
            false_positive_count: 1,
            false_negative_count: 1,
            unsure_count: 0,
          },
        ],
      },
    });
    renderPage(<FeedbackSummaryCard />);
    const row = await screen.findByTestId("row-pii-detection");
    expect(within(row).getByText("PII Detection")).toBeInTheDocument();
    expect(within(row).getByText("4")).toBeInTheDocument();
    expect(within(row).getByText("2")).toBeInTheDocument();
    expect(within(row).getAllByText("1")).toHaveLength(2);
    expect(within(row).getByText("50%")).toBeInTheDocument();
  });

  it("gives every number column the width its header needs, so none is cut off", async () => {
    get.mockResolvedValue({
      data: { result: [{ check_name: "pii-detection", total_feedback: 1 }] },
    });
    renderPage(<FeedbackSummaryCard />);
    await screen.findByTestId("row-pii-detection");
    const headers = fixedHeaders(screen.getByTestId("ag-grid"));
    expect(headers.map((h) => h.label)).toContain("False negative");
    headers.forEach((header) =>
      expect(header.width, header.label).toBeGreaterThanOrEqual(header.needs),
    );
  });

  it("has one empty state that says where feedback comes from", async () => {
    get.mockResolvedValue({ data: { result: [] } });
    renderPage(<FeedbackSummaryCard />);
    expect(await screen.findByText("No feedback recorded")).toBeInTheDocument();
  });

  it("has an error state that is not an empty one", async () => {
    // The axios interceptor rejects with a plain object, not an Error.
    get.mockImplementation(() =>
      Promise.reject({ message: "Service unavailable", statusCode: 503 }),
    );
    renderPage(<FeedbackSummaryCard />);
    expect(
      await screen.findByText("Feedback could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No feedback recorded")).toBeNull();
  });
});
