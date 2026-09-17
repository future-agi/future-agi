import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import ExperimentDetailDrawer from "./ExperimentDetailDrawer";

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: {
    get: axiosGetMock,
    post: axiosPostMock,
  },
  endpoints: {
    develop: {
      experiment: {
        rowDetail: (experimentId, rowId) =>
          `/experiments/${experimentId}/${rowId}/`,
      },
      getRowsDiff: "/rows-diff/",
    },
  },
}));

vi.mock("react-router", () => ({
  useParams: () => ({ experimentId: "experiment-1" }),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("./ExperimentDetailDrawerContent", () => ({
  default: ({ row, handleFetchNextRow }) => (
    <div>
      <div>Current row: {row.rowId}</div>
      <button type="button" onClick={handleFetchNextRow}>
        Next row
      </button>
    </div>
  ),
}));

const row1 = "00000000-0000-4000-8000-000000000001";
const row2 = "00000000-0000-4000-8000-000000000002";
const row3 = "00000000-0000-4000-8000-000000000003";

const renderDrawer = (props = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ExperimentDetailDrawer
        open
        onClose={vi.fn()}
        row={{ rowId: row1, index: 0 }}
        setExpandRow={vi.fn()}
        allRows={[
          { rowIndex: 0, data: { rowId: row1, value: "one" } },
          { rowIndex: 1, data: { rowId: row2, value: "two" } },
        ]}
        setAllRows={vi.fn()}
        totalCount={3}
        columnConfig={[]}
        refreshGrid={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
};

describe("ExperimentDetailDrawer legacy row continuation", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosPostMock.mockReset();
  });

  it("keeps the current row on failure and retries the same bounded point read", async () => {
    axiosGetMock
      .mockRejectedValueOnce(new Error("row lookup failed"))
      .mockResolvedValueOnce({
        data: {
          status: true,
          result: {
            column_config: [],
            table: [{ row_id: row2 }],
            next_row_ids: [row3],
          },
        },
      });

    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Next row" }));

    expect(
      await screen.findByText(/The current row is still shown/i),
    ).toBeInTheDocument();
    expect(screen.getByText(`Current row: ${row1}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(axiosGetMock).toHaveBeenCalledTimes(2));
    for (const call of axiosGetMock.mock.calls) {
      expect(call[0]).toBe(`/experiments/experiment-1/${row2}/`);
      expect(call[1]).toMatchObject({
        timeout: INTERACTIVE_REQUEST_TIMEOUT_MS,
      });
      expect(call[1].signal).toBeInstanceOf(AbortSignal);
    }
    await waitFor(() =>
      expect(
        screen.queryByText(/The current row is still shown/i),
      ).not.toBeInTheDocument(),
    );
  });
});
