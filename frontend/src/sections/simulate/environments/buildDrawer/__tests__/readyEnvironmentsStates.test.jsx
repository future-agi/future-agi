import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render as baseRender, fireEvent, screen } from "src/utils/test-utils";

/* The ready environments while they load, and when they can't be read. */

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: { items: [], total: 0 },
    isLoading: false,
  }),
}));

const releases = vi.fn();
vi.mock("src/api/harness/releases", () => ({
  useHarnessReleases: () => releases(),
  createHarnessAdaptation: vi.fn(),
  useHarnessReleaseFit: () => ({}),
}));

const { default: DrawerCatalog } = await import("../DrawerCatalog");
const { default: WorldsBrowser } = await import("../starts/WorldsBrowser");

const NONE = "No ready environments are published to this workspace yet.";
const FAILED = "Couldn't load the ready environments";
const render = (ui) =>
  baseRender(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
const catalog = () =>
  render(<DrawerCatalog onPick={vi.fn()} onBrowseWorlds={vi.fn()} />);

beforeEach(() => releases.mockReset());

describe("the catalog's ready environments", () => {
  it("load, rather than reading as none", () => {
    releases.mockReturnValue({ data: undefined, isLoading: true, error: null });
    catalog();

    expect(
      screen.getByRole("progressbar", { name: "Loading ready environments" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(NONE)).toBeNull();
  });

  it("say they couldn't be read, with a retry", () => {
    const refetch = vi.fn();
    releases.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: { message: "releases are down" },
      refetch,
    });
    catalog();

    expect(screen.getByText(FAILED)).toBeInTheDocument();
    expect(screen.getByText("releases are down")).toBeInTheDocument();
    expect(screen.queryByText(NONE)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("are none when there are none", () => {
    releases.mockReturnValue({ data: [], isLoading: false, error: null });
    catalog();

    expect(screen.getByText(NONE)).toBeInTheDocument();
  });

  it("keep a list read before when a later read fails", () => {
    releases.mockReturnValue({
      data: [],
      isLoading: false,
      error: { message: "releases are down" },
    });
    catalog();

    expect(screen.queryByText(FAILED)).toBeNull();
  });
});

describe("the ready environments browser", () => {
  it("says they couldn't be read, with a retry and no count", () => {
    const onRetry = vi.fn();
    render(
      <WorldsBrowser
        worlds={[]}
        error={{ message: "releases are down" }}
        onRetry={onRetry}
        onOpenWorld={vi.fn()}
        onOpenSandbox={vi.fn()}
      />,
    );

    expect(screen.getByText(FAILED)).toBeInTheDocument();
    expect(screen.queryByText(/of 1 environments/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
