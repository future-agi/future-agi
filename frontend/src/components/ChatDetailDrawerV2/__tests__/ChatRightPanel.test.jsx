import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";

import ChatRightPanel from "../ChatRightPanel";

const renderPanel = (data) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ChatRightPanel data={data} />
    </QueryClientProvider>,
  );

const base = { id: "chat-1", module: "simulate", status: "completed", messages: [], scenario_columns: {} };

describe("ChatRightPanel Evals tab", () => {
  it("lists the call's evals but not its sub-goal checks", async () => {
    renderPanel({
      ...base,
      eval_metrics: {
        "ev-1": { name: "tone_check", value: "Passed", type: "Pass/Fail", kind: "evaluation" },
        "sg-1": { name: "pin_verified", value: "Passed", type: "Pass/Fail", kind: "sub_goal" },
      },
    });
    await userEvent.click(screen.getByRole("tab", { name: /Evals/ }));
    expect(screen.getByText("tone_check")).toBeInTheDocument();
    expect(screen.queryByText("pin_verified")).toBeNull();
  });
});

describe("ChatRightPanel choice evals", () => {
  it("counts a choice eval by the server's verdict and shows its answer", async () => {
    renderPanel({
      ...base,
      eval_metrics: {
        "ev-loop": { id: "ev-loop", name: "loop_detection", value: ["never"], type: "choices" },
        "ev-tone": { id: "ev-tone", name: "tone_check", value: "Passed", type: "Pass/Fail" },
      },
      evaluations: [
        { id: "ev-loop", name: "loop_detection", passed: true },
        { id: "ev-tone", name: "tone_check", passed: true },
      ],
    });
    await userEvent.click(screen.getByRole("tab", { name: /Evals/ }));
    expect(screen.getByText("never")).toBeInTheDocument();
    expect(screen.getByText(/2\/2 passed/)).toBeInTheDocument();
  });
});

describe("ChatRightPanel removed evals", () => {
  it("marks a removed eval and leaves it out of the pass count", async () => {
    renderPanel({
      ...base,
      eval_metrics: {
        "ev-tone": { id: "ev-tone", name: "tone_check", value: "Passed", type: "Pass/Fail" },
        "ev-old": { id: "ev-old", name: "old_check", value: "Failed", type: "Pass/Fail", removed: true },
      },
    });
    await userEvent.click(screen.getByRole("tab", { name: /Evals/ }));
    expect(screen.getByTestId("removed-eval-marker")).toBeInTheDocument();
    expect(screen.getByText(/1\/1 passed/)).toBeInTheDocument();
    expect(screen.queryByText(/1 failed/)).toBeNull();
  });
});

describe("ChatRightPanel Scenario tab", () => {
  it("shows for a call with a persona but no scenario columns", () => {
    renderPanel({ ...base, persona_details: { name: "Siddharth Nair", voice: null, age: null, traits: [] } });
    expect(screen.getByRole("tab", { name: "Scenario" })).toBeInTheDocument();
  });

  it("stays hidden when there is neither", () => {
    renderPanel({ ...base, persona_details: null });
    expect(screen.queryByRole("tab", { name: "Scenario" })).not.toBeInTheDocument();
  });
});
