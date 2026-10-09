import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import SpanDetailPane from "../SpanDetailPane";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// The trace drawer is the one place an eval can jump to its span, so its
// Evals tab keeps the View span action the voice and chat drawers drop.
describe("SpanDetailPane evals tab", () => {
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
