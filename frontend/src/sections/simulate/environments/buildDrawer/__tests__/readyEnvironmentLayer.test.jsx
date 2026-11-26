import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  act,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

/* A ready environment's layer in the drawer: reopened after a reload, and held while it's busy. */

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

let panel;
vi.mock("../starts/ReadyEnvironmentPanel", () => ({
  default: (props) => {
    panel = props;
    return <div>{`${props.item.world.name} on ${props.item.initialTab}`}</div>;
  },
}));

const { default: BuildEnvironmentDrawer } = await import(
  "../BuildEnvironmentDrawer"
);

const release = (id, name) => ({
  id,
  digest: "sha256:" + "ab".repeat(32),
  name,
  system: true,
  tools: [],
  policy: { parameters: [], agent_rules: [] },
  suites: [{ id: `suite-${id}`, name: `${name}_base`, scenario_count: 2 }],
  catalogue: null,
});
const LOADING = { data: undefined, isLoading: true, error: null };
const LOADED = {
  data: ["retail", "clinic", "travel", "bank"].map((name) =>
    release(`rel-${name}`, name),
  ),
  isLoading: false,
  error: null,
};

const onWayChange = vi.fn();
const client = new QueryClient();
const drawer = (way) => (
  <QueryClientProvider client={client}>
    <BuildEnvironmentDrawer
      open
      onClose={vi.fn()}
      way={way}
      onWayChange={onWayChange}
    />
  </QueryClientProvider>
);
const render = (way) => {
  const view = baseRender(drawer(way));
  return () => view.rerender(drawer(way));
};

beforeEach(() => {
  onWayChange.mockReset();
  releases.mockReset();
  releases.mockReturnValue(LOADED);
});

describe("a ready environment's way", () => {
  it("reopens it on its tab once the ready environments load, and not before", () => {
    releases.mockReturnValue(LOADING);
    const update = render("world:rel-retail:start");
    expect(onWayChange).not.toHaveBeenCalled();

    releases.mockReturnValue(LOADED);
    update();

    expect(screen.getByText("Retail on start")).toBeInTheDocument();
  });

  it("is kept while they can't be read", () => {
    releases.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: { message: "releases are down" },
    });
    render("world:rel-retail:start");

    expect(onWayChange).not.toHaveBeenCalled();
  });

  it("is the catalog for one that isn't there", () => {
    render("world:rel-gone:start");

    expect(screen.queryByText(/ on start$/)).toBeNull();
    expect(screen.getByText("Build environment")).toBeInTheDocument();
    expect(onWayChange).toHaveBeenLastCalledWith("");
  });

  it("gives way to what is picked before they load", async () => {
    releases.mockReturnValue(LOADING);
    const update = render("world:rel-retail:start");
    fireEvent.click(screen.getByRole("button", { name: "Connect GitHub" }));
    await waitFor(() => expect(onWayChange).toHaveBeenLastCalledWith("github"));

    releases.mockReturnValue(LOADED);
    update();

    expect(screen.queryByText("Retail on start")).toBeNull();
    expect(screen.getByText("Paste a repo link")).toBeInTheDocument();
  });
});

describe("choosing another world", () => {
  it("leaves the list under the layer alone while it is busy", async () => {
    render("world:rel-retail:start");
    act(() => panel.onBusyChange(true));
    act(() => panel.onOtherWorld());

    expect(
      screen.getByRole("button", { name: "Back to build environment" }),
    ).toBeInTheDocument();

    act(() => panel.onBusyChange(false));
    act(() => panel.onOtherWorld());

    await waitFor(() =>
      expect(screen.queryByText("Retail on start")).toBeNull(),
    );
    expect(
      screen.getByRole("textbox", { name: "Search environments" }),
    ).toBeInTheDocument();
  });
});

describe("how many ready environments there are", () => {
  it("is the same on the catalog, in the browser's header and in its list", () => {
    render("");
    fireEvent.click(screen.getByRole("button", { name: "Browse all 4" }));

    expect(screen.getByText("Ready environments").textContent).toBe(
      "Ready environments4",
    );
    expect(screen.getByText("4 of 4 environments")).toBeInTheDocument();
    // The sandbox keeps its card; it isn't counted.
    expect(
      screen.getByRole("listitem", { name: "SaaS sandbox" }),
    ).toBeInTheDocument();
  });
});
