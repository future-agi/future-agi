import React from "react";
import { Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

const h = vi.hoisted(() => ({
  detail: null,
  versions: null,
  mutation: { mutateAsync: vi.fn(), isPending: false },
  enqueueSnackbar: vi.fn(),
  post: vi.fn(),
}));

vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: h.detail, isLoading: false }),
  useUpdateEval: () => h.mutation,
  useDuplicateEval: () => h.mutation,
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: h.versions }),
  useCreateEvalVersion: () => h.mutation,
  useSetDefaultVersion: () => h.mutation,
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({ data: null }),
  useUpdateCompositeEval: () => h.mutation,
  useExecuteCompositeEval: () => h.mutation,
  useExecuteCompositeEvalAdhoc: () => h.mutation,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: {
    TURING_MODELS: "turing",
    AGENTIC_EVAL: "agent",
    JEV_MODELS: "jev",
  },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: h.enqueueSnackbar }),
}));
vi.mock("src/hooks/use-credit-exhaustion", () => ({
  useCreditExhaustion: () => ({
    exhaustionError: null,
    handleError: vi.fn(),
    handleUpgradeClick: vi.fn(),
    handleDismiss: vi.fn(),
  }),
}));
vi.mock("src/components/CreditExhaustionBanner", () => ({
  CreditExhaustionBanner: () => null,
}));
vi.mock("../../hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn(), reset: vi.fn() }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: h.post },
  endpoints: { develop: { eval: {} } },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

// Keep the real detail page, playground, LLMPromptEditor, MessageEditor and
// MessageEditorBlock. Only the Quill DOM leaf is replaced with an editable
// textarea; input still traverses the production message/dirty-state handlers.
/* eslint-disable react/prop-types */
vi.mock("src/components/PromptCards/PromptEditor", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function PromptEditorInput(
      { prompt, onPromptChange, disabled },
      ref,
    ) {
      return (
        <textarea
          ref={ref}
          aria-label="System message"
          value={prompt.map((block) => block.text || "").join("")}
          disabled={disabled}
          onChange={(event) =>
            onPromptChange([{ type: "text", text: event.target.value }])
          }
        />
      );
    }),
  };
});
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
/* eslint-enable react/prop-types */
vi.mock("../ModelSelector", () => ({
  default: () => null,
  FAGI_MODEL_VALUES: new Set(),
  JEV_MODEL_VALUES: new Set(),
}));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../OutputTypeConfig", () => ({ default: () => null }));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({ default: () => null }));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../../types/TypedEvalDetail", () => ({ default: () => null }));
vi.mock("../../alignment/AgreementTab", () => ({ default: () => null }));
vi.mock("../CodeEditor", () => ({ default: () => null }));
vi.mock("../EvalResultDisplay", () => ({ default: () => null }));
vi.mock("../DatasetTestMode", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef(() => null) };
});
vi.mock("../TracingTestMode", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef(() => null) };
});
vi.mock("../SimulationTestMode", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef(() => null) };
});

const SAVED = {
  1: "Judge {{output}} using the saved default instructions.",
  2: "Judge {{output}} using the saved alternate instructions.",
};
const DRAFT = "Judge {{output}} using these unsaved, stricter instructions.";
const versionButton = (number) =>
  screen.getByRole("button", { name: `V${number}` });
const messageInput = () => screen.getByRole("textbox", { name: "System message" });
const saveButton = () => screen.getByRole("button", { name: "Save Version" });

// The editor deliberately releases its population guard on the next task.
const finishPopulation = async () => {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
};

async function openSavedVersion(number = 1) {
  // V1 exercises the initial automatic default selection, not a hand-set child
  // state. V2 exercises the same flow when arriving via a saved-version URL.
  window.history.replaceState(
    {},
    "",
    `/dashboard/evaluations/fixture-eval?keep=1${number === 1 ? "" : `&v=${number}`}`,
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Routes>
        <Route
          path="/dashboard/evaluations/:evalId"
          element={<EvalDetailPage />}
        />
      </Routes>
    </QueryClientProvider>,
  );
  await screen.findByDisplayValue(SAVED[number]);
  await finishPopulation();
  fireEvent.click(screen.getByText("Versions", { exact: true }));
  expect(versionButton(number)).toHaveAttribute("aria-current", "true");
  expect(saveButton()).toBeDisabled();
  expect(new URLSearchParams(window.location.search).get("v")).toBe(String(number));
  expect(window.confirm).not.toHaveBeenCalled();
}

function editMessage() {
  fireEvent.change(messageInput(), { target: { value: DRAFT } });
  expect(messageInput()).toHaveValue(DRAFT);
  expect(saveButton()).toBeEnabled();
}

