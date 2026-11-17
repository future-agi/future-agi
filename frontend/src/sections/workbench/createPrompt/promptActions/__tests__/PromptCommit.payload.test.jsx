import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PropTypes from "prop-types";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import SaveAndCommit from "../SaveAndCommit";
import { useRunOnDraftGuard } from "../SaveDraftFirstDialog";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    develop: {
      runPrompt: {
        commitSavePrompt: (id) => `/model-hub/prompt-templates/${id}/commit/`,
        getNameChange: (id) => `/model-hub/prompt-templates/${id}/save-name/`,
      },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { promptCommitClicked: "promptCommitClicked" },
  PropertyName: { promptId: "promptId" },
  trackEvent: vi.fn(),
}));

const COMMIT_URL = "/model-hub/prompt-templates/prompt-1/commit/";

function renderAction(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.spyOn(queryClient, "invalidateQueries");
  vi.spyOn(queryClient, "refetchQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/workbench/create/prompt-1"]}>
        <Routes>
          <Route path="/dashboard/workbench/create/:id" element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
}

function RunOnDraft({ onRun }) {
  const { onClickCapture, dialog, savedVersion } = useRunOnDraftGuard({
    promptId: "prompt-1",
    version: "v1",
    isDraft: true,
  });
  return (
    <>
      <div onClickCapture={onClickCapture}>
        <button data-testid="run-on-rows" onClick={onRun}>
          Run on a dataset
        </button>
      </div>
      {dialog}
      <output data-testid="saved-version">{savedVersion}</output>
    </>
  );
}

RunOnDraft.propTypes = { onRun: PropTypes.func.isRequired };

describe("prompt commit payloads before a provider run", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.post.mockResolvedValue({ data: { result: "Committed" } });
  });

  it.each([
    ["Commit", false],
    ["Commit and set as a default version", true],
  ])(
    "%s finalizes the draft and refreshes version state",
    async (button, setDefault) => {
      const onClose = vi.fn();
      const queryClient = renderAction(
        <SaveAndCommit
          open
          onClose={onClose}
          promptName="Unrun prompt"
          askName={false}
          data={{ version: "v1", isDraft: true, isDefault: false }}
        />,
      );

      fireEvent.click(
        screen.getByRole("button", { name: button, exact: true }),
      );

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledExactlyOnceWith(COMMIT_URL, {
          message: "v1",
          set_default: setDefault,
          is_draft: false,
          version_name: "v1",
        });
        expect(onClose).toHaveBeenCalledOnce();
      });
      expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["prompt-versions", "prompt-1"],
      });
      expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["prompt-latest-version", "prompt-1"],
      });
    },
  );

  it("Save and continue commits the draft before opening the dataset run", async () => {
    const onRun = vi.fn();
    const queryClient = renderAction(<RunOnDraft onRun={onRun} />);

    fireEvent.click(screen.getByRole("button", { name: "Run on a dataset" }));
    expect(onRun).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledExactlyOnceWith(COMMIT_URL, {
        message: "",
        set_default: false,
        is_draft: false,
        version_name: "v1",
      });
      expect(onRun).toHaveBeenCalledOnce();
      expect(screen.getByTestId("saved-version")).toHaveTextContent("v1");
    });
    expect(queryClient.refetchQueries).toHaveBeenCalledWith({
      queryKey: ["prompt-latest-version", "prompt-1"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["prompt-versions", "prompt-1"],
    });
  });
});
