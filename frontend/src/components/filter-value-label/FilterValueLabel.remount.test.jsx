import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { act, render, renderHook, screen, waitFor } from "src/utils/test-utils";
import { readQuery } from "src/utils/axios";
import FilterValueLabel from "./FilterValueLabel";
import { useResolvedFilterOptions } from "./useResolvedFilterOptions";

vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: { dashboard: { filterValues: "/tracer/dashboard/filter_values/" } },
  readQuery: vi.fn(),
}));

it("resolves a saved label from the cached cursor after a failed remount refetch", async () => {
  const client = new QueryClient();
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const filter = {
    id: "reviewer",
    type: "annotation",
    value: ["reviewer-id"],
  };
  const cursor = "older-reviewers";
  let rejectRefetch;
  readQuery
    .mockResolvedValueOnce({
      data: { result: { values: [], has_more: true, next_cursor: cursor } },
    })
    .mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectRefetch = reject;
        }),
    )
    .mockResolvedValueOnce({
      data: {
        result: {
          values: [{ value: "reviewer-id", label: "Lead reviewer" }],
          has_more: false,
          next_cursor: null,
        },
      },
    });
  const requestedCursors = () =>
    readQuery.mock.calls.map(([, { params }]) => params.cursor);

  // A picker leaves an empty but pageable result in the shared query cache.
  const picker = renderHook(() => useResolvedFilterOptions(filter, "traces"), {
    wrapper,
  });
  await waitFor(() =>
    expect(picker.result.current.continuationKey).toBe(cursor),
  );
  picker.unmount();

  const chip = render(<FilterValueLabel filter={filter} source="traces" />, {
    wrapper,
  });
  await waitFor(() =>
    expect(requestedCursors()).toEqual([undefined, undefined]),
  );
  expect(chip.container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();

  await act(async () => {
    rejectRefetch(new Error("Refetch failed"));
  });
  expect(await screen.findByText("Lead reviewer")).toBeVisible();
  expect(requestedCursors()).toEqual([undefined, undefined, cursor]);
  expect(
    chip.container.querySelector(".MuiSkeleton-root"),
  ).not.toBeInTheDocument();
  chip.unmount();
  client.clear();
});
