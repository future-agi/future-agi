import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import usePinnedVersions from "../usePinnedVersions";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    agentDefinitions: {
      versions: (id) => `/simulate/agent-definitions/${id}/versions/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
    },
  },
}));

const V = (n) => ({ id: `v${n}`, version_number: n });
const PAGES = [[V(3), V(2)], [V(1)]];
const page = (n) => ({
  count: 3,
  next: n < PAGES.length ? "next" : null,
  previous: null,
  total_pages: PAGES.length,
  current_page: n,
  results: PAGES[n - 1],
});

beforeEach(() => {
  axios.get.mockReset();
  axios.get.mockImplementation((url, config) =>
    Promise.resolve({
      data: url.endsWith("/versions/")
        ? page(config.params.page)
        : V(Number(url.match(/versions\/v(\d+)\//)[1])),
    }),
  );
});

const wrapperFor = (client) => {
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};
const newClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });
const ids = (r) => r.current.versions.map((v) => v.id);

describe("usePinnedVersions", () => {
  it("pins the same way in a view opened after another scrolled", async () => {
    const client = newClient();
    const wrapper = wrapperFor(client);
    const menu = renderHook(() => usePinnedVersions("a1", "v1"), { wrapper });
    await waitFor(() => expect(ids(menu.result)).toEqual(["v1", "v3", "v2"]));
    await act(() => menu.result.current.fetchNextPage());
    await waitFor(() =>
      expect(menu.result.current.isFetchingNextPage).toBe(false),
    );
    expect(ids(menu.result)).toEqual(["v1", "v3", "v2"]);

    const timeline = renderHook(() => usePinnedVersions("a1", "v1"), {
      wrapper,
    });
    await waitFor(() => expect(timeline.result.current.selected).toBeTruthy());
    expect(ids(timeline.result)).toEqual(["v1", "v3", "v2"]);
  });

  it("is pinned from the first render when the pages are cached", async () => {
    const client = newClient();
    client.setQueryData(["agent-definition-versions", "a1"], {
      pages: [page(1)],
      pageParams: [1],
    });
    client.setQueryData(["agents", "version", "a1", "v1"], V(1));
    const firsts = [];
    renderHook(
      () => {
        const r = usePinnedVersions("a1", "v1");
        firsts.push(r.versions[0]?.id);
        return r;
      },
      { wrapper: wrapperFor(client) },
    );
    expect(firsts.every((id) => id === "v1")).toBe(true);
  });

  it("doesn't pin a version that's on the first page", async () => {
    const { result } = renderHook(() => usePinnedVersions("a1", "v2"), {
      wrapper: wrapperFor(newClient()),
    });
    await waitFor(() => expect(ids(result)).toEqual(["v3", "v2"]));
  });
});
