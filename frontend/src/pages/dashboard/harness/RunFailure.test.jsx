import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { render } from "src/utils/test-utils";

import { CAUSE_WORDS, INFRASTRUCTURE_LINE } from "./failureWords";
import RunFailure from "./RunFailure";
import ScenarioOutcome from "./ScenarioOutcome";

const certify = {
  domain: "environment",
  code: "authoring_runtime_validation_failed",
  cause: "agent_exited_before_ready",
  message:
    "We couldn't run this agent version in its environment: ProcessRuntimeError: depends_on/spawn_failed (agent): agent: exited before readiness probe passed",
};

const infrastructure = {
  domain: "infrastructure",
  code: "guest_crashed",
  message: "guest entrypoint exited 2",
};

describe("RunFailure", () => {
  // The cause is the point; the code and the raw line are for debugging.
  it("reads a named cause in plain words and keeps the raw record behind Details", async () => {
    const user = userEvent.setup();
    render(<RunFailure failure={certify} />);

    expect(
      screen.getByText(
        "Didn't start — the agent exited before it was ready. Fix the crash on startup, then push a new agent version.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ProcessRuntimeError/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/authoring runtime validation failed/i),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.getByText(/ProcessRuntimeError/)).toBeInTheDocument();
    expect(screen.getByText(/agent_exited_before_ready/)).toBeInTheDocument();
  });

  it("reads a cause it does not know as unrecorded, never as the raw value", () => {
    render(<RunFailure failure={{ ...certify, cause: "something_newer" }} />);

    expect(screen.getByText(CAUSE_WORDS.unknown)).toBeInTheDocument();
    expect(screen.queryByText(/something_newer/)).not.toBeInTheDocument();
  });

  it("reads our infrastructure's failure as ours, not the agent's", async () => {
    const user = userEvent.setup();
    render(<RunFailure failure={infrastructure} />);

    expect(screen.getByText(INFRASTRUCTURE_LINE)).toBeInTheDocument();
    expect(
      screen.queryByText(/guest entrypoint exited 2/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/guest crashed/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.getByText(/guest entrypoint exited 2/)).toBeInTheDocument();
  });

  it("leaves any other failure as it was recorded", () => {
    render(
      <RunFailure
        failure={{
          domain: "environment",
          code: "provider_credentials_invalid",
          message: "The Vapi key was rejected.",
        }}
      />,
    );

    expect(
      screen.getByText("Environment · Provider credentials invalid"),
    ).toBeInTheDocument();
    expect(screen.getByText("The Vapi key was rejected.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Details/ }),
    ).not.toBeInTheDocument();
  });
});

describe("ScenarioOutcome failure", () => {
  it("reads a scenario lost to our infrastructure as ours, with the raw line behind Details", () => {
    render(
      <ScenarioOutcome
        outcome={{ status: "errored", subGoals: [], failure: infrastructure }}
      />,
    );

    expect(screen.getByText(INFRASTRUCTURE_LINE)).toBeInTheDocument();
    expect(
      screen.queryByText(/guest entrypoint exited 2/),
    ).not.toBeInTheDocument();
  });
});
