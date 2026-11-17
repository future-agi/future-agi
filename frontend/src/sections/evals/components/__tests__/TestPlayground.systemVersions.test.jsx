import React from "react";
import { bpText } from "./pinCopyBp.fixture";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import TestPlayground from "../TestPlayground";

// System evals now have seeded versions (E2E-SMK-02), so their Versions tab
// lists V1, V2 ... A system eval is read-only, and the set-default view only
// accepts user templates, so the per-version actions menu must not be offered
// for one. A user eval keeps it.

const h = vi.hoisted(() => ({
  versions: [],
  setDefault: vi.fn(),
  role: "Owner",
}));

vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: { versions: h.versions } }),
  useSetDefaultVersion: () => ({ mutateAsync: h.setDefault }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
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

const VERSIONS = [
  {
    id: "version-2",
    version_number: 2,
    is_default: true,
    created_at: "2026-10-22T10:00:00Z",
    created_by_name: "",
  },
  {
    id: "version-1",
    version_number: 1,
    is_default: false,
    created_at: "2026-10-21T10:00:00Z",
    created_by_name: "",
  },
];

const openVersions = (props) => {
  render(
    <TestPlayground
      templateId="template-1"
      evalName="is_json"
      evalType="code"
      showVersions
      {...props}
    />,
  );
  fireEvent.click(screen.getByText("Versions"));
};

describe("TestPlayground versions tab", () => {
  beforeEach(() => {
    h.versions = VERSIONS;
    h.role = "Owner";
    h.setDefault.mockReset();
  });

  it("lists a system eval's versions without the actions menu", () => {
    openVersions({ isSystemEval: true });

    expect(screen.getAllByText("V2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("V1").length).toBeGreaterThan(0);
    expect(screen.getByText(bpText["A1-11"])).toHaveAttribute(
      "title",
      bpText["A2-06"],
    );
    expect(
      screen.queryByText("Default", { exact: true }),
    ).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: "Actions" })).toHaveLength(
      0,
    );
    expect(screen.queryByText("Set as Default")).not.toBeInTheDocument();
    expect(screen.queryByText("Restore Version")).not.toBeInTheDocument();
  });

  it("keeps Set as Default and Restore Version for a user eval", () => {
    openVersions({ isSystemEval: false });

    const actions = screen.getAllByRole("button", { name: "Actions" });
    expect(actions).toHaveLength(2);
    // The second row is the non-default V1.
    fireEvent.click(actions[1]);
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Set as Default")).toBeInTheDocument();
    expect(within(menu).getByText("Restore Version")).toBeInTheDocument();
  });
});
