import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PromptPreview from "../PromptPreview";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: () => ({
    queryKey: ["scenario-prompt-preview-dataset"],
    queryFn: () => ({}),
    enabled: false,
  }),
}));
vi.mock("src/components/ReactMarkdownWithHighlight", () => ({
  default: ({ content }) => <div>{content}</div>,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../EditPrompt", () => ({ default: () => null }));

const renderPreview = (scenario) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PromptPreview scenario={scenario} />
    </QueryClientProvider>,
  );

describe("PromptPreview: a scenario whose generation failed", () => {
  it("says plainly why there is no prompt", () => {
    renderPreview({
      id: "scn-1",
      status: "Failed",
      dataset: null,
      prompts: [],
    });

    expect(
      screen.getByText(
        "Scenario generation failed, so there is no prompt to show.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("There was an error generating the prompt"),
    ).toBeNull();
  });

  it("still shows a prompt that was saved before the failure", () => {
    renderPreview({
      id: "scn-1",
      status: "Failed",
      dataset: null,
      prompts: [
        { role: "system", content: "You are a caller asking for a refund." },
      ],
    });

    expect(
      screen.getByText("You are a caller asking for a refund."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Scenario generation failed, so there is no prompt to show.",
      ),
    ).toBeNull();
  });
});
