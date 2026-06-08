import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import TestExecutionCallDetail from "../TestExecutionCallDetail";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  navigate: vi.fn(),
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
  useNavigate: () => mocks.navigate,
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
    mocks.navigate.mockClear();
    mocks.search = "";
  });

  it("records voice call review for voice onboarding links", async () => {
    const user = userEvent.setup();
    mocks.search =
      "?from=onboarding&onboarding=review-voice-call&agent_definition_id=agent-1&call_id=call-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice";

    render(<TestExecutionCallDetail />);

    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.getByText("Review the voice test call")).toBeVisible();
    expect(screen.getByText("Success criteria")).toBeVisible();
    expect(screen.getByTestId("call-details")).toBeVisible();

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

    await user.click(
      screen.getByRole("button", { name: /add success criteria/i }),
    );

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/test/test-1/runs?from=onboarding&onboarding=success-criteria&agent_definition_id=agent-1&call_id=call-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
  });

  it("preserves agent review behavior for legacy onboarding links", async () => {
    mocks.search =
      "?from=onboarding&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent";

    render(<TestExecutionCallDetail />);

    expect(screen.queryByText("Voice setup")).not.toBeInTheDocument();

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
