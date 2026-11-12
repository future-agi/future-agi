// OBS-SW-104: Get started opens the first unfinished step once, when this
// visit's checks load. A later refetch of the checks (the app refetches when
// the user comes back to the tab) must leave the user on the step they chose.
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import GetStartedView from "../GetStartedView";

const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  resetUser: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));
vi.mock("../GetStartedHeaderView", () => ({ default: () => null }));
vi.mock("../try-out-features/TryOutFeatures", () => ({ default: () => null }));
// The step list and the step content are not under test: show which step is
// open, and let the test pick one the way the left list does.
vi.mock("../setup-steps/SetupStepsView", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ currentLabel, setCurrentLabel }) => (
    <div>
      <div data-testid="open-step">{currentLabel}</div>
      <button
        type="button"
        onClick={() => setCurrentLabel("SetupObsabilityInApplication")}
      >
        Setup observability in application
      </button>
    </div>
  ),
}));

// axios puts a new XMLHttpRequest on every response, so a refetch never
// returns an object deep-equal to the last one and the query's data changes.
class FakeRequest {}

const NO_DATASET_YET = {
  keys: true,
  dataset: false,
  evaluation: false,
  experiment: false,
  observe: false,
  invite: false,
};

let checks;
let client;

const response = (result) => ({
  data: { status: true, result: { ...result } },
  status: 200,
  request: new FakeRequest(),
});

const renderView = (seed) => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  seed?.(client);
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <GetStartedView />
    </QueryClientProvider>,
    { route: "/dashboard/get-started" },
  );
};

const openStep = () => screen.getByTestId("open-step").textContent;
const tabInUrl = () => new URLSearchParams(window.location.search).get("tab");
const checkUpdates = () =>
  client.getQueryState(["currentLabel"])?.dataUpdateCount ?? 0;

beforeEach(() => {
  checks = { ...NO_DATASET_YET };
  http.get.mockReset();
  http.get.mockImplementation(() => Promise.resolve(response(checks)));
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  focusManager.setFocused(undefined);
});

describe("Get started step choice", () => {
  it("opens the first unfinished step when the checks load", async () => {
    renderView();

    await waitFor(() => expect(openStep()).toBe("createFirstDataset"));
    expect(tabInUrl()).toBe("createFirstDataset");
  });

  it("keeps the chosen step when the user comes back to the tab", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    renderView();
    await waitFor(() => expect(openStep()).toBe("createFirstDataset"));

    fireEvent.click(screen.getByText("Setup observability in application"));
    await waitFor(() =>
      expect(openStep()).toBe("SetupObsabilityInApplication"),
    );

    // Open the docs in another tab, and come back once the checks are stale.
    act(() => {
      focusManager.setFocused(false);
    });
    vi.setSystemTime(Date.now() + 10 * 1000);
    act(() => {
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(checkUpdates()).toBe(2));
    await act(async () => {});

    expect(http.get).toHaveBeenCalledTimes(2);
    expect(openStep()).toBe("SetupObsabilityInApplication");
    expect(tabInUrl()).toBe("SetupObsabilityInApplication");
  });

  it("opens the next step by fresh checks, not cached ones", async () => {
    // The last visit cached checks without a dataset; the user made one since.
    const cached = response(NO_DATASET_YET);
    checks = { ...NO_DATASET_YET, dataset: true };
    renderView((queryClient) =>
      queryClient.setQueryData(["currentLabel"], cached, {
        updatedAt: Date.now() - 30 * 1000,
      }),
    );

    await waitFor(() => expect(checkUpdates()).toBe(2));
    await waitFor(() => expect(openStep()).toBe("createFirstEvaluation"));
    expect(tabInUrl()).toBe("createFirstEvaluation");
  });
});
