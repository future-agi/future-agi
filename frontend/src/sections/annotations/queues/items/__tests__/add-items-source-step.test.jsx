import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import AddItemsDialog from "../add-items-dialog";

const harness = vi.hoisted(() => ({
  onClose: vi.fn(),
  queryClient: { fetchQuery: vi.fn() },
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
  }),
);

vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => harness.queryClient,
  useQuery: () => ({ data: undefined, isLoading: false, isFetching: false }),
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));
vi.mock(
  "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider",
  () => ({ default: ({ children }) => children }),
);
/* eslint-enable react/prop-types */

afterEach(() => {
  cleanup();
  harness.onClose.mockReset();
});

const renderDialog = () =>
  render(
    <AddItemsDialog open queueId="queue-sources" onClose={harness.onClose} />,
  );

const docsLink = () => screen.getByRole("link", { name: /docs/i });

// W10-ANNOTATIONS-U07: the first step of Add Items is reached from an existing
// queue, so it must not talk about a "first annotation project", it must name
// every source it lists, and its docs link must go somewhere.
describe("W10-ANNOTATIONS-U07 Add Items source step copy", () => {
  it("titles the step for adding items to this queue", () => {
    renderDialog();

    expect(
      screen.getByRole("heading", { name: "Add items to this queue" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/first annotation project/i),
    ).not.toBeInTheDocument();
  });

  it("names every source the step offers", () => {
    renderDialog();
    const description = docsLink().parentElement.textContent.toLowerCase();

    for (const source of [
      "datasets",
      "traces",
      "spans",
      "sessions",
      "simulations",
    ]) {
      expect(description).toContain(source);
    }
    for (const label of [
      /from datasets/i,
      /from traces/i,
      /from spans/i,
      /from sessions/i,
      /from simulation/i,
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
    }
  });

  it("links to the annotation docs in a new tab", () => {
    renderDialog();
    const link = docsLink();

    expect(link).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/annotations",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });
});
