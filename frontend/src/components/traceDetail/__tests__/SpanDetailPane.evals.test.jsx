import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import SpanDetailPane from "../SpanDetailPane";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const failedScore = {
  eval_config_id: "eval-1",
  eval_name: "Toxicity check",
  output_type: "Pass/Fail",
  result: false,
  score: 0,
  explanation: "The reply insults the user.",
};

const rollup = {
  scope: "trace",
  evals: [
    {
      eval_config_id: "eval-1",
      eval_name: "Toxicity check",
      output_type: "Pass/Fail",
      target_type: "span",
      aggregate: { pass: 0, fail: 1 },
      spans: [
        {
          span_id: "span-1",
          span_name: "llm call",
          value: "Failed",
          explanation: "The reply insults the user.",
        },
      ],
    },
  ],
};

const renderEvalsTab = async (entry) => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SpanDetailPane
        entry={{
          observation_span: {
            id: "span-1",
            trace: "trace-1",
            name: "llm call",
          },
          ...entry,
        }}
        projectId="project-1"
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
  await user.click(screen.getByText("Evals"));
  return user;
};

describe("SpanDetailPane evals tab", () => {
  it("keeps the eval list, labels, explanation and Fix with Falcon when a rollup is present", async () => {
    const user = await renderEvalsTab({
      eval_scores: [failedScore],
      eval_rollup: rollup,
    });

    expect(screen.getByPlaceholderText("Search evals...")).toBeInTheDocument();
    // One eval name on the tab: the collapsed rollup adds no second copy.
    expect(screen.getByText("Fail")).toBeInTheDocument();
    await user.click(screen.getByText("Toxicity check"));
    expect(screen.getByText("The reply insults the user.")).toBeVisible();
    // Summary bar and the expanded failed row both offer it.
    expect(screen.getAllByText("Fix with Falcon").length).toBeGreaterThan(0);
  });

  it("offers the per-span rollup as a collapsed section above the list", async () => {
    const user = await renderEvalsTab({
      eval_scores: [failedScore],
      eval_rollup: rollup,
    });

    expect(screen.queryByText("View span")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: /per-span breakdown/i }),
    );
    expect(screen.getByText("View span")).toBeVisible();
    expect(screen.getByPlaceholderText("Search evals...")).toBeInTheDocument();
  });

  it("says the rollup is unavailable without hiding the eval list", async () => {
    await renderEvalsTab({
      eval_scores: [failedScore],
      eval_rollup: { scope: "trace", evals: [], error: true },
    });

    expect(
      screen.getByText("Evaluations are temporarily unavailable."),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search evals...")).toBeInTheDocument();
  });

  // The trace drawer is the one place an eval can jump to its span, so its
  // Evals tab keeps the View span action the voice and chat drawers drop.
  it("keeps View span on each eval and jumps to that eval's span", async () => {
    const user = userEvent.setup();
    const onSelectSpan = vi.fn();

    render(
      <QueryClientProvider client={new QueryClient()}>
        <SpanDetailPane
          entry={{
            observation_span: { id: "span-1", trace: "trace-1", name: "root" },
            eval_scores: [
              { eval_config_id: "cfg-1", eval_name: "Tone", score: 90 },
            ],
            children: [
              {
                observation_span: { id: "span-2", name: "llm call" },
                eval_scores: [
                  { eval_config_id: "cfg-2", eval_name: "Policy", score: 20 },
                ],
              },
            ],
          }}
          projectId="project-1"
          onSelectSpan={onSelectSpan}
          onClose={() => {}}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByText("Evals"));

    expect(screen.getAllByText("View span")).toHaveLength(2);
    expect(screen.getAllByTestId("eval-actions-column")).toHaveLength(3);
    await user.click(screen.getAllByText("View span")[1]);
    expect(onSelectSpan).toHaveBeenCalledWith("span-2");
  });
});
