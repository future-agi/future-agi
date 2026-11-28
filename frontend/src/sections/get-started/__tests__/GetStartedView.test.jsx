/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The setup-status request; each test sets its response.
const getTabs = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getTabs(...args) },
  endpoints: { getStarted: { getTabs: "/accounts/first-checks/" } },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: {} }) }));
const trackEvent = vi.fn();
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: (...args) => trackEvent(...args),
  Events: { demoVideoPageLoaded: "demo_video_page_loaded" },
  PropertyName: { status: "status" },
}));
// URL tab state, kept in React state the way the real hook keeps it in the URL.
const tabWrites = vi.fn();
vi.mock("src/routes/hooks", async () => {
  const React = await import("react");
  return {
    useSearchParams: (defaults) => {
      const [value, setValue] = React.useState(defaults);
      return [
        value,
        (next) => {
          tabWrites(next);
          setValue((previous) => ({ ...previous, ...next }));
        },
      ];
    },
  };
});
let pickTab = null;
vi.mock("../setup-steps/SetupStepsView", () => ({
  default: ({ currentLabel, setCurrentLabel }) => {
    pickTab = setCurrentLabel;
    return <div data-testid="setup-steps">tab:{currentLabel}</div>;
  },
}));
vi.mock("../GetStartedHeaderView", () => ({ default: () => <div /> }));
vi.mock("../try-out-features/TryOutFeatures", () => ({ default: () => <div /> }));

import GetStartedView from "../GetStartedView";

const status = (overrides = {}) => ({
  data: {
    result: {
      keys: false,
      dataset: false,
      evaluation: false,
      experiment: false,
      observe: false,
      invite: false,
      ...overrides,
    },
  },
});

function renderView(client) {
  return render(
    <QueryClientProvider client={client}>
      <GetStartedView />
    </QueryClientProvider>,
  );
}

describe("GetStartedView setup steps", () => {
  beforeEach(() => {
    getTabs.mockReset();
    trackEvent.mockReset();
    tabWrites.mockReset();
    pickTab = null;
    sessionStorage.clear();
  });

  it("renders steps, not an error, when the status is already cached", async () => {
    // Cached data makes React Query run the selector during the first render,
    // which used to read the tab setter before it was defined.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["currentLabel"], status({ keys: true }));
    getTabs.mockResolvedValue(status({ keys: true }));
    renderView(client);
    expect(await screen.findByTestId("setup-steps")).toHaveTextContent("tab:createFirstDataset");
    expect(screen.queryByText("Could not load your setup steps")).toBeNull();
  });

  it("opens the first incomplete step once the status loads", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    getTabs.mockResolvedValue(status({ keys: true, dataset: true }));
    renderView(client);
    expect(await screen.findByTestId("setup-steps")).toHaveTextContent("tab:createFirstEvaluation");
    expect(screen.queryByText("Could not load your setup steps")).toBeNull();
  });

  it("keeps a tab the user picks when the view re-renders", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    getTabs.mockResolvedValue(status());
    const view = renderView(client);
    expect(await screen.findByTestId("setup-steps")).toHaveTextContent("tab:addKeys");
    act(() => pickTab("inviteTeamMembers"));
    view.rerender(
      <QueryClientProvider client={client}>
        <GetStartedView />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("setup-steps")).toHaveTextContent("tab:inviteTeamMembers");
  });

  it("announces the page once and offers the demo only to a new account", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    getTabs.mockResolvedValue(status());
    const view = renderView(client);
    await screen.findByTestId("setup-steps");
    view.rerender(
      <QueryClientProvider client={client}>
        <GetStartedView />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(trackEvent).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem("videoShows")).toBe("yes");
  });

  it("still shows the error state when the request fails", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    getTabs.mockRejectedValue(new Error("network down"));
    renderView(client);
    expect(await screen.findByText("Could not load your setup steps")).toBeInTheDocument();
  });
});
