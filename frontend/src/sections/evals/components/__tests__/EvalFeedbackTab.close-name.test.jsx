/* eslint-disable react/prop-types */
import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  items: [
    {
      id: "feedback-1",
      value: "passed",
      explanation: "Matches the reference answer.",
      user_name: "Reviewer",
      source: "eval_playground",
      created_at: "2026-11-01T10:00:00Z",
    },
  ],
}));

vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, onRowClick }) => (
    <div>
      {data.map((row) => (
        <button key={row.id} type="button" onClick={() => onRowClick(row)}>
          {`Open feedback from ${row.user_name}`}
        </button>
      ))}
    </div>
  ),
  DataTablePagination: () => null,
}));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalCellValue: (value) => value,
}));
vi.mock("../../hooks/useEvalFeedback", () => ({
  useEvalFeedbackList: () => ({
    data: { items: mocks.items, total: mocks.items.length },
    isLoading: false,
    isFetching: false,
  }),
}));

import EvalFeedbackTab from "../EvalFeedbackTab";

describe("Feedback details panel (W10-EVALS-U02)", () => {
  it("names the button that closes the feedback details", async () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <EvalFeedbackTab templateId="template-1" />
      </QueryClientProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Open feedback from Reviewer" }),
    );
    const close = await screen.findByRole("button", {
      name: "Close feedback details",
    });
    act(() => close.focus());
    expect(close).toHaveFocus();

    fireEvent.click(close);
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Close feedback details" }),
      ).not.toBeInTheDocument(),
    );
  });
});
