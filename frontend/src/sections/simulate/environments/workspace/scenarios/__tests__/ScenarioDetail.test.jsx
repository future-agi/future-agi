import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

vi.mock("src/api/harness/scenarios", () => ({
  useValidateScenarios: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScenarioValidation: () => ({ data: undefined }),
  useScenarioRevisions: () => ({
    data: {
      revisions: [
        { revision: 2, digest: "d2", suite_versions: [3, 4], changed_fields: ["tests"] },
        { revision: 1, digest: "d1", suite_versions: [1], changed_fields: [] },
      ],
    },
  }),
}));

const saveDrafts = vi.hoisted(() => vi.fn());
vi.mock("src/api/simulate-environments/versions", () => ({
  useDrafts: () => ({
    data: { any: true, pieces: ["suite"], suite: { parentId: "s1", revision: 3 } },
  }),
  useSaveDrafts: () => ({ mutate: saveDrafts, isPending: false }),
}));

import ScenarioDetail, { ScenarioDetailBody } from "../ScenarioDetail";

const row = (subTasks) => ({ id: "sc-1", name: "Refund request", task: "Ask for a refund.", subTasks });

describe("ScenarioDetail sub-goals", () => {
  // The scenarios list endpoint sends sub_goals as plain name strings.
  it("shows each sub-goal's text when the list sends plain strings", () => {
    render(<ScenarioDetail row={row(["identity_verified", "refund_created"])} defaultOpen />);
    expect(screen.getByText("Sub-goals: the moves that settle it (2)")).toBeInTheDocument();
    expect(screen.getByText("identity_verified")).toBeInTheDocument();
    expect(screen.getByText("refund_created")).toBeInTheDocument();
  });

  it("shows each sub-goal's label when they arrive as objects", () => {
    render(<ScenarioDetail row={row([{ id: "a", label: "Verify identity" }])} defaultOpen />);
    expect(screen.getByText("Verify identity")).toBeInTheDocument();
  });

  it("skips empty sub-goals rather than numbering a blank line", () => {
    render(<ScenarioDetail row={row(["identity_verified", "", null])} defaultOpen />);
    expect(screen.getByText("Sub-goals: the moves that settle it (1)")).toBeInTheDocument();
  });
});

describe("ScenarioDetail persona", () => {
  it("lists the persona facts the API serves and skips the empty ones", () => {
    render(
      <ScenarioDetail
        defaultOpen
        row={{
          ...row([]),
          persona: {
            name: "Alex Morgan",
            ageGroup: "25-32",
            occupation: "Marketing lead",
            location: "Austin",
            personality: "Direct",
            communicationStyle: "Brief",
            languages: ["English", "Spanish"],
            accent: null,
          },
        }}
      />,
    );
    expect(screen.getByText("Marketing lead")).toBeInTheDocument();
    expect(screen.getByText("Austin")).toBeInTheDocument();
    expect(screen.getByText("Direct")).toBeInTheDocument();
    expect(screen.getByText("Brief")).toBeInTheDocument();
    expect(screen.getByText("English, Spanish")).toBeInTheDocument();
    expect(screen.queryByText("Accent")).toBeNull();
    expect(screen.queryByText("Gender")).toBeNull();
  });
});

describe("ScenarioDetailBody", () => {
  it("shows the branch once when the category says the same", () => {
    render(<ScenarioDetailBody row={{ ...row([]), branchCategory: "Price objection", conversationBranch: "Price objection" }} />);
    expect(screen.queryByText("Category")).toBeNull();
    expect(screen.getByText("Branch")).toBeInTheDocument();
  });
});

describe("ScenarioDetailBody — empty scenario", () => {
  it("leaves out sections with nothing in them", () => {
    render(<ScenarioDetailBody row={{ id: "x", name: "orphan", subTasks: [] }} />);
    expect(screen.queryByText("What this tests")).toBeNull();
    expect(screen.queryByText("The person is told")).toBeNull();
  });
});

describe("ScenarioDetail lifecycle", () => {
  const lifecycle = {
    state: "draft",
    reason: "negative_control_passed",
    reason_text: "The checks pass a wrong behaviour: two_refunds (refunds twice). Tighten refund_once.",
    sources: [
      { kind: "rule", ref: "rule:3", quote: "Refund at most once.", label: "Refund at most once.", resolved: true },
      { kind: "assumption", ref: "", quote: "", label: "Assumption", resolved: true },
    ],
    proof: { controls: [{ name: "two_refunds", why: "refunds twice", failed: false, assumed: [] }] },
  };

  it("shows the status, sources, controls and revisions", () => {
    render(
      <ScenarioDetailBody
        jobId="env-1"
        row={{ id: "r1", name: "Refund", scenarioKey: "refund", lifecycle }}
      />,
    );
    expect(screen.getByTestId("lifecycle-status")).toHaveTextContent(lifecycle.reason_text);
    expect(screen.getByTestId("lifecycle-sources")).toHaveTextContent("Refund at most once.");
    expect(screen.getByText("Assumption")).toBeInTheDocument();
    expect(screen.getByTestId("lifecycle-controls")).toHaveTextContent("Passed");
    expect(screen.getByTestId("lifecycle-revisions")).toHaveTextContent("Revision 2");
  });

  it("says a case with no sources was assumed by the builder", () => {
    render(<ScenarioDetailBody row={{ id: "r1", name: "Refund", lifecycle: { ...lifecycle, sources: [] } }} />);
    expect(screen.getByText("No source recorded (written before sources were required).")).toBeInTheDocument();
  });
});

describe("ScenarioDetail source changes", () => {
  it("names the environment version a cited source changed in", () => {
    const lifecycle = {
      state: "draft",
      reason: "not_validated",
      reason_text: "Not validated on these versions yet.",
      sources: [],
      source_changed: ["rule:1"],
      source_changed_version: 4,
    };
    render(<ScenarioDetailBody row={{ id: "r1", name: "Refund", lifecycle }} />);
    expect(screen.getByTestId("lifecycle-source-changed")).toHaveTextContent("Source changed in environment v4: rule:1");
  });
});

describe("ScenarioDetail Activate", () => {
  const base = { reason: null, reason_text: null, sources: [], proof: null };

  it("shows Activate only for a Validated case and saves the versions it was proved on", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <ScenarioDetailBody jobId="env-1" row={{ id: "r1", name: "Refund", lifecycle: { ...base, state: "validated" } }} />,
    );
    expect(screen.getByTestId("lifecycle-activate")).toHaveTextContent(
      "Activating saves these versions. It does not start a run.",
    );
    await user.click(screen.getByRole("button", { name: "Activate" }));
    expect(saveDrafts).toHaveBeenCalledWith([{ piece: "suite", parentId: "s1", revision: 3 }]);

    for (const state of ["active", "draft", "kept_as_written", "archived"]) {
      rerender(<ScenarioDetailBody jobId="env-1" row={{ id: "r1", name: "Refund", lifecycle: { ...base, state } }} />);
      expect(screen.queryByTestId("lifecycle-activate")).toBeNull();
    }
  });
});
