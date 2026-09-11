import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import AddLabelDrawer from "../AddLabelDrawer";

const {
  entitlementMessage,
  defaultQueueState,
  labelQueryState,
  mockFetchNextPage,
  mockRefetchLabels,
  mockGetOrCreate,
  mockGetOrCreateMutate,
  mockInvalidateQueries,
  mockUseInfiniteLabels,
} = vi.hoisted(() => ({
  entitlementMessage:
    "You've reached the 10 annotation queues limit across this organization.",
  defaultQueueState: { isPending: false },
  labelQueryState: {
    data: {
      results: [{ id: "label-1", name: "Review", type: "categorical" }],
    },
    isLoading: false,
    isFetchingNextPage: false,
    hasNextPage: false,
    isError: false,
  },
  mockFetchNextPage: vi.fn(),
  mockRefetchLabels: vi.fn(),
  mockGetOrCreate: vi.fn(),
  mockGetOrCreateMutate: vi.fn(),
  mockInvalidateQueries: vi.fn(),
  mockUseInfiniteLabels: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
  };
});

vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  annotationLabelKeys: { all: ["annotation-labels"] },
  useInfiniteAnnotationLabelsList: (filters) => {
    mockUseInfiniteLabels(filters);
    return {
      ...labelQueryState,
      fetchNextPage: mockFetchNextPage,
      refetch: mockRefetchLabels,
    };
  },
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  extractErrorMessage: (error, fallback) =>
    error?.response?.data?.result ||
    error?.response?.data?.error?.message ||
    fallback,
  useGetOrCreateDefaultQueue: (options) => {
    mockGetOrCreate(options);
    return {
      mutate: mockGetOrCreateMutate,
      isPending: defaultQueueState.isPending,
    };
  },
  useAddLabelToQueue: () => ({ mutateAsync: vi.fn() }),
  useRemoveLabelFromQueue: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("src/sections/annotations/labels/create-label-drawer", () => ({
  default: () => null,
}));

describe("AddLabelDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    defaultQueueState.isPending = false;
    Object.assign(labelQueryState, {
      data: {
        results: [{ id: "label-1", name: "Review", type: "categorical" }],
      },
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: false,
      isError: false,
    });
    mockGetOrCreateMutate.mockImplementation((_variables, callbacks) => {
      callbacks.onError({
        response: {
          data: {
            status: false,
            result: entitlementMessage,
            error: {
              code: "ENTITLEMENT_LIMIT",
              message: entitlementMessage,
            },
          },
        },
      });
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the exact queue entitlement once and suppresses generic UI", async () => {
    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);

    await waitFor(() => {
      expect(screen.getAllByText(entitlementMessage)).toHaveLength(1);
    });
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
    expect(mockGetOrCreate).toHaveBeenCalledWith({ notifyOnError: false });
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("does not show a false empty state while labels are loading", () => {
    Object.assign(labelQueryState, {
      data: { results: [] },
      isLoading: true,
    });

    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);

    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(screen.queryByText("No labels found")).not.toBeInTheDocument();
  });

  it("debounces rapid server searches and only submits the final value", async () => {
    vi.useFakeTimers();
    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);

    fireEvent.change(screen.getByPlaceholderText("Search labels..."), {
      target: { value: "pri" },
    });
    fireEvent.change(screen.getByPlaceholderText("Search labels..."), {
      target: { value: "prior" },
    });
    fireEvent.change(screen.getByPlaceholderText("Search labels..."), {
      target: { value: "priority" },
    });

    expect(mockUseInfiniteLabels).not.toHaveBeenCalledWith({
      search: "pri",
      limit: 50,
    });
    expect(mockUseInfiniteLabels).not.toHaveBeenCalledWith({
      search: "prior",
      limit: 50,
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    expect(mockUseInfiniteLabels).toHaveBeenLastCalledWith({
      search: "priority",
      limit: 50,
    });
  });

  it("shows a sanitized first-page error with an explicit retry", () => {
    Object.assign(labelQueryState, {
      data: { results: [] },
      isLoading: false,
      isError: true,
    });

    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);

    expect(
      screen.getByText("We couldn't load labels. Please retry."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
    expect(screen.queryByText("No labels found")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(mockRefetchLabels).toHaveBeenCalledTimes(1);
  });

  it("loads the next page when the constrained label region is scrolled", async () => {
    labelQueryState.hasNextPage = true;
    mockFetchNextPage.mockResolvedValue({});

    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);
    const scroller = screen.getByTestId("annotation-labels-scroll-region");
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 500 },
      clientHeight: { configurable: true, value: 200 },
      scrollTop: { configurable: true, value: 285 },
    });

    fireEvent.scroll(scroller);

    await waitFor(() => {
      expect(mockFetchNextPage).toHaveBeenCalledTimes(1);
    });
  });

  it("passes the debounced value to the server query", async () => {
    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);
    fireEvent.change(screen.getByPlaceholderText("Search labels..."), {
      target: { value: "priority" },
    });

    await waitFor(
      () => {
        expect(mockUseInfiniteLabels).toHaveBeenLastCalledWith({
          search: "priority",
          limit: 50,
        });
      },
      { timeout: 1_000 },
    );
  });

  it("loads another server page when more than one label page exists", () => {
    labelQueryState.hasNextPage = true;

    render(<AddLabelDrawer open onClose={vi.fn()} projectId="project-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Load more labels" }));

    expect(mockFetchNextPage).toHaveBeenCalledTimes(1);
  });
});
