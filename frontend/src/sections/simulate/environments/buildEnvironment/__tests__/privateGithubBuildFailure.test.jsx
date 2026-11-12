import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { jobToBuildProgress } from "../buildPipeline.constants";

// The hero animation and the timeline have their own tests. Here the timeline
// prints only what the failed step says, so the words a failure carries can be
// read without its icons or timers.
vi.mock("../building/DerivingAnimation", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ label }) => <div data-testid="deriving">{label}</div>,
}));
vi.mock("../building/PipelineChecks", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ pipeline }) => (
    <div data-testid="failed-step">
      {pipeline.find((step) => step.status === "failed")?.failure?.detail}
    </div>
  ),
}));

const { default: BuildingPane } = await import("../building/BuildingPane");

const UNAVAILABLE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";
// The words job creation answers with; a launch's attempt record keeps them.
const CREATE_TIME_REFUSAL =
  "Private GitHub repositories are not available on Future AGI cloud. Use a public repository or upload the code.";
const NOT_YOUR_AGENT =
  "This failure wasn't caused by your agent. Start a new build to try again.";

const failedJob = (failure) => ({
  status: { stage: "failed", failure },
  events: [],
});
const privateRefusal = (domain) =>
  failedJob({
    domain,
    stage: "acquiring_source",
    code: "github_private_unavailable",
    message: CREATE_TIME_REFUSAL,
  });
const sandboxFailure = failedJob({
  domain: "infrastructure",
  stage: "queued",
  code: "sandbox_launch_failed",
  message: "sandbox failed before the guest entrypoint started",
});

const renderPane = (job) => {
  const { done, running, failure } = jobToBuildProgress(job);
  return render(
    <BuildingPane
      done={done}
      running={running}
      failure={failure}
      onStartNewBuild={vi.fn()}
    />,
  );
};

describe("a build that couldn't read its private GitHub repository", () => {
  it.each(["infrastructure", "environment"])(
    "says what happened on the failed step and offers no retry (%s)",
    (domain) => {
      const { failure } = jobToBuildProgress(privateRefusal(domain));

      expect(failure.detail).toBe(UNAVAILABLE);
      expect(failure.retryable).toBe(false);
    },
  );

  it("shows the sentence and never blames the platform", () => {
    renderPane(privateRefusal("infrastructure"));

    expect(screen.getByTestId("failed-step")).toHaveTextContent(UNAVAILABLE);
    expect(screen.queryByText(NOT_YOUR_AGENT)).toBeNull();
    expect(screen.queryByText(/wasn't caused by your agent/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Start a new build" }),
    ).toBeInTheDocument();
  });
});

describe("a build that failed in the sandbox", () => {
  it("reads as before: its own message, and not the agent's fault", () => {
    const { failure } = jobToBuildProgress(sandboxFailure);
    expect(failure.detail).toBe(
      "sandbox failed before the guest entrypoint started",
    );
    expect(failure.retryable).toBe(true);

    renderPane(sandboxFailure);

    expect(screen.getByText(NOT_YOUR_AGENT)).toBeInTheDocument();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });
});
