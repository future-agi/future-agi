import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ChoosePromptTemplateDrawer } from "../ChoosePromptTemplateDrawer";
import { SelectedPromptTemplateDrawer } from "../SelectedPromptTemplateDrawer";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  },
  endpoints: {
    develop: {
      runPrompt: {
        promptTemplate: "/prompt-base-templates/",
        categories: "/prompt-base-templates/categories/",
        createPromptDraft: "/prompt-templates/create-draft/",
      },
    },
  },
}));
vi.mock("src/sections/develop-detail/RunPrompt/Modals/ImportPrompt", () => ({
  ReadOnlyPrompt: () => <div data-testid="read-only-message" />,
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const BLOG_LINE =
  /Generate engaging blog posts on any topic with SEO optimization/;

function withProviders(node) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

const template = (extra = {}) => ({
  id: "tpl-travel",
  name: "Travel planner",
  desc: "",
  promptConfig: {
    id: "tpl-travel",
    name: "Travel planner",
    category: "planning",
    prompt_config_snapshot: {
      messages: [
        { role: "system", content: "You plan trips." },
        { role: "user", content: "Plan a trip to {{city}}" },
      ],
    },
    ...extra,
  },
});

// FA-PROMPTS-02: the template preview never shows another template's text.
describe("SelectedPromptTemplateDrawer preview", () => {
  it("does not show the fixed blog-post line for a template without a description", () => {
    render(
      withProviders(
        <SelectedPromptTemplateDrawer
          open
          onClose={vi.fn()}
          data={template()}
        />,
      ),
    );
    expect(screen.getByText("Travel planner")).toBeInTheDocument();
    expect(screen.queryByText(BLOG_LINE)).toBeNull();
  });

  it("shows the selected template's own description", () => {
    render(
      withProviders(
        <SelectedPromptTemplateDrawer
          open
          onClose={vi.fn()}
          data={template({ description: "Plan a weekend city trip." })}
        />,
      ),
    );
    expect(screen.getByText("Plan a weekend city trip.")).toBeInTheDocument();
    expect(screen.queryByText(BLOG_LINE)).toBeNull();
  });
});

// FA-PROMPTS-03: the Prompt Templates subtitle reads "coding, research".
describe("ChoosePromptTemplateDrawer subtitle", () => {
  it("has a space after the comma in the subtitle", () => {
    render(
      withProviders(<ChoosePromptTemplateDrawer open onClose={vi.fn()} />),
    );
    expect(screen.getByText("Prompt Templates")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Browse and discover curated prompt templates for writing, coding, research and more.",
      ),
    ).toBeInTheDocument();
  });
});
