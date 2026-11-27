import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

/* Which version of the agent Use with my agent adapts. */

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        { id: "a1", name: "ShopMate", modality: "text", source: "retell" },
      ],
      total: 1,
    },
    isLoading: false,
  }),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

const useHarnessReleaseFit = vi.fn(() => ({ data: undefined }));
vi.mock("src/api/harness/releases", () => ({
  useHarnessReleaseFit: (choice) => useHarnessReleaseFit(choice),
  createHarnessAdaptation: vi.fn(),
}));

const { default: axios } = await import("src/utils/axios");
const { default: ReadyEnvironmentPanel } = await import(
  "../starts/ReadyEnvironmentPanel"
);
const { readyEnvironmentFrom } = await import("../readyEnvironments");

const world = readyEnvironmentFrom({
  id: "rel-1",
  digest: "sha256:" + "ab".repeat(32),
  name: "retail",
  system: true,
  tools: [],
  policy: { parameters: [], agent_rules: [] },
  suites: [{ id: "suite-1", name: "retail_base", scenario_count: 2 }],
  catalogue: null,
});

const version = (n, extra = {}) => ({
  id: `version-${n}`,
  version_name_display: `v${n}`,
  is_active: false,
  ...extra,
});
// 25 versions, newest first, ten to a page.
const pagesOf = (active, broken = null) => {
  const all = Array.from({ length: 25 }, (_, i) =>
    version(25 - i, { is_active: 25 - i === active }),
  );
  axios.get.mockImplementation((url, { params: { page } }) =>
    page === broken
      ? Promise.reject({ message: "versions are down" })
      : Promise.resolve({
          data: {
            results: all.slice((page - 1) * 10, page * 10),
            current_page: page,
            next: page < 3 ? `?page=${page + 1}` : null,
          },
        }),
  );
};

const render = () => {
  baseRender(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReadyEnvironmentPanel item={{ world, initialTab: "start" }} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
};
const fitVersions = () => [
  ...new Set(
    useHarnessReleaseFit.mock.calls
      .map(([choice]) => choice.agentVersionId)
      .filter(Boolean),
  ),
];

beforeEach(() => {
  axios.get.mockReset();
  useHarnessReleaseFit.mockClear();
});

describe("the version adapted", () => {
  it("is the active one, however many versions came after it, and is shown", async () => {
    pagesOf(3);
    render();

    expect(await screen.findByText("v3")).toBeInTheDocument();
    expect(screen.getByText("Agent version")).toBeInTheDocument();
    expect(fitVersions()).toEqual(["version-3"]);
  });

  it("reads no more pages than it needs", async () => {
    pagesOf(24);
    render();

    expect(await screen.findByText("v24")).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("is the latest when none is active", async () => {
    pagesOf(null);
    render();

    expect(await screen.findByText("v25")).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(3);
  });

  it("isn't guessed when its page can't be read, and is found on a retry", async () => {
    pagesOf(13, 2);
    render();

    expect(
      await screen.findByText(
        "Couldn't read the agent's versions: versions are down",
      ),
    ).toBeInTheDocument();
    expect(fitVersions()).toEqual([]);

    pagesOf(13);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(fitVersions()).toEqual(["version-13"]));
  });
});
