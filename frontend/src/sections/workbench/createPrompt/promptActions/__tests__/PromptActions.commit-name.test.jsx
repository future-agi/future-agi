import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { PromptWorkbenchContext } from "../../WorkbenchContext";
import PromptActions from "../PromptActions";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const promptVersions = { versions: [] };
vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: promptVersions.versions,
    refetch: vi.fn(),
  }),
}));

// A new prompt after Run: the run saves v1 (is_draft false) but nothing has
// been committed yet (no commit message).
const RUN_NOT_COMMITTED = {
  id: "ver-1",
  template_version: "v1",
  is_draft: false,
  commit_message: "",
};

const workbench = {
  currentTab: "Playground",
  setCurrentTab: vi.fn(),
  modelConfig: [{ model: "gpt-4o" }],
  prompts: [
    {
      prompts: [
        { role: "user", content: [{ type: "text", text: "Customer asks" }] },
      ],
    },
  ],
  results: [{ output: [{ text: "echo" }] }],
  selectedVersions: [
    {
      version: "v1",
      isDraft: false,
      lastSaved: "2026-11-10T18:43:34.798930Z",
      labels: [],
      id: "a",
    },
  ],
  isEmptyPrompt: [],
  variableData: {},
  promptName: "Untitled-1",
  setPromptName: vi.fn(),
  promptGeneratingStatus: [false],
  getStreamingIds: () => [],
};

function renderActions() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <PromptWorkbenchContext.Provider value={workbench}>
        <MemoryRouter initialEntries={["/dashboard/workbench/create/prompt-1"]}>
          <Routes>
            <Route
              path="/dashboard/workbench/create/:id"
              element={<PromptActions />}
            />
          </Routes>
        </MemoryRouter>
      </PromptWorkbenchContext.Provider>
    </QueryClientProvider>,
  );
}

function openCommit() {
  fireEvent.click(screen.getByRole("button", { name: "Commit" }));
  return screen.getByRole("dialog");
}

// WLA-R2-01: the first Commit of a prompt asks for its name, also when the
// prompt was run first (a run marks the version as saved, not committed).
describe("PromptActions first Commit asks for the prompt name", () => {
  let post;
  beforeEach(() => {
    post = vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
  });
  afterEach(() => {
    post.mockRestore();
    promptVersions.versions = [];
  });

  it("shows Prompt name on the first Commit after a run", () => {
    promptVersions.versions = [RUN_NOT_COMMITTED];
    renderActions();

    const dialog = openCommit();

    const nameField = within(dialog).getByTestId("commit-prompt-name");
    expect(nameField).toHaveValue("Untitled-1");
    expect(within(dialog).getByText("Prompt name")).toBeInTheDocument();
  });

  it("saves the version under the typed name", async () => {
    promptVersions.versions = [RUN_NOT_COMMITTED];
    renderActions();

    const dialog = openCommit();
    fireEvent.change(within(dialog).getByTestId("commit-prompt-name"), {
      target: { value: "Order support" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Commit", exact: true }),
    );

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post).toHaveBeenNthCalledWith(
      1,
      expect.stringMatching(/\/prompt-templates\/prompt-1\/save-name\/$/),
      { name: "Order support" },
    );
    expect(post).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(/\/prompt-templates\/prompt-1\/commit\/$/),
      expect.objectContaining({ version_name: "v1", is_draft: false }),
    );
  });

  it("does not ask again once a version is committed", () => {
    promptVersions.versions = [{ ...RUN_NOT_COMMITTED, commit_message: "v1" }];
    renderActions();

    const dialog = openCommit();

    expect(within(dialog).queryByTestId("commit-prompt-name")).toBeNull();
    expect(within(dialog).queryByText("Prompt name")).toBeNull();
  });
});