function expectDraftRetained(number, search) {
  expect(messageInput()).toHaveValue(DRAFT);
  expect(saveButton()).toBeEnabled();
  expect(window.location.search).toBe(search);
  expect(versionButton(number)).toHaveAttribute("aria-current", "true");
  expect(versionButton(number === 1 ? 2 : 1)).not.toHaveAttribute("aria-current");
}

function expectSaved(number) {
  expect(messageInput()).toHaveValue(SAVED[number]);
  expect(saveButton()).toBeDisabled();
  const params = new URLSearchParams(window.location.search);
  expect(params.get("v")).toBe(String(number));
  expect(params.get("keep")).toBe("1");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  h.versions = {
    versions: [1, 2].map((number) => ({
      id: `version-${number}`,
      version_number: number,
      is_default: number === 1,
      criteria: SAVED[number],
      config_snapshot: {
        model: "gpt-4o-mini",
        messages: [{ role: "system", content: SAVED[number] }],
        output: "Pass/Fail",
      },
    })),
  };
  h.detail = {
    id: "fixture-eval",
    name: "Saved message evaluation",
    owner: "user",
    eval_type: "llm",
    instructions: SAVED[1],
    output_type: "pass_fail",
    config: h.versions.versions[0].config_snapshot,
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("W3-A-002 saved-version discard decisions", () => {
  it.each([1, 2])(
    "cancel selected V%i deselection retains edits, dirty state, URL and highlighting",
    async (number) => {
      await openSavedVersion(number);
      editMessage();
      const search = window.location.search;

      fireEvent.click(versionButton(number));

      expect(window.confirm).toHaveBeenCalledTimes(1);
      expect(window.confirm).toHaveBeenCalledWith(
        "You have unsaved changes. Discard and switch version?",
      );
      expectDraftRetained(number, search);
      expect(h.mutation.mutateAsync).not.toHaveBeenCalled();
      expect(h.post).not.toHaveBeenCalled();
    },
  );

  it.each([1, 2])(
    "confirm selected V%i deselection discards to the saved default once",
    async (number) => {
      await openSavedVersion(number);
      editMessage();
      window.confirm.mockReturnValue(true);

      fireEvent.click(versionButton(number));

      expect(window.confirm).toHaveBeenCalledTimes(1);
      expectSaved(1);
      if (number === 2) {
        expect(versionButton(1)).toHaveAttribute("aria-current", "true");
      }
    },
  );

  it.each([1, 2])(
    "clean selected V%i deselection loads the default without warning",
    async (number) => {
      await openSavedVersion(number);

      fireEvent.click(versionButton(number));

      expect(window.confirm).not.toHaveBeenCalled();
      expectSaved(1);
    },
  );

  it.each([false, true])(
    "Back to default has one shared discard decision (confirm=%s)",
    async (confirmed) => {
      await openSavedVersion(2);
      editMessage();
      const search = window.location.search;
      window.confirm.mockReturnValue(confirmed);

      fireEvent.click(screen.getByRole("button", { name: "Back to default" }));

      expect(window.confirm).toHaveBeenCalledTimes(1);
      if (confirmed) {
        expectSaved(1);
        expect(versionButton(1)).toHaveAttribute("aria-current", "true");
        expect(screen.queryByRole("button", { name: "Back to default" })).toBeNull();
      } else {
        expectDraftRetained(2, search);
      }
    },
  );

  it("clean Back to default does not warn", async () => {
    await openSavedVersion(2);

    fireEvent.click(screen.getByRole("button", { name: "Back to default" }));

    expect(window.confirm).not.toHaveBeenCalled();
    expectSaved(1);
    expect(versionButton(1)).toHaveAttribute("aria-current", "true");
  });

  it.each(["row", "restore"])(
    "cancel explicit version %s preserves selection and does not announce a load",
    async (action) => {
      await openSavedVersion();
      editMessage();
      const search = window.location.search;
      h.enqueueSnackbar.mockClear();

      if (action === "restore") {
        fireEvent.click(screen.getAllByRole("button", { name: "Actions" })[1]);
        fireEvent.click(within(screen.getByRole("menu")).getByText("Restore Version"));
      } else {
        fireEvent.click(versionButton(2));
      }

      expect(window.confirm).toHaveBeenCalledTimes(1);
      expectDraftRetained(1, search);
      expect(h.enqueueSnackbar).not.toHaveBeenCalled();
    },
  );

  it.each(["row", "restore"])(
    "confirm explicit version %s loads the chosen version once",
    async (action) => {
      await openSavedVersion();
      editMessage();
      window.confirm.mockReturnValue(true);

      if (action === "restore") {
        fireEvent.click(screen.getAllByRole("button", { name: "Actions" })[1]);
        fireEvent.click(within(screen.getByRole("menu")).getByText("Restore Version"));
      } else {
        fireEvent.click(versionButton(2));
      }

      expect(window.confirm).toHaveBeenCalledTimes(1);
      expectSaved(2);
      expect(versionButton(2)).toHaveAttribute("aria-current", "true");
    },
  );
});
