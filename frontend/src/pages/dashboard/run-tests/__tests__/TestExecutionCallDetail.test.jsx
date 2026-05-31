import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "src/utils/test-utils";
import TestExecutionCallDetail from "../TestExecutionCallDetail";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  search: "",
}));

vi.mock("react-router-dom", () => ({
  useParams: () => ({
    testId: "test-1",
    executionId: "execution-1",
  }),
  useLocation: () => ({
    search: mocks.search,
  }),
  BrowserRouter: ({ children }) => children,
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }) => children,
}));

vi.mock("src/sections/test-detail/CallDetails", () => ({
  default: () => <div data-testid="call-details" />,
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.mutate,
  }),
}));

describe("TestExecutionCallDetail", () => {
  beforeEach(() => {
    mocks.mutate.mockClear();
    mocks.search = "";
  });

  it("records voice call review for voice onboarding links", async () => {
    mocks.search =
      "?from=onboarding&onboarding=review-voice-call&call_id=call-1";

    render(<TestExecutionCallDetail />);

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "voice_call_reviewed",
          primaryPath: "voice",
          stage: "review_voice_call",
          artifactType: "voice_call",
          artifactId: "call-1",
        }),
      ),
    );
  });

  it("preserves agent review behavior for legacy onboarding links", async () => {
    mocks.search =
      "?from=onboarding&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent";

    render(<TestExecutionCallDetail />);

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "agent_trace_reviewed",
          primaryPath: "agent",
          stage: "review_agent_trace",
          artifactType: "test_execution",
          artifactId: "execution-1",
          quick_start_goal: "build_ai_agent",
          quick_start_id: "agent",
          quick_start_primary_path: "agent",
        }),
      ),
    );
  });
});
