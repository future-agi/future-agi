import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const amendMutate = vi.hoisted(() => vi.fn());
const sendMessage = vi.hoisted(() => vi.fn());
vi.mock("src/api/harness/harness", async () => ({
  ...(await vi.importActual("src/api/harness/harness")),
  sendHarnessConversationMessage: (...args) => sendMessage(...args),
  harnessIdempotencyKey: () => "req-1",
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: { name: "Reviewer One" } }) }));
vi.mock("src/api/harness/scenarios", () => ({
  useAmendScenarios: () => ({ mutate: amendMutate, isPending: false }),
  useScenarioRecords: (jobId, key, table) => ({
    data: table ? { table, rows: [{ id: "RX-1001" }, { id: "RX-1002" }], available: true } : undefined,
  }),
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
const discardDraft = vi.hoisted(() => vi.fn());
const validateDrafts = vi.hoisted(() => vi.fn());
const saveCandidates = vi.hoisted(() => vi.fn());
const draftsData = vi.hoisted(() => ({ validation: null, candidates: null }));
vi.mock("src/api/simulate-environments/versions", () => ({
  useDrafts: () => ({
    data: { any: true, pieces: ["suite"], suite: { parentId: "s1", revision: 3 }, ...draftsData },
  }),
  useDraftReview: () => ({ data: { review_digest: "digest-1" } }),
  useValidateDrafts: () => ({ mutate: validateDrafts, isPending: false, error: null }),
  useSaveCandidates: () => ({ mutate: saveCandidates, isPending: false }),
  useSaveDrafts: () => ({ mutate: saveDrafts, isPending: false }),
  useDiscardDraft: () => ({ mutate: discardDraft, isPending: false }),
  useActors: () => ({ data: { knowledge_sources: { records: [{ store: "db", collection: "refills" }] } } }),
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

describe("ScenarioDetail unsaved case", () => {
  const lifecycle = {
    state: "draft",
    reason: "in_draft",
    reason_text: "Unsaved change; validate and save to use it.",
    sources: [],
    proof: null,
  };

  it("validates an unsaved case with the unsaved changes, never the saved versions", async () => {
    const user = userEvent.setup();
    render(<ScenarioDetailBody jobId="env-1" row={{ id: "r1", name: "Refund", scenarioKey: "refund", lifecycle }} />);

    expect(screen.queryByRole("button", { name: "Validate scenario against the environment" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Validate with the unsaved changes" }));
    expect(validateDrafts).toHaveBeenCalledWith({
      expectedRevisions: { suite: 3 },
      expectedReviewDigest: "digest-1",
    });
  });

  it("says where the validation of the unsaved changes stands", () => {
    draftsData.validation = { state: "failed", affectedScenarios: ["refund"] };
    render(<ScenarioDetailBody jobId="env-1" row={{ id: "r1", name: "Refund", scenarioKey: "refund", lifecycle }} />);
    expect(screen.getByTestId("lifecycle-validate-drafts")).toHaveTextContent("Validation failed. 1 scenario needs a change");
    draftsData.validation = null;
  });
});

describe("ScenarioDetail Activate after validating an unsaved case", () => {
  it("names and saves the validated candidates from the drawer", async () => {
    const user = userEvent.setup();
    saveDrafts.mockClear();
    saveCandidates.mockClear();
    draftsData.validation = { state: "ready", affectedScenarios: [] };
    draftsData.candidates = { environmentVersionId: "e2", suiteVersionId: "s2" };
    const lifecycle = { state: "draft", reason: "in_draft", sources: [], proof: null };
    render(<ScenarioDetailBody jobId="env-1" row={{ id: "r1", name: "Refund", scenarioKey: "refund", lifecycle }} />);

    const drawer = within(screen.getByTestId("lifecycle-validate-drafts"));
    await user.type(drawer.getByRole("textbox", { name: "Name this version" }), "with refunds");
    await user.click(drawer.getByRole("button", { name: "Activate" }));

    expect(saveCandidates).toHaveBeenCalledWith({
      candidates: { environmentVersionId: "e2", suiteVersionId: "s2" },
      expectedReviewDigest: "digest-1",
      name: "with refunds",
    });
    expect(saveDrafts).not.toHaveBeenCalled();
    draftsData.validation = null;
    draftsData.candidates = null;
  });
});

describe("ScenarioDetail Activate of validated changes", () => {
  it("saves the validated candidates under the name given", async () => {
    const user = userEvent.setup();
    saveDrafts.mockClear();
    draftsData.validation = { state: "ready", affectedScenarios: [] };
    draftsData.candidates = { environmentVersionId: "e2", suiteVersionId: "s2" };
    render(
      <ScenarioDetailBody
        jobId="env-1"
        row={{ id: "r1", name: "Refund", lifecycle: { state: "validated", reason: null, sources: [], proof: null } }}
      />,
    );

    expect(screen.getByRole("button", { name: "Activate" })).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Name this version" }), "with refunds");
    await user.click(screen.getByRole("button", { name: "Activate" }));

    expect(saveCandidates).toHaveBeenCalledWith({
      candidates: { environmentVersionId: "e2", suiteVersionId: "s2" },
      expectedReviewDigest: "digest-1",
      name: "with refunds",
    });
    expect(saveDrafts).not.toHaveBeenCalled();
    draftsData.validation = null;
    draftsData.candidates = null;
  });
});

describe("ScenarioDetail missing record", () => {
  const lifecycle = { state: "draft", reason: "not_ready", reason_text: "RX-99999 is not in the world", sources: [], proof: null };
  const open = () =>
    render(<ScenarioDetailBody jobId="env-1" row={{ id: "r1", name: "refill", scenarioKey: "refill", lifecycle }} />);

  it("uses a real record through the builder chat", async () => {
    const user = userEvent.setup();
    open();
    expect(screen.getByTestId("record-choice")).toHaveTextContent("This case presumes a record the world does not hold.");
    await user.click(screen.getByLabelText("Table"));
    await user.click(await screen.findByRole("option", { name: "refills" }));
    await user.click(screen.getByLabelText("Record"));
    await user.click(await screen.findByRole("option", { name: "RX-1002" }));
    await user.click(screen.getByRole("button", { name: "Use a real record" }));
    expect(sendMessage).toHaveBeenCalledWith("env-1", {
      content:
        "Change scenario refill to use refills record RX-1002 instead of the record it presumes that the world does not hold.",
      client_request_id: "req-1",
      kind: "user_message",
      payload: {},
    });
  });

  it("approves a synthetic record as an assumption source", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByLabelText("Table"));
    await user.click(await screen.findByRole("option", { name: "refills" }));
    await user.type(screen.getByLabelText("Synthetic record id"), "RX-99999");
    await user.click(screen.getByRole("button", { name: "Approve as a synthetic record" }));
    expect(amendMutate).toHaveBeenCalledWith({
      rework: false,
      changes: [
        {
          op: "set_field",
          scenario: "refill",
          field: "sources",
          value: [
            {
              kind: "assumption",
              ref: "synthetic:refills/RX-99999",
              quote: "Synthetic record approved by Reviewer One",
              supports: "refills record RX-99999",
            },
          ],
        },
      ],
    });
  });
});

describe("ScenarioDetail failed change", () => {
  it("offers to discard a change the builder could not apply", async () => {
    const user = userEvent.setup();
    render(
      <ScenarioDetailBody
        jobId="env-1"
        row={{
          id: "r1",
          name: "refund",
          lifecycle: { state: "draft", reason: "apply_failed", reason_text: "This change could not be applied: bad check.", sources: [], proof: null },
        }}
      />,
    );
    await user.click(screen.getByTestId("discard-failed-change"));
    expect(discardDraft).toHaveBeenCalledWith(["suite"]);
  });
});
