import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import axios from "src/utils/axios";
import { HOSTED_AGENT_DETAIL } from "src/api/agents/_fixtures/agentDetail";
import AgentVersionsTab from "../AgentVersionsTab";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    agentDefinitions: {
      detail: (id) => `/simulate/agent-definitions/${id}/`,
      versions: (id) => `/simulate/agent-definitions/${id}/versions/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
    },
  },
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const renderTab = (search = "") =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter
        initialEntries={[
          `/dashboard/agents/connected/${HOSTED_AGENT_DETAIL.id}/versions${search}`,
        ]}
      >
        <Routes>
          <Route
            path="/dashboard/agents/connected/:agentId/versions"
            element={<AgentVersionsTab />}
          />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );

// The paginated versions list, newest first, as GET .../versions/ sends it.
const [V2, V1] = [...HOSTED_AGENT_DETAIL.versions].sort(
  (a, b) => b.version_number - a.version_number,
);
const servePages = (pages) =>
  axios.get.mockImplementation((url, config) => {
    if (url.endsWith("/versions/")) {
      const page = config.params.page;
      return Promise.resolve({
        data: {
          count: pages.flat().length,
          next: page < pages.length ? "next" : null,
          previous: null,
          total_pages: pages.length,
          current_page: page,
          results: pages[page - 1],
        },
      });
    }
    const one = [V2, V1].find((v) => url.endsWith(`/versions/${v.id}/`));
    if (one) return Promise.resolve({ data: one });
    return Promise.resolve({ data: HOSTED_AGENT_DETAIL });
  });

// jsdom has no layout, so every box measures 0×0 and looks "not scrollable".
// Tests that scroll give the versions list a real-looking size first.
const sizeOf = { scrollHeight: 0, clientHeight: 0 };
const realDescriptors = {};
const fakeListSize = ({ scrollHeight, clientHeight }) => {
  Object.assign(sizeOf, { scrollHeight, clientHeight });
};
beforeAll(() => {
  ["scrollHeight", "clientHeight"].forEach((key) => {
    realDescriptors[key] = Object.getOwnPropertyDescriptor(
      Element.prototype,
      key,
    );
    Object.defineProperty(Element.prototype, key, {
      configurable: true,
      get() {
        return this.dataset?.testid === "versions-scroll" ? sizeOf[key] : 0;
      },
    });
  });
});
afterAll(() => {
  Object.entries(realDescriptors).forEach(([key, d]) => {
    if (d) Object.defineProperty(Element.prototype, key, d);
  });
});

beforeEach(() => {
  axios.get.mockReset();
  fakeListSize({ scrollHeight: 800, clientHeight: 300 });
  servePages([[V2, V1]]);
});

describe("AgentVersionsTab", () => {
  it("reads versions from the paginated versions list", async () => {
    renderTab();
    await screen.findAllByTestId("version-entry");
    expect(axios.get).toHaveBeenCalledWith(
      `/simulate/agent-definitions/${HOSTED_AGENT_DETAIL.id}/versions/`,
      { params: { page: 1, limit: 10 } },
    );
  });

  it("marks the version the page is showing", async () => {
    renderTab("?v=v1-id");
    const [newest, oldest] = await screen.findAllByTestId("version-entry");
    await waitFor(() => expect(oldest).toHaveAttribute("aria-current", "true"));
    expect(newest).not.toHaveAttribute("aria-current");
  });

  it("keeps an off-page selected version pinned to the top", async () => {
    servePages([[V2], [V1]]);
    renderTab("?v=v1-id");
    const labels = () =>
      screen
        .getAllByTestId("version-entry")
        .map((e) => within(e).getByTestId("version-label").textContent);
    await waitFor(() => expect(labels()).toEqual(["v1", "v2"]));
    expect(screen.getAllByTestId("version-entry")[0]).toHaveAttribute(
      "aria-current",
      "true",
    );
    // The filled dot marks the version the page is showing, like a radio.
    const dots = screen.getAllByTestId("version-dot");
    expect(dots[0]).toHaveAttribute("data-selected", "true");
    expect(dots[1]).not.toHaveAttribute("data-selected");

    const list = screen.getByTestId("versions-scroll");
    Object.defineProperty(list, "scrollTop", {
      value: 500,
      configurable: true,
    });
    fireEvent.scroll(list);

    // Its page has loaded, but it stays pinned and isn't listed twice.
    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith(
        expect.stringMatching(/\/versions\/$/),
        {
          params: { page: 2, limit: 10 },
        },
      ),
    );
    await waitFor(() =>
      expect(screen.queryAllByTestId("version-skeleton")).toHaveLength(0),
    );
    expect(labels()).toEqual(["v1", "v2"]);
  });

  it("doesn't pin a selected version that's on the first page", async () => {
    servePages([[V2, V1]]);
    renderTab("?v=v1-id");
    const labels = () =>
      screen
        .getAllByTestId("version-entry")
        .map((e) => within(e).getByTestId("version-label").textContent);
    await waitFor(() => expect(labels()).toEqual(["v2", "v1"]));
  });

  it("says why the versions couldn't load and retries", async () => {
    let fail = true;
    const ok = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, config) =>
      fail && url.endsWith("/versions/")
        ? Promise.reject({ message: "Versions are unavailable." })
        : ok(url, config),
    );
    renderTab();
    expect(
      await screen.findByText("Couldn't load versions"),
    ).toBeInTheDocument();
    expect(screen.getByText("Versions are unavailable.")).toBeInTheDocument();
    expect(screen.queryByTestId("version-skeleton")).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findAllByTestId("version-entry")).toHaveLength(2);
  });

  it("selects a version from the timeline", async () => {
    renderTab();
    const [, oldest] = await screen.findAllByTestId("version-entry");
    fireEvent.click(oldest);
    await waitFor(() => expect(currentLocation.search).toBe("?v=v1-id"));
  });

  it("shows skeleton rows while the first page loads", async () => {
    axios.get.mockImplementation((url) =>
      url.endsWith("/versions/")
        ? new Promise(() => {})
        : Promise.resolve({ data: HOSTED_AGENT_DETAIL }),
    );
    renderTab();
    expect(
      (await screen.findAllByTestId("version-skeleton")).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByTestId("version-entry")).toBeNull();
    expect(screen.queryByText("No versions yet")).toBeNull();
  });

  it("adds skeleton rows below the list while the next page loads", async () => {
    let releasePage2;
    axios.get.mockImplementation((url, config) => {
      if (!url.endsWith("/versions/"))
        return Promise.resolve({ data: HOSTED_AGENT_DETAIL });
      if (config.params.page === 1) {
        return Promise.resolve({
          data: { count: 2, next: "next", current_page: 1, results: [V2] },
        });
      }
      return new Promise((resolve) => {
        releasePage2 = () =>
          resolve({
            data: { count: 2, next: null, current_page: 2, results: [V1] },
          });
      });
    });
    renderTab();
    await screen.findAllByTestId("version-entry");
    expect(screen.queryByTestId("version-skeleton")).toBeNull();

    const list = screen.getByTestId("versions-scroll");
    Object.defineProperty(list, "scrollTop", {
      value: 500,
      configurable: true,
    });
    fireEvent.scroll(list);

    expect(
      (await screen.findAllByTestId("version-skeleton")).length,
    ).toBeGreaterThan(0);
    releasePage2();
    expect(await screen.findByText("First version")).toBeInTheDocument();
    expect(screen.queryByTestId("version-skeleton")).toBeNull();
  });

  it("keeps loading pages while the list is too short to scroll", async () => {
    fakeListSize({ scrollHeight: 300, clientHeight: 300 });
    servePages([[V2], [V1]]);
    renderTab();
    expect(await screen.findByText("First version")).toBeInTheDocument();
    expect(screen.getAllByTestId("version-entry")).toHaveLength(2);
  });

  it("loads older versions when the list is scrolled to the end", async () => {
    servePages([[V2], [V1]]);
    renderTab();
    await screen.findAllByTestId("version-entry");
    expect(screen.getAllByTestId("version-entry")).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "Show older versions" }),
    ).toBeNull();

    const list = screen.getByTestId("versions-scroll");
    Object.defineProperty(list, "scrollTop", {
      value: 500,
      configurable: true,
    });
    fireEvent.scroll(list);

    expect(await screen.findByText("First version")).toBeInTheDocument();
    expect(screen.getAllByTestId("version-entry")).toHaveLength(2);
  });

  it("lists every version, newest first", async () => {
    renderTab();
    expect(await screen.findByText("2 versions")).toBeInTheDocument();
    const entries = screen.getAllByTestId("version-entry");
    expect(
      entries.map((e) => within(e).getByTestId("version-label").textContent),
    ).toEqual(["v2", "v1"]);
  });

  it("marks the active and latest version", async () => {
    renderTab();
    const [newest, oldest] = await screen.findAllByTestId("version-entry");
    expect(within(newest).getByText("Latest")).toBeInTheDocument();
    expect(within(newest).getByText("Active")).toBeInTheDocument();
    expect(within(oldest).queryByText("Latest")).toBeNull();
    expect(within(oldest).queryByText("Active")).toBeNull();
  });

  it("shows each version's commit message and date", async () => {
    renderTab();
    const [newest] = await screen.findAllByTestId("version-entry");
    expect(within(newest).getByText("Shorter greeting")).toBeInTheDocument();
    expect(within(newest).getByText(/15-10-2026/)).toBeInTheDocument();
  });

  it("has no version actions, only selection", async () => {
    renderTab();
    await screen.findByText("2 versions");
    expect(
      screen.queryByRole("button", {
        name: /make active|mark as reference|view files|switch/i,
      }),
    ).toBeNull();
  });

  it("shows Compare as a plain Coming soon card", async () => {
    renderTab();
    const region = await screen.findByRole("region", { name: "Compare" });
    expect(
      within(region).getByText(/Compare any two versions/),
    ).toBeInTheDocument();
    expect(within(region).getByLabelText("Coming soon")).toBeInTheDocument();
    expect(within(region).queryByText(/prompts\/system\.md/)).toBeNull();
  });
});
