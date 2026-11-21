import { useLayoutEffect } from "react";
import PropTypes from "prop-types";
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { readQuery } from "src/utils/axios";
import FilterValueLabel from "./FilterValueLabel";
import { useResolvedFilterOptions } from "./useResolvedFilterOptions";

vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: { dashboard: { filterValues: "/tracer/dashboard/filter_values/" } },
  readQuery: vi.fn(),
}));

const filter = { id: "reviewer", type: "annotation", value: ["reviewer-id"] };
const target = { value: "reviewer-id", label: "Lead reviewer" };
const cached = { value: "cached-id", label: "Cached reviewer" };
const cursor = "older-reviewers";
const page = (values, nextCursor = null) => ({
  data: { result: { values, has_more: !!nextCursor, next_cursor: nextCursor } },
});
const requestedCursors = () =>
  readQuery.mock.calls.map(([, { params }]) => params.cursor);
const sharedCache = () => {
  const client = new QueryClient();
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  wrapper.propTypes = { children: PropTypes.node.isRequired };
  return { wrapper, client };
};
const deferredRefetch = () => {
  let reject;
  const promise = new Promise((_, fail) => {
    reject = fail;
  });
  return { promise, reject };
};

beforeEach(() => {
  readQuery.mockReset();
});

it("resolves a saved label from the cached cursor after a failed remount refetch", async () => {
  const { wrapper, client } = sharedCache();
  const refetch = deferredRefetch();
  readQuery
    .mockResolvedValueOnce(page([], cursor))
    .mockReturnValueOnce(refetch.promise)
    .mockResolvedValueOnce(page([target]));

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

  await act(async () => refetch.reject(new Error("Refetch failed")));
  expect(await screen.findByText(target.label)).toBeVisible();
  expect(requestedCursors()).toEqual([undefined, undefined, cursor]);
  expect(
    chip.container.querySelector(".MuiSkeleton-root"),
  ).not.toBeInTheDocument();
  chip.unmount();
  client.clear();
});

// A second mounted observer of the same infinite query. Refetch through the
// client: the picker's public `refetch` is a custom fresh-page action, not an
// ordinary TanStack refetch. Each test client contains exactly this one query.
function PeerPicker({ refresh }) {
  const client = useQueryClient();
  const query = useResolvedFilterOptions(filter, "traces");
  useLayoutEffect(() => {
    if (refresh) void client.refetchQueries({ type: "active" });
  }, [client, refresh]);
  const result = query.isFetchNextPageError
    ? "continuation error"
    : query.isError
      ? "ordinary error"
      : "ok";
  return (
    <>
      <output>{`${query.fetchStatus}: ${query.continuationKey}: ${result}`}</output>
      <button type="button" onClick={() => void query.fetchNextPage()}>
        Load more
      </button>
    </>
  );
}

PeerPicker.propTypes = { refresh: PropTypes.bool.isRequired };

const pair = (id, refresh = false) => (
  <>
    <PeerPicker refresh={refresh} />
    <FilterValueLabel filter={{ ...filter, value: [id] }} source="traces" />
  </>
);

it("keeps the raw ID after an ordinary refetch replaces a continuation error at the same cursor", async () => {
  const { wrapper, client } = sharedCache();
  const refetch = deferredRefetch();
  readQuery
    .mockResolvedValueOnce(page([cached], cursor))
    .mockRejectedValueOnce(new Error("Continuation failed"))
    .mockReturnValueOnce(refetch.promise)
    .mockResolvedValueOnce(page([], "later"))
    .mockResolvedValueOnce(page([target]));

  const chip = render(pair(target.value), { wrapper });
  await screen.findByText(`idle: ${cursor}: continuation error`);
  expect(await screen.findByText(target.value)).toBeVisible();
  expect(requestedCursors()).toEqual([undefined, cursor]);

  chip.rerender(pair(target.value, true));
  await waitFor(() =>
    expect(requestedCursors()).toEqual([undefined, cursor, undefined]),
  );
  await act(async () => refetch.reject(new Error("Ordinary refetch failed")));
  await screen.findByText(`idle: ${cursor}: ordinary error`);
  expect(screen.getByText(target.value)).toBeVisible();
  expect(chip.container.querySelector(".MuiSkeleton-root")).toBeNull();
  expect(requestedCursors()).toEqual([undefined, cursor, undefined]);

  // Explicit picker recovery advances the cursor. The same mounted chip can
  // now spend its next read on "later" rather than remaining exhausted at C.
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText(target.label)).toBeVisible();
  expect(requestedCursors()).toEqual([
    undefined,
    cursor,
    undefined,
    cursor,
    "later",
  ]);
  chip.unmount();
  client.clear();
});

it("does not spend a cursor or a read joining a peer's layout-effect refetch after an idle render", async () => {
  const { wrapper, client } = sharedCache();
  const refetch = deferredRefetch();
  const cursors = Array.from({ length: 12 }, (_, i) => `cursor-${i + 1}`);
  readQuery
    .mockResolvedValueOnce(page([cached], cursors[0]))
    .mockReturnValueOnce(refetch.promise);
  cursors.forEach((_, i) => {
    const values = i === cursors.length - 1 ? [target] : [];
    readQuery.mockResolvedValueOnce(page(values, cursors[i + 1]));
  });

  const chip = render(pair(cached.value), { wrapper });
  expect(await screen.findByText(cached.label)).toBeVisible();
  await screen.findByText(`idle: ${cursors[0]}: ok`);
  // Both observers render idle. The new ID makes the chip eligible, but its
  // passive effect runs after the peer's layout effect starts page one.
  chip.rerender(pair(target.value, true));
  await screen.findByText(`fetching: ${cursors[0]}: ok`);
  expect(requestedCursors()).toEqual([undefined, undefined]);
  expect(readQuery.mock.calls[1][1].signal.aborted).toBe(false);
  expect(chip.container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();

  await act(async () => refetch.reject(new Error("Ordinary refetch failed")));
  expect(
    await screen.findByText(target.label, {}, { timeout: 5000 }),
  ).toBeVisible();
  expect(requestedCursors()).toEqual([undefined, undefined, ...cursors]);
  expect(chip.container.querySelector(".MuiSkeleton-root")).toBeNull();
  chip.unmount();
  client.clear();
});
