import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "src/utils/test-utils";
import TestPlayground from "../TestPlayground";
import {
  evalPresentationTheme,
  expectReadableSegmentedTab,
} from "./evalPresentationTheme";

const h = vi.hoisted(() => ({
  versions: {
    versions: [
      { id: "version-2", version_number: 2, is_default: true },
      { id: "version-1", version_number: 1, is_default: false },
    ],
  },
  select: vi.fn(),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: h.versions }),
  useSetDefaultVersion: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
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
vi.mock("../../hooks/useCompositeEval", () => ({
  useExecuteCompositeEval: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useExecuteCompositeEvalAdhoc: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));
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

beforeEach(() => {
  h.select.mockClear();
  window.history.replaceState({}, "", "/dashboard/evaluations/fixture-eval");
});
afterEach(cleanup);

const mount = (mode = "light") =>
  render(
    <TestPlayground
      templateId="fixture-eval"
      evalType="llm"
      showVersions
      onVersionSelect={h.select}
    />,
    { theme: evalPresentationTheme(mode) },
  );

describe("playground keyboard presentation", () => {
  it.each(["light", "dark"])(
    "keeps source tab labels readable in %s mode",
    (mode) => {
      const theme = evalPresentationTheme(mode);
      mount(mode);
      for (const name of ["Dataset", "Tracing", "Simulation"]) {
        expectReadableSegmentedTab(screen.getByRole("tab", { name }), theme);
      }
    },
  );

  it.each(["{Enter}", " "])(
    "opens Versions and selects a version with %s",
    async (key) => {
      const user = userEvent.setup();
      mount();
      const testTab = screen.getByRole("tab", { name: "Test Evaluation" });
      testTab.focus();
      await user.keyboard("{ArrowRight}");
      const versionsTab = screen.getByRole("tab", { name: "Versions" });
      expect(versionsTab).toHaveFocus();
      await user.keyboard(key);
      expect(versionsTab).toHaveAttribute("aria-selected", "true");
      const version = screen.getByRole("button", { name: /V1/ });
      expect(version).toHaveAttribute("tabindex", "0");
      await user.tab();
      await user.tab();
      await user.tab();
      expect(version).toHaveFocus();
      // jsdom resolves competing focus rules by insertion order. Inspect the
      // badge's local rule rather than a global ButtonBase focus override.
      const rules = Array.from(document.styleSheets).flatMap((sheet) =>
        Array.from(sheet.cssRules),
      );
      const focusRule = rules.find((candidate) => {
        const selectors = candidate.selectorText
          ?.split(",")
          .map((selector) => selector.trim());
        return Array.from(version.classList).some(
          (className) =>
            selectors?.includes(`.${className}:focus-visible`) &&
            selectors.includes(`.${className}.Mui-focusVisible`),
        );
      });
      expect(focusRule).toBeDefined();
      expect(focusRule.style.outline).toContain("2px solid");
      expect(focusRule.style["outline-offset"]).toBe("-2px");
      await user.keyboard(key);
      expect(h.select).toHaveBeenLastCalledWith(h.versions.versions[1]);
      expect(version).toHaveAttribute("aria-current", "true");
      await user.keyboard(key);
      expect(h.select).toHaveBeenLastCalledWith(null);
      expect(version).not.toHaveAttribute("aria-current", "true");
    },
  );

  it("renders version selection and Actions as siblings in a noninteractive row", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("tab", { name: "Versions" }));
    const actions = screen.getAllByRole("button", { name: "Actions" });
    expect(actions).toHaveLength(h.versions.versions.length);
    h.versions.versions.forEach((version, index) => {
      const selection = screen.getByRole("button", {
        name: new RegExp(`V${version.version_number}\\b`),
      });
      const action = actions[index];
      const row = selection.parentElement;
      expect(action.parentElement).toBe(row);
      expect(row).not.toHaveAttribute("role", "button");
      expect(row).not.toHaveAttribute("tabindex");
      expect(
        action.parentElement.closest('button, [role="button"]'),
      ).toBeNull();
      expect(selection.querySelector('button, [role="button"]')).toBeNull();
    });
  });

  it("keeps the native version badge separate from the default chip and Actions", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("tab", { name: "Versions" }));
    const selection = screen.getByRole("button", { name: "V2" });
    const defaultChip = screen.getByText(/^(?:Recorded )?default$/i);
    expect(selection.tagName).toBe("BUTTON");
    expect(selection).toContainElement(screen.getByText("V2"));
    expect(selection.querySelector("div, p")).toBeNull();
    expect(selection.parentElement).toContainElement(defaultChip);
    expect(selection).not.toContainElement(defaultChip);
    expect(defaultChip.closest('button, [role="button"]')).toBeNull();
    // Do not overlay metadata: the union's default-chip tooltip must remain
    // pointer-accessible, and the existing whole-row click target stays intact.
    expect(getComputedStyle(selection).position).not.toBe("absolute");
    await user.click(selection);
    expect(h.select).toHaveBeenCalledTimes(1);
    expect(h.select).toHaveBeenLastCalledWith(h.versions.versions[0]);
    await user.click(defaultChip);
    expect(h.select).toHaveBeenCalledTimes(2);
    expect(h.select).toHaveBeenLastCalledWith(null);
  });

  it.each(["{Enter}", " "])(
    "keeps the version actions independently keyboard-operable with %s",
    async (key) => {
      const user = userEvent.setup();
      mount();
      await user.click(screen.getByRole("tab", { name: "Versions" }));
      const version = screen.getByRole("button", { name: /V1/ });
      const action = screen.getAllByRole("button", { name: "Actions" })[1];
      version.focus();
      await user.tab();
      expect(action).toHaveFocus();
      await user.keyboard(key);
      expect(
        screen.getByRole("menuitem", { name: "Restore Version" }),
      ).toBeVisible();
      expect(
        screen.getByRole("menuitem", { name: "Set as Default" }),
      ).toBeVisible();
      expect(h.select).not.toHaveBeenCalled();
    },
  );
});
