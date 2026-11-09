import React from "react";
import { createHash } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import WhereItRuns, { whereLine } from "../WhereItRuns";
import { EXECUTION_MODES, catalogEntry } from "../catalog";
import R4RunCard, {
  r4CardLine,
  r4CardSummary,
} from "src/sections/develop-detail/Experiment/R4RunCard";

// Copied programmatically from activation-005-rl1915-cs-at-go,
// row SBXCS-FA-RUNSOURCES-01-RL1915; verified against the row before commit.
const WITHHELD = "Future AGI sandbox: Coming soon";
const WITHHELD_SHA256 =
  "aeb3bf326a660d9662cd2adf27a126395d1849ef3f3f52695a2cada064e81859";
const LIVE = "Runs in the Future AGI sandbox";
const state = vi.hoisted(() => ({ capability: {}, r4: {} }));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { OMEGA_SANDBOXES: "omega_sandboxes" },
  useFeatureAllowed: vi.fn(() => state.capability),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/common/ux/Term", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: () => {},
}));
vi.mock("src/sections/common/RunSources/SourceLogo", () => ({
  default: () => null,
}));
vi.mock("react-hook-form", () => ({ useWatch: () => 2 }));
vi.mock("src/sections/develop-detail/Experiment/cardWatch-g2", () => ({
  useCardValue: (_control, _prefix, path) =>
    path === "r4" ? state.r4 : "Retained run",
}));
vi.mock("src/sections/develop-detail/Experiment/ExperimentFormContext", () => ({
  useExperimentForm: () => ({ exampleCount: 3 }),
}));
vi.mock("src/sections/develop-detail/Experiment/RunCardShell", () => ({
  default: ({ title, children, actions }) => (
    <section>
      <h2>{title}</h2>
      {children}
      {actions}
    </section>
  ),
}));

beforeEach(() => {
  state.capability = {};
  state.r4 = { entryId: "langgraph", mode: "sandbox" };
  vi.clearAllMocks();
});
afterEach(cleanup);

const OTHER_LINES = [
  ["platform", "Runs in Future AGI"],
  ["local", "Runs on this machine"],
  ["ci", "Runs in CI"],
  ["remote", "Runs at a remote endpoint"],
];
const CAPABILITY_STATES = [
  ["not allowed", { allowed: false, isLoading: false }, WITHHELD],
  ["loading", { allowed: false, isLoading: true }, WITHHELD],
  [
    "loading despite cached allow",
    { allowed: true, isLoading: true },
    WITHHELD,
  ],
  ["absent", {}, WITHHELD],
  ["error", { allowed: true, isError: true }, WITHHELD],
  ["allowed", { allowed: true, isLoading: false, isError: false }, LIVE],
];

describe("sandbox whereLine capability contract", () => {
  it("pins the activated withheld bytes and untouched LIVE catalog line", () => {
    expect(createHash("sha256").update(WITHHELD).digest("hex")).toBe(
      WITHHELD_SHA256,
    );
    expect(EXECUTION_MODES.find((m) => m.id === "sandbox").short).toBe(LIVE);
  });

  it.each([false, undefined, null, "true", 1])(
    "fails closed for sandboxAllowed=%s",
    (sandboxAllowed) => {
      expect(whereLine("sandbox", "langgraph", null, { sandboxAllowed })).toBe(
        WITHHELD,
      );
    },
  );

  it("defaults to withheld when the capability/options argument is absent", () => {
    expect(whereLine("sandbox")).toBe(WITHHELD);
    expect(whereLine("sandbox", "langgraph", null, {})).toBe(WITHHELD);
    expect(WITHHELD).not.toContain("Runs in");
  });

  it("allows LIVE only with an explicit true capability", () => {
    expect(
      whereLine("sandbox", "langgraph", null, { sandboxAllowed: true }),
    ).toBe(LIVE);
  });

  it.each(OTHER_LINES)("leaves %s unchanged in both states", (mode, line) => {
    for (const sandboxAllowed of [false, true]) {
      expect(whereLine(mode, "langgraph", null, { sandboxAllowed })).toBe(line);
    }
  });

  it("preserves entry-specific remote wording in both states", () => {
    for (const sandboxAllowed of [false, true]) {
      expect(
        whereLine("remote", { remoteLabel: "Custom endpoint" }, null, {
          sandboxAllowed,
        }),
      ).toBe("Runs as a custom endpoint");
    }
  });

  it.each(["langgraph", "codex_cli", "http"])(
    "changes only the location in %s card helpers (including fallback)",
    (entryId) => {
      const r4 = {
        entryId,
        mode: "sandbox",
        form: "connect",
        machine: { name: "Kept machine" },
        limits: { maxCostUsd: 3 },
        slot: { harness: { model: "retained-model", max_cost_usd: 0.02 } },
      };
      for (const helper of [r4CardLine, r4CardSummary]) {
        const withheld = helper(r4, { tries: 6, sandboxAllowed: false });
        const live = helper(r4, { tries: 6, sandboxAllowed: true });
        expect(withheld).toContain(WITHHELD);
        expect(withheld).not.toContain(LIVE);
        expect(helper(r4, { tries: 6 })).toBe(withheld);
        expect(live).toBe(withheld.replace(WITHHELD, LIVE));
      }
      expect(r4CardLine(r4)).toContain(catalogEntry(entryId).name);
      expect(r4CardLine(r4)).toContain("Kept machine");
    },
  );
});

describe("sandbox callers read workspace capabilities", () => {
  it.each(CAPABILITY_STATES)(
    "WhereItRuns: %s",
    (_name, capability, expected) => {
      state.capability = capability;
      render(
        <WhereItRuns
          kind="code"
          entryId="langgraph"
          mode="sandbox"
          onChange={vi.fn()}
        />,
      );
      expect(screen.getByText(expected, { exact: true })).toBeInTheDocument();
      expect(
        screen.queryByText(expected === LIVE ? WITHHELD : LIVE),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("where-change")).toBeInTheDocument();
      expect(useFeatureAllowed).toHaveBeenCalledWith(
        CAPABILITY.OMEGA_SANDBOXES,
      );
    },
  );

  it.each(CAPABILITY_STATES)("R4RunCard: %s", (_name, capability, expected) => {
    state.capability = capability;
    render(
      <R4RunCard control={{}} fieldPrefix="promptConfig.0" onEdit={vi.fn()} />,
    );
    expect(screen.getByTestId("r4-run-card").textContent).toBe(
      `Model and tools are read from your first trace · ${expected}`,
    );
    expect(
      screen.getByRole("heading", { name: "Retained run" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("r4-card-edit")).toBeInTheDocument();
    expect(useFeatureAllowed).toHaveBeenCalledWith(CAPABILITY.OMEGA_SANDBOXES);
  });

  it.each(OTHER_LINES)(
    "both callers preserve %s in both states",
    (mode, line) => {
      for (const allowed of [false, true]) {
        state.capability = { allowed, isLoading: false };
        state.r4 = { entryId: "langgraph", mode };
        render(
          <WhereItRuns
            kind="code"
            entryId="langgraph"
            mode={mode}
            onChange={vi.fn()}
          />,
        );
        render(<R4RunCard control={{}} fieldPrefix="promptConfig.0" />);
        expect(screen.getByText(line, { exact: true })).toBeInTheDocument();
        expect(screen.getByTestId("r4-run-card").textContent).toBe(
          `Model and tools are read from your first trace · ${line}`,
        );
        cleanup();
      }
    },
  );
});
