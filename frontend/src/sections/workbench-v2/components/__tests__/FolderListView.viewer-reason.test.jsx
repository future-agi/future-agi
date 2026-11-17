/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import FolderListView from "../FolderListView";
import { usePromptStore } from "../../store/usePromptStore";

const auth = vi.hoisted(() => ({ role: "Viewer" }));

vi.mock("react-router", () => ({
  useParams: () => ({ folder: "all" }),
}));

// A workspace with no prompts yet.
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() =>
      Promise.resolve({ data: { results: [], count: 0, total_pages: 1 } }),
    ),
  },
  endpoints: {
    develop: {
      runPrompt: {
        promptExecutions: () => "/model-hub/prompt-executions/",
        promptTemplate: "/model-hub/prompt-base-templates/",
      },
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

vi.mock("../PromptItem", () => ({
  default: ({ name }) => <div data-testid="prompt-item">{name}</div>,
}));

vi.mock("../../../workbench/SelectedPromptTemplateDrawer", () => ({
  SelectedPromptTemplateDrawer: () => null,
}));

const REASON =
  "Viewers can't create prompts. Ask a workspace admin for Member access.";

function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FolderListView sortConfig={{ field: "updated_at", direction: "desc" }} />
    </QueryClientProvider>,
  );
}

describe("FolderListView first run — view-only teammates (W10-DESIGN-SYSTEM-U03)", () => {
  beforeEach(() => {
    auth.role = "Viewer";
    usePromptStore.setState({ searchQuery: "", newPromptModal: false });
  });

  it.each(["Viewer", "workspace_viewer"])(
    "tells a %s why Create prompt is disabled",
    async (role) => {
      auth.role = role;
      renderView();

      expect(await screen.findByText("No prompts yet")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Create prompt" }),
      ).toBeDisabled();
      expect(screen.getByText(REASON)).toBeInTheDocument();
    },
  );

  it("shows no reason when the teammate can create prompts", async () => {
    auth.role = "Member";
    renderView();

    expect(await screen.findByText("No prompts yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create prompt" })).toBeEnabled();
    expect(screen.queryByText(/can't create prompts/)).not.toBeInTheDocument();
  });
});
