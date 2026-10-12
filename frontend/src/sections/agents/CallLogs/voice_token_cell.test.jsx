import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import VoiceTokenCell from "./VoiceTokenCell";

const reportedUsage = {
  "gen_ai.usage.input_tokens": 119318,
  "gen_ai.usage.output_tokens": 846,
  "gen_ai.usage.total_tokens": 120164,
};

describe("VoiceTokenCell", () => {
  it("formats the reported input, output, and total without changing the counts", () => {
    render(<VoiceTokenCell data={reportedUsage} />);
    expect(screen.getByText("119.32k")).toBeInTheDocument();
    expect(screen.getByText("846")).toBeInTheDocument();
    expect(screen.getByText("(Σ 120.16k)")).toBeInTheDocument();
  });

  it("shows exact counts in the breakdown tooltip", async () => {
    const { container } = render(<VoiceTokenCell data={reportedUsage} />);
    fireEvent.mouseOver(
      container.querySelector("[data-mui-internal-clone-element]"),
    );
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Input tokens119,318");
    expect(tooltip).toHaveTextContent("Output tokens846");
    expect(tooltip).toHaveTextContent("Total tokens120,164");
  });

  it.each([
    { prompt_tokens: 119318, completion_tokens: 846 },
    {
      "cost_breakdown.llmPromptTokens": 119318,
      "cost_breakdown.llmCompletionTokens": 846,
    },
  ])("preserves legacy token sources and derived totals: %j", (data) => {
    render(<VoiceTokenCell data={data} />);
    expect(screen.getByText("119.32k")).toBeInTheDocument();
    expect(screen.getByText("846")).toBeInTheDocument();
    expect(screen.getByText("(Σ 120.16k)")).toBeInTheDocument();
  });

  it("retains gen_ai usage precedence over legacy fields", () => {
    render(
      <VoiceTokenCell
        data={{ ...reportedUsage, prompt_tokens: 42, total_tokens: 99 }}
      />,
    );
    expect(screen.getByText("119.32k")).toBeInTheDocument();
    expect(screen.getByText("(Σ 120.16k)")).toBeInTheDocument();
  });

  it("renders total-only usage", () => {
    render(<VoiceTokenCell data={{ total_tokens: 120164 }} />);
    expect(screen.getByText("120,164")).toBeInTheDocument();
  });

  it.each([undefined, {}, { total_tokens: 0 }])(
    "renders a dash for missing usage: %j",
    (data) => {
      render(<VoiceTokenCell data={data} />);
      expect(screen.getByText("-")).toBeInTheDocument();
    },
  );
});
