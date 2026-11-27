import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  within,
} from "src/utils/test-utils";

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
const { readyEnvironmentFrom } = await import("../readyEnvironments");

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
      />,
    );

    expect(screen.getByText(FAILED)).toBeInTheDocument();
    expect(screen.queryByText(/of \d+ environments/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
  });

  const worlds = [
    ["retail", "Retail"],
    ["appointment_booking", "Clinic front desk"],
  ].map(([name, title]) =>
    readyEnvironmentFrom({
      id: name,
      digest: "sha256:ab",
      name,
      tools: [],
      suites: [{ id: "s", name: `${name}_base`, scenario_count: 1 }],
      catalogue: { title },
    }),
  );
  const browser = () =>
    render(
      <WorldsBrowser
        worlds={worlds}
        onOpenWorld={vi.fn()}
      />,
    );
  const optionsOf = (index) => {
    fireEvent.mouseDown(screen.getAllByRole("combobox")[index]);
    return within(screen.getByRole("listbox"))
      .getAllByRole("option")
      .map((option) => option.textContent);
  };

  it("lists no sandbox among them", () => {
    browser();

    expect(screen.getByText("2 of 2 environments")).toBeInTheDocument();
    expect(optionsOf(0)).toEqual(["All types2", "Chat2"]);
  });

  it("filters by the domain's name", () => {
    browser();

    expect(optionsOf(1)).toEqual([
      "All domains",
      "Clinic front desk",
      "Retail",
    ]);
    fireEvent.click(screen.getByRole("option", { name: "Clinic front desk" }));
    expect(screen.getByText("1 of 2 environments")).toBeInTheDocument();
  });
});
