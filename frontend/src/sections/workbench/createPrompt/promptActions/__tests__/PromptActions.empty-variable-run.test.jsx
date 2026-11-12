import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { enqueueSnackbar } from "src/components/snackbar";
import { PromptWorkbenchContext } from "../../WorkbenchContext";
import PromptActions from "../PromptActions";

// Staging walk row 15: Prompts > Run Prompt after editing the message (a V2
// draft) with {{7*7}} that has no value. Run Prompt is held by the
// "empty variable" box; every way out of it except its two buttons closed it
// without a run and without a word, so the walker saw "Run Prompt does
// nothing". The box also promised "the run sends it as written" while Run
// anyway sends the name blank (the answer came back "CALC END").

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));

vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: [
      { id: "v1", template_version: "V1", is_draft: false },
      { id: "v2", template_version: "V2", is_draft: true },
    ],
    refetch: vi.fn(),
  }),
}));

// The index re-exports all of notistack: keep the rest real.
vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const MESSAGE =
  "Reply with exactly this text and nothing else: CALC {{7*7}} END";

const NOT_RUN_NOTICE =
  /^Not run: \{\{7\*7\}\} has no value\..*Variables.*Run anyway/;

function workbenchFor({ text = MESSAGE, variableData = {} } = {}) {
  return {
    currentTab: "Playground",
    setCurrentTab: vi.fn(),
    modelConfig: [{ model: "liquid/lfm-3-1.7b" }],
    prompts: [
      { prompts: [{ role: "user", content: [{ type: "text", text }] }] },
    ],
    templateFormat: "mustache",
    results: [],
    selectedVersions: [{ version: "V2", isDraft: true, id: "a" }],
    isEmptyPrompt: [],
    variableData,
    promptName: "Untitled-1",
    promptGeneratingStatus: [false],
    loadingPrompt: false,
    isAddingDraft: false,
    saveAndRun: vi.fn(),
    setVariableData: vi.fn(),
    setVariableDrawerOpen: vi.fn(),
    setOpenSelectModel: vi.fn(),
    getStreamingIds: () => [],
  };
}

function renderActions(workbench) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
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

// Press Run Prompt; resolves with the box that holds the run.
async function pressRun(workbench) {
  renderActions(workbench);
  fireEvent.click(screen.getByTestId("run-prompt"));
  return screen.findByTestId("run-empty-variables");
}

const notRunCalls = () =>
  enqueueSnackbar.mock.calls.filter(([message]) =>
    /Not run/.test(String(message)),
  );

describe("Run Prompt with {{7*7}} that has no value (V2 draft)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("the box says the run is on hold: an alert dialog titled Not run yet that names {{7*7}}", async () => {
    await pressRun(workbenchFor());
    const dialog = await screen.findByRole("alertdialog", {
      name: "Not run yet",
    });
    expect(dialog).toHaveTextContent(/\{\{7\*7\}\}/);
    expect(dialog).toHaveAccessibleDescription(/\{\{7\*7\}\} has no value/);
  });

  it("Escape does not close the box silently: it says nothing ran and what to do", async () => {
    const workbench = workbenchFor();
    const box = await pressRun(workbench);
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringMatching(NOT_RUN_NOTICE),
        expect.objectContaining({ variant: "info" }),
      ),
    );
    expect(workbench.saveAndRun).not.toHaveBeenCalled();
  });

  it("a second press on Run Prompt (it lands on the box's invisible backdrop) says nothing ran", async () => {
    const workbench = workbenchFor();
    const box = await pressRun(workbench);
    const backdrop = box
      .closest(".MuiPopover-root")
      .querySelector(".MuiBackdrop-root");
    fireEvent.click(backdrop);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringMatching(NOT_RUN_NOTICE),
        expect.objectContaining({ variant: "info" }),
      ),
    );
    expect(workbench.saveAndRun).not.toHaveBeenCalled();
  });

  it("the box tells the truth: Run anyway leaves {{7*7}} blank, it is not sent as written", async () => {
    const box = await pressRun(workbenchFor());
    expect(box).not.toHaveTextContent(/as written/);
    expect(box).toHaveTextContent(/blank/);
  });

  it("Fill it finds the value box even when the name holds a double quote", async () => {
    const name = 'a"b';
    const input = document.createElement("input");
    input.setAttribute("data-testid", `variable-value-${name}`);
    document.body.appendChild(input);
    try {
      const workbench = workbenchFor({ text: `Say {{${name}}}` });
      await pressRun(workbench);
      fireEvent.click(screen.getByRole("button", { name: "Fill it" }));
      await waitFor(() => expect(document.activeElement).toBe(input), {
        timeout: 2000,
      });
      expect(workbench.setVariableDrawerOpen).not.toHaveBeenCalled();
    } finally {
      input.remove();
    }
  });

  it("guard, passes on BASE too: Run anyway with the name missing fills it blank and runs once, with no Not run notice", async () => {
    const workbench = workbenchFor();
    await pressRun(workbench);
    fireEvent.click(screen.getByRole("button", { name: "Run anyway" }));
    expect(workbench.setVariableData).toHaveBeenCalledWith(
      expect.objectContaining({ "7*7": [""] }),
    );
    await waitFor(() => expect(workbench.saveAndRun).toHaveBeenCalledTimes(1));
    expect(notRunCalls()).toHaveLength(0);
  });

  it("guard, passes on BASE too: Run anyway with the name present but blank runs at once", async () => {
    const workbench = workbenchFor({ variableData: { "7*7": [""] } });
    await pressRun(workbench);
    fireEvent.click(screen.getByRole("button", { name: "Run anyway" }));
    expect(workbench.saveAndRun).toHaveBeenCalledTimes(1);
    expect(workbench.setVariableData).not.toHaveBeenCalled();
    expect(notRunCalls()).toHaveLength(0);
  });

  it("guard, passes on BASE too: the box shows {{7*7}} literally and never computes it", async () => {
    const box = await pressRun(workbenchFor());
    expect(box).toHaveTextContent("{{7*7}}");
    expect(box.textContent).not.toMatch(/49/);
  });
});
