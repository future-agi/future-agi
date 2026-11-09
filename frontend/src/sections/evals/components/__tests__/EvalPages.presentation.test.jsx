/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";
import EvalCreatePage from "../EvalCreatePage";
import {
  evalPresentationTheme,
  expectReadableSegmentedTab,
  expectReadableTab,
} from "./evalPresentationTheme";

const h = vi.hoisted(() => ({
  detail: null,
  versions: { versions: [] },
  mutation: { mutateAsync: vi.fn(), isPending: false },
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: h.detail, isLoading: false }),
  useUpdateEval: () => h.mutation,
  useDuplicateEval: () => h.mutation,
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: h.versions }),
  useCreateEvalVersion: () => h.mutation,
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({ data: null }),
  useUpdateCompositeEval: () => h.mutation,
  useCreateCompositeEval: () => h.mutation,
}));
vi.mock("../../hooks/useCreateEval", () => ({
  useCreateEval: () => h.mutation,
}));
vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { TURING_MODELS: "turing", AGENTIC_EVAL: "agent" },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(async () => ({ data: {} })) },
  endpoints: { develop: { eval: { createEvalTemplateV2: "/fixture-draft/" } } },
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("../TestPlayground", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef(() => null) };
});
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../LLMPromptEditor", () => ({ default: () => null }));
vi.mock("../OutputTypeConfig", () => ({ default: () => null }));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "",
}));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../ModelSelector", () => ({ FAGI_MODEL_VALUES: new Set() }));
vi.mock("../../types/TypedEvalDetail", () => ({ default: () => null }));
vi.mock("../../types/TypedEvalEditor", () => ({ default: () => null }));
vi.mock("../../alignment/AgreementTab", () => ({ default: () => null }));

beforeEach(() => {
  window.history.replaceState({}, "", "/dashboard/evaluations/fixture-eval");
  h.detail = {
    id: "fixture-eval",
    name: "Fixture",
    owner: "user",
    eval_type: "llm",
    config: {},
  };
});
afterEach(cleanup);

describe("evaluation presentation", () => {
  it("keeps Save Changes as the only primary in the composite action bar", () => {
    h.detail = { ...h.detail, template_type: "composite" };
    render(<EvalDetailPage />, { theme: evalPresentationTheme("light") });
    const save = screen.getByRole("button", { name: "Save Changes" });
    const run = screen.getByRole("button", { name: "Run Composite" });
    const bar = save.closest("span").parentElement.parentElement;
    expect(
      within(bar)
        .getAllByRole("button")
        .filter((button) => button.classList.contains("MuiButton-contained")),
    ).toEqual([save]);
    expect(run).toHaveClass("MuiButton-outlined");
  });

  it.each(["light", "dark"])(
    "uses canonical readable detail tabs in %s mode",
    (mode) => {
      const theme = evalPresentationTheme(mode);
      render(<EvalDetailPage />, { theme });
      for (const name of ["Usage", "Feedback", "Ground Truth"]) {
        expectReadableTab(
          screen.getByRole("tab", { name }),
          theme.palette.background.paper,
          [theme.palette.text.subtitle, theme.palette.text.secondary],
        );
      }
      const list = screen.getByRole("tablist");
      const indicator = list
        .closest(".MuiTabs-root")
        .querySelector(".MuiTabs-indicator");
      expect(getComputedStyle(indicator).display).not.toBe("none");
    },
  );

  it.each(["light", "dark"])(
    "uses readable segmented mode and type labels in %s mode",
    (mode) => {
      const theme = evalPresentationTheme(mode);
      render(<EvalCreatePage />, { theme });
      for (const name of ["Composite", "LLM-As-A-Judge"]) {
        expectReadableSegmentedTab(screen.getByRole("tab", { name }), theme);
      }
    },
  );
});
