/* eslint-disable react/prop-types */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import CallLogsView from "./CallLogsView";

const mocks = vi.hoisted(() => ({
  fetchNextPage: vi.fn(),
  navigate: vi.fn(),
  recordActivationEvent: vi.fn(),
}));

vi.mock("react-router", () => ({
  useLocation: () => ({
    search:
      "?from=onboarding&onboarding=monitor-calls&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
  }),
  useNavigate: () => mocks.navigate,
  useParams: () => ({ testId: "test-1" }),
}));

vi.mock("@tanstack/react-query", () => ({
  useInfiniteQuery: () => ({
    data: {
      pages: [
        {
          data: {
            results: [{ id: "call-1", status: "completed" }],
          },
        },
      ],
    },
    fetchNextPage: mocks.fetchNextPage,
    isFetchingNextPage: false,
    isPending: false,
  }),
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.recordActivationEvent,
  }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "admin" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: {
    CREATE: "CREATE",
  },
  RolePermission: {
    SIMULATION_AGENT: {
      CREATE: {
        admin: true,
      },
    },
  },
}));

vi.mock("../../../hooks/use-scroll-end", () => ({
  useScrollEnd: () => vi.fn(),
}));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));

vi.mock("../../../utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    runTests: {
      callExecutionsByTestRunId: () => "/call-logs",
    },
  },
}));

vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({
    AudioPlaybackProvider: ({ children }) => <>{children}</>,
  }),
);

vi.mock("./CallLogsHeader", () => ({
  default: () => <div>Call logs header</div>,
}));

vi.mock("./CallLogsCard", () => ({
  default: ({ log }) => <div>Call log {log.id}</div>,
}));

vi.mock("src/components/show", () => ({
  ShowComponent: ({ children, condition }) => (condition ? children : null),
}));

vi.mock("src/components/EmptyLayout/EmptyLayout", () => ({
  default: ({ title }) => <div>{title}</div>,
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

describe("CallLogsView voice onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a voice monitoring focus panel and records monitor activation", async () => {
    render(<CallLogsView />);

    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.getByText("Monitor voice calls")).toBeVisible();
    expect(screen.getByText("Call log call-1")).toBeVisible();

    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "voice_call_monitor_opened",
          primaryPath: "voice",
          stage: "voice_monitor_calls",
        }),
      ),
    );

    await userEvent.click(
      screen.getByRole("button", { name: /run another test call/i }),
    );

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/test/test-1/runs?from=onboarding&onboarding=monitor-calls&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
  });
});
