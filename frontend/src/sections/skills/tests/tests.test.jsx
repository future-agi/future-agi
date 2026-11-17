import { webcrypto } from "node:crypto";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TestsTab from "./TestsTab";
import TestCaseEditor from "./TestCaseEditor";
import TestRunSetup from "./TestRunSetup";
import TestRunResults from "./TestRunResults";
import TestAttemptDrawer from "./TestAttemptDrawer";
import TestEvidence, { evidenceSummary } from "./TestEvidence";
import EditorTestsPanel from "./EditorTestsPanel";
import TestInventoryReview, { centsUp, providerName, sendSentence } from "./TestInventoryReview";
import { RETIRED_TERMS } from "../utils/terms";

const api = vi.hoisted(() => ({
  scope: {},
  suite: {},
  options: {},
  run: {},
  results: {},
  evidence: {},
  attempt: {},
  file: {},
  draft: {},
  skills: {},
  preview: vi.fn(),
  create: vi.fn(),
  saveSuite: vi.fn(),
  cancel: vi.fn(),
  share: vi.fn(),
  refetch: vi.fn(),
  resultsHook: vi.fn(),
  attemptHook: vi.fn(),
  fileHook: vi.fn(),
  runHook: vi.fn(),
}));
vi.mock("src/api/skills", () => ({
  useSkill: () => ({ data: api.skill }),
  useSkillDraft: () => api.draft,
  useSkillsList: () => api.skills,
  getSkillErrorCode: (error) => error?.response?.data?.code ?? error?.code,
}));
vi.mock("src/api/skills/r3-spend", () => ({
  useSkillsSpendPolicy: () => api.spend ?? {},
}));
// A test can stand in an admin (TST6); every other test uses the real access rules.
const accessOverride = vi.hoisted(() => ({ value: null }));
vi.mock("../utils/access", async (original) => {
  const actual = await original();
  return { ...actual, useSkillsAccess: () => { const real = actual.useSkillsAccess(); return accessOverride.value ?? real; } };
});
vi.mock("src/api/skills/testing", () => ({
  useSkillV11Scope: () => api.scope,
  useSkillTestSuite: () => ({ ...api.suite, refetch: api.refetch }),
  useSaveSkillTestSuite: () => ({ mutateAsync: api.saveSuite }),
  useSkillTestingOptions: () => ({ ...api.options, refetch: api.refetch }),
  usePreviewSkillTestRun: () => ({ mutateAsync: api.preview }),
  useCreateSkillTestRun: () => ({ mutateAsync: api.create }),
  useSkillTestRuns: () => ({ data: { results: [], totalPages: 1 } }),
  useSkillTestRun: (...args) => {
    api.runHook(...args);
    return { ...api.run, refetch: api.refetch };
  },
  useSkillTestResults: (...args) => {
    api.resultsHook(...args);
    return { ...api.results, refetch: api.refetch };
  },
  useSkillTestAttempt: (...args) => {
    api.attemptHook(...args);
    return { ...api.attempt, refetch: api.refetch };
  },
  useSkillTestFile: (...args) => {
    api.fileHook(...args);
    return { ...api.file, refetch: api.refetch };
  },
  useCancelSkillTestRun: () => ({ mutateAsync: api.cancel }),
  useShareSkillTestRun: () => ({ mutateAsync: api.share }),
  useSkillTestEvidence: () => ({ ...api.evidence, refetch: api.refetch }),
  isActiveSkillJob: (status) =>
    ["queued", "running", "cancelling"].includes(status),
}));

const ids = {
  skill: "11111111-1111-4111-8111-111111111111",
  competitor: "22222222-2222-4222-8222-222222222222",
  case: "33333333-3333-4333-8333-333333333333",
  run: "44444444-4444-4444-8444-444444444444",
  attempt: "55555555-5555-4555-8555-555555555555",
};
const currentness = {
  content: true,
  baseline: true,
  suite: true,
  competitors: true,
  payloadAvailable: true,
};
const usage = {
  knownInputTokens: null,
  knownOutputTokens: null,
  knownTotalTokens: null,
  unknownCalls: 1,
  complete: false,
};
const cost = {
  knownEstimateUsd: null,
  reservedEstimateUsd: "0.20",
  unknownCalls: 1,
  complete: false,
  pricingSource: "recorded-fixture",
};
const metrics = {
  taskUsage: usage,
  judgeUsage: usage,
  taskCost: cost,
  judgeCost: cost,
  elapsedMs: 1000,
  providerDurationMs: 800,
};
const savedDraft = {
  files: [{ path: "SKILL.md", content: "Synthetic instructions" }],
  revision: 7,
  note: "",
  baseVersion: 2,
};
const testCase = {
  id: ids.case,
  sourceGroupId: "refund-boundary",
  title: "Eligible refund",
  prompt: "Is this supplied order eligible for a refund?",
  fixtures: [],
  expectedTrigger: true,
  checks: [
    {
      id: "check-1",
      kind: "json_equals",
      critical: true,
      config: { pointer: "/eligible", value: true },
    },
  ],
  reviewed: true,
  exposedToAssist: false,
};
const skill = {
  id: ids.skill,
  name: "refund-policy",
  hasDraft: true,
  latest: { number: 3, digest: "sha256:version" },
  labels: [{ name: "production", version: 2 }],
  permissions: { canRunTests: true },
};
const profile = {
  maxCases: 3,
  maxVariants: 2,
  repetitions: 1,
  maxAttempts: 6,
  deadlineSeconds: 300,
  maxTaskTurnsPerAttempt: 6,
  maxToolCallsPerAttempt: 12,
  taskPhaseSeconds: 90,
  judgePhaseSeconds: 30,
  providerCallSeconds: 45,
  maxTaskOutputTokens: 1536,
  maxJudgeOutputTokens: 2048,
  maxDeliveredBytesPerAttempt: 196608,
  maxTotalTokens: 150000,
};
const options = {
  enabled: true,
  assistEnabled: true,
  models: [
    {
      modelId: "fixture-task",
      provider: "fixture",
      canRun: true,
      configured: true,
      toolSupport: "supported",
      pricingStatus: "known",
    },
    { modelId: "fixture-judge", provider: "fixture", canRun: true },
  ],
  limits: {
    profiles: {
      quick: profile,
      repeated: {
        ...profile,
        maxCases: 20,
        maxVariants: 3,
        repetitions: 3,
        maxAttempts: 180,
        maxTotalTokens: 1000000,
        maxTaskOutputTokens: 2048,
        deadlineSeconds: 1800,
      },
    },
    maxCompetitors: 5,
    maxActiveAttemptsPerRun: 2,
    maxActiveProviderCallsPerOrganization: 4,
    maxActiveModelJobsPerOrganization: 2,
    maxQueueWaitSeconds: 60,
    maxEstimatedCostUsd: "25.00000000",
  },
};
const preview = {
  fingerprint: "plan-fingerprint",
  caseCount: 1,
  variantCount: 2,
  repetitions: 1,
  attemptCount: 2,
  maxTaskCalls: 12,
  maxJudgeCalls: 0,
  estimate: { upperBoundUsd: "0.10", pricingSource: "fixture-prices" },
  providerDisclosure: { taskProvider: "fixture", judgeProvider: null },
  limits: options.limits,
  warnings: [],
  inventory: [
    {
      skillId: ids.skill,
      digest: "candidate-digest",
      files: [
        { path: "SKILL.md", sha256: "root-hash", sizeBytes: 22, flags: [] },
      ],
    },
  ],
  outboundFindings: [],
};
const frozenConfig = {
  model: {
    modelId: "historical-task-2025",
    temperature: 0.3,
    maxOutputTokens: 1536,
  },
  judge: {
    modelId: "historical-judge-2025",
    temperature: 0,
    maxOutputTokens: 2048,
  },
  harnessVersion: "harness-recorded",
  adapterVersion: "adapter-recorded",
  evaluatorVersion: "evaluator-recorded",
};
const run = {
  id: ids.run,
  status: "completed",
  profile: "quick",
  visibility: "private",
  createdAt: "2026-11-06T12:00:00Z",
  payloadExpiresAt: "2026-12-06T12:00:00Z",
  payloadExpired: false,
  candidate: { draftRevision: 7, version: null, digest: "candidate-digest" },
  production: { version: 2 },
  competitors: [],
  selectedCaseIds: [ids.case],
  repetitions: 1,
  fullSuite: true,
  counts: {
    planned: 2,
    finished: 2,
    answered: 2,
    graded: 2,
    errors: 0,
    incomplete: 0,
    notStarted: 0,
  },
  decision: {
    state: "works_on_examples",
    improvedCases: 0,
    regressedCases: 0,
    unchangedCases: 1,
    unmatchedCases: 0,
  },
  answerComparisons: [
    {
      baseline: "without_target",
      matchedCases: 1,
      expectedCases: 1,
      deltaPercentagePoints: 0,
    },
  ],
  validity: "reviewed",
  validityReasons: [],
  currentness,
  executionConfiguration: frozenConfig,
  metrics,
  error: null,
};
const caseResult = {
  caseId: ids.case,
  title: "Eligible refund",
  expectedTrigger: true,
  change: "unchanged",
  variants: ["candidate", "without_target"].map((variant) => ({
    variant,
    planned: 1,
    completed: 1,
    loaded: variant === "candidate" ? 1 : 0,
    answerPassed: 1,
    answerGraded: 1,
    deliveryPassed: variant === "candidate" ? 1 : 0,
    inconsistent: false,
    attempts: [
      {
        id: variant === "candidate" ? ids.attempt : "control-attempt",
        repeatIndex: 0,
        status: "completed",
        routingVerdict: variant === "candidate" ? "pass" : "not_applicable",
        answerVerdict: "pass",
        deliveryVerdict: variant === "candidate" ? "pass" : "not_applicable",
      },
    ],
  })),
};
const receipt = {
  callId: "call-1",
  toolCallId: "tool-1",
  skillId: ids.skill,
  versionId: null,
  digest: "candidate-digest",
  path: "policy.md",
  sha256: "file-hash",
  offset: 0,
  nextOffset: null,
  totalChars: 12,
  eof: true,
  deliveredBytes: 12,
  outcome: "delivered",
  errorCode: null,
};
const attempt = {
  id: ids.attempt,
  case: testCase,
  variant: "candidate",
  repeatIndex: 0,
  status: "completed",
  answer: '{"eligible":true}',
  targetRequested: true,
  targetLoaded: true,
  rootFullyDelivered: false,
  checks: [
    {
      checkId: "check-1",
      category: "answer",
      verdict: "pass",
      expected: true,
      actual: true,
      evidence: '"eligible":true',
      reason: null,
    },
  ],
  receipts: [receipt],
  calls: [
    {
      id: "call-1",
      sequence: 1,
      kind: "task",
      state: "unknown",
      requestedModel: "requested-recorded",
      returnedModel: "returned-recorded",
      assistantMessage: { role: "assistant", content: "Answer" },
      finishReason: null,
      usage,
      cost,
      latencyMs: null,
      error: null,
    },
  ],
  metrics,
  error: null,
};
const evidenceRow = {
  runId: ids.run,
  status: "completed",
  visibility: "private",
  currentness,
  validity: "reviewed",
  selectedCases: 1,
  suiteCases: 3,
  repetitions: 1,
  answerRegressions: 0,
  routingFailures: 0,
  deliveryFailures: 0,
  errorAttempts: 0,
  createdAt: run.createdAt,
  canAttach: false,
};
const mount = (element, entry = "/dashboard/skills/skill?tab=tests") =>
  render(<MemoryRouter initialEntries={[entry]}>{element}</MemoryRouter>);
const choose = async (label, name) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(await screen.findByRole("option", { name }));
};
const configure = async () => {
  fireEvent.click(screen.getByText("Model parameters and token stop"));
  fireEvent.click(
    screen.getByText("0 selected competitors · manage comparison context"),
  );
  await choose("Task model", "fixture-task");
  fireEvent.change(screen.getByLabelText("Estimated-cost stop (USD)"), {
    target: { value: "1.00" },
  });
};
// UXF-03: the two file statements and provider consent are one tick.
const SEND = /^Send (this file|these \d+ files) and 1 test case to Fixture, up to \$\d+\.\d{2}\. (It contains|They contain) no answer keys or grading material\.$/;
const reviewPlan = async () => {
  fireEvent.click(
    screen.getByRole("button", { name: "Preview work and spend" }),
  );
  await screen.findByText("Confirm the reviewed plan");
  fireEvent.click(screen.getByLabelText(SEND));
};
// The visible sub-tab panel (Results, Run a test or Cases).
const openTab = (name) => fireEvent.click(screen.getByRole("tab", { name }));
const visiblePanel = () => screen.getByRole("tabpanel");
const setup = (props = {}) => (
  <TestRunSetup
    skill={api.skill}
    draft={savedDraft}
    suite={api.suite.data}
    caseIds={[ids.case]}
    onStarted={vi.fn()}
    {...props}
  />
);

beforeEach(() => {
  api.spend = undefined;
  vi.clearAllMocks();
  Object.defineProperty(globalThis.crypto, "subtle", {
    configurable: true,
    value: webcrypto.subtle,
  });
  api.scope = {
    identity: "org:workspace:user",
    enabled: true,
    scope: { workspaceId: "workspace" },
  };
  api.skill = structuredClone(skill);
  api.suite = { data: { revision: 4, cases: [structuredClone(testCase)] } };
  api.options = { data: structuredClone(options) };
  api.run = { data: structuredClone(run) };
  api.results = {
    data: { results: [structuredClone(caseResult)], page: 1, totalPages: 1 },
  };
  api.evidence = {
    data: {
      candidateDigest: "candidate-digest",
      currentReleaseRevision: 7,
      runs: [structuredClone(evidenceRow)],
    },
  };
  api.attempt = { data: structuredClone(attempt) };
  api.file = {
    data: { ...receipt, sizeBytes: 12, text: "Policy text.", eof: true },
  };
  api.draft = { data: structuredClone(savedDraft) };
  api.skills = {
    data: {
      results: [
        structuredClone(skill),
        {
          id: ids.competitor,
          name: "nearby-policy",
          labels: [{ name: "production", version: 4 }],
        },
      ],
      totalPages: 1,
    },
  };
  api.preview.mockResolvedValue(structuredClone(preview));
  api.create.mockResolvedValue({ id: ids.run, status: "queued" });
  api.share.mockResolvedValue({ id: ids.run, visibility: "workspace" });
  api.saveSuite.mockResolvedValue(api.suite.data);
  api.refetch.mockResolvedValue({ data: api.suite.data });
});
afterEach(cleanup);

describe("starter templates", () => {
  it("falls back to everyday support requests when nothing matches the skill", async () => {
    const { startersFor } = await import("./starterTemplates");
    expect(startersFor({ name: "tone-guide", description: "Keep replies friendly." }).map((item) => item.title)).toEqual([
      "Customer asks for a refund on a damaged item",
      "Customer asks where their order is",
      "Customer doesn't recognise a charge",
    ]);
    expect(startersFor({ name: "invoice-extractor", description: "Extract JSON fields" }).map((item) => item.title)).toContain("Structured output with a missing value");
  });
});

describe("reviewed examples", () => {
  it("shows the first-run action and starters that match the skill, without paid work", () => {
    api.suite.data.cases = [];
    mount(<TestsTab skill={skill} />);
    // Results is the default and leads to Cases while there is nothing to run.
    expect(screen.getByRole("tab", { name: "Results" })).toHaveAttribute("aria-selected", "true");
    expect(within(visiblePanel()).getByText("No test runs yet")).toBeVisible();
    fireEvent.click(within(visiblePanel()).getByRole("button", { name: "Add test cases" }));
    expect(screen.getByRole("tab", { name: "Cases" })).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByText("Does this skill handle your real requests?"),
    ).toBeInTheDocument();
    const first = screen.getByRole("button", { name: "Add first test case" });
    expect(first).toBeEnabled();
    expect(first).toHaveClass("MuiButton-containedPrimary");
    // Unrelated domains are not offered for a refund skill, and the privacy note waits for a case.
    expect(
      screen.queryByRole("button", { name: "Tenant isolation in a supplied patch" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Saved test cases are visible to your workspace/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Customer asks for a refund on a damaged item" }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Refund policy boundary",
      }),
    );
    expect(screen.getByLabelText("Request").value).toContain("30 days");
    expect(
      screen.getByLabelText(
        "I reviewed the request, supplied facts and expected behavior",
      ),
    ).not.toBeChecked();
    expect(api.preview).not.toHaveBeenCalled();
    expect(api.create).not.toHaveBeenCalled();
  });
  it("clears review on edits and submits exact typed JSON instead of metadata", () => {
    const onSave = vi.fn();
    mount(
      <TestCaseEditor
        open
        initialCase={{
          ...testCase,
          reviewedBy: { id: "other" },
          origin: "assist",
        }}
        onClose={vi.fn()}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByLabelText("Request"), {
      target: { value: "A changed request" },
    });
    expect(
      screen.getByLabelText(
        "I reviewed the request, supplied facts and expected behavior",
      ),
    ).not.toBeChecked();
    fireEvent.change(screen.getByLabelText("Expected JSON value"), {
      target: { value: "null" },
    });
    fireEvent.click(
      screen.getByLabelText(
        "I reviewed the request, supplied facts and expected behavior",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save example" }));
    expect(onSave.mock.calls[0][0]).toMatchObject({
      reviewed: true,
      checks: [{ config: { pointer: "/eligible", value: null } }],
    });
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("reviewedBy");
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("origin");
  });
  it("chooses the required skill and business action instead of typing the reserved fixture", () => {
    const onSave = vi.fn();
    mount(
      <TestCaseEditor
        open
        initialCase={{ ...testCase, fixtures: [] }}
        applicationBehavior={{
          skills: [
            { id: "4e7d55e9-7da5-4a2b-94bc-b19f63257709", label: "skills/refund" },
            { id: "8acacf60-c238-4a5e-af7d-806d7617c0ee", label: "skills/chargeback" },
          ],
        }}
        onClose={vi.fn()}
        onSave={onSave}
      />,
    );
    expect(screen.queryByLabelText("Fixture 1 name")).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByLabelText("Required skill"));
    fireEvent.click(screen.getByRole("option", { name: "skills/refund" }));
    // A skill without an action is not a complete expectation.
    expect(screen.getByRole("button", { name: "Save example" })).toBeDisabled();
    expect(screen.getByText("Choose both a skill and an action, or leave both as Not specified.")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByLabelText("Required business action"));
    fireEvent.click(screen.getByRole("option", { name: "Issue a refund" }));
    fireEvent.click(
      screen.getByLabelText("I reviewed the request, supplied facts and expected behavior"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save example" }));
    const fixtures = onSave.mock.calls[0][0].fixtures;
    expect(fixtures).toHaveLength(1);
    expect(fixtures[0].name).toBe("fi.application.expectations");
    expect(JSON.parse(fixtures[0].text)).toEqual({
      schema: 1,
      skill_id: "4e7d55e9-7da5-4a2b-94bc-b19f63257709",
      action: "issue_refund",
    });
  });
  it("cannot save malformed JSON using a previously valid assertion", () => {
    mount(
      <TestCaseEditor
        open
        initialCase={testCase}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Expected JSON value"), {
      target: { value: "{bad" },
    });
    expect(screen.getByRole("button", { name: "Save example" })).toBeDisabled();
    expect(
      screen.getByLabelText(
        "I reviewed the request, supplied facts and expected behavior",
      ),
    ).not.toBeChecked();
  });
  it("preserves edited input after suite CAS rejection", async () => {
    api.saveSuite.mockRejectedValue({ code: "test_suite_conflict" });
    mount(<TestsTab skill={skill} />);
    openTab("Cases");
    fireEvent.click(screen.getByLabelText("Actions for Eligible refund"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit test case" }));
    fireEvent.change(screen.getByLabelText("Request"), {
      target: { value: "Keep my unsaved request" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save example" }));
    await screen.findAllByText(/Someone changed the saved test cases/);
    expect(screen.getByLabelText("Request")).toHaveValue(
      "Keep my unsaved request",
    );
    expect(api.saveSuite.mock.calls[0][0].revision).toBe(4);
  });
  it.each(["loading", "error", "empty"])(
    "preserves the %s review state",
    (state) => {
      mount(
        <TestsTab skill={skill} />,
        `/dashboard/skills/skill?tab=tests&state=${state}`,
      );
      expect(api.create).not.toHaveBeenCalled();
      // The tab strip stays in every state; the state shows inside Cases (and Run a test).
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Results", "Run a test", "Cases"]);
      openTab("Cases");
      if (state === "loading")
        expect(
          within(visiblePanel()).getByRole("progressbar", { name: "Loading skill tests" }),
        ).toBeInTheDocument();
      else
        expect(
          within(visiblePanel()).getByText(
            state === "error"
              ? "Could not load saved test cases"
              : "Does this skill handle your real requests?",
          ),
        ).toBeInTheDocument();
    },
  );
  it("clears private editor form state when the principal changes", () => {
    const view = mount(<TestsTab skill={skill} />);
    openTab("Cases");
    fireEvent.click(screen.getByRole("button", { name: "Add test case" }));
    fireEvent.change(screen.getByLabelText("Request"), {
      target: { value: "Private unsaved text" },
    });
    api.scope.identity = "org:other-workspace:other-user";
    view.rerender(
      <MemoryRouter>
        <TestsTab skill={skill} />
      </MemoryRouter>,
    );
    expect(
      screen.queryByDisplayValue("Private unsaved text"),
    ).not.toBeInTheDocument();
  });
});

describe("reviewed run setup", () => {
  it("matches the v1 Python manifest digest for Unicode paths and non-ASCII content", async () => {
    const draft = {
      ...savedDraft,
      files: [
        ...savedDraft.files,
        { path: "references/é-𐀀.md", content: "café" },
        { path: "references/.md", content: "private-use name" },
      ],
    };
    mount(setup({ draft }));
    await configure();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview work and spend" }),
    );
    await waitFor(() => expect(api.preview).toHaveBeenCalled());
    expect(api.preview.mock.calls[0][0].candidate.digest).toBe(
      "sha256:a2a98ab0e8ab9d33bce8c9faea2fe86be631a3013aca45074d2b276562de9b7c",
    );
  });
  it("never substitutes an unconfigured model and links to this workspace’s provider settings", () => {
    api.options.data.models = [];
    mount(setup());
    expect(
      screen.getByRole("link", { name: "Open workspace model settings" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/settings/workspace/workspace/ai-providers",
    );
    expect(
      screen.getByRole("button", { name: "Preview work and spend" }),
    ).toBeDisabled();
    expect(api.preview).not.toHaveBeenCalled();
  });
  it("fails closed for missing server run permission", () => {
    api.skill.permissions = {};
    mount(setup());
    expect(
      screen.getByText("Workspace write access is required to run tests."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Preview work and spend" }),
    ).toBeDisabled();
  });
  it("blocks a reviewed start after permission loss without erasing setup", async () => {
    const view = mount(setup());
    await configure();
    await reviewPlan();
    api.skill = { ...skill, permissions: { canRunTests: false } };
    view.rerender(<MemoryRouter>{setup()}</MemoryRouter>);
    expect(
      screen.getByRole("button", { name: "Run quick check" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Estimated-cost stop (USD)")).toHaveValue(
      "1.00",
    );
    expect(api.create).not.toHaveBeenCalled();
  });
  it("requires an explicit no-target choice and blocks target-as-competitor", async () => {
    api.skill.labels = [];
    mount(setup());
    await configure();
    expect(
      screen.getByRole("button", { name: "Preview work and spend" }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        "No production version yet. Compare without this skill instead.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByLabelText("Compare without this skill · without any skills"),
    );
    fireEvent.mouseDown(screen.getByLabelText("Add published competitor"));
    expect(
      screen.queryByRole("option", { name: /refund-policy/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: "nearby-policy · v4" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Preview work and spend" }),
    );
    await waitFor(() => expect(api.preview).toHaveBeenCalled());
    expect(api.preview.mock.calls[0][0]).toMatchObject({
      baselines: ["without_target"],
      competitors: [{ skillId: ids.competitor, version: 4 }],
    });
  });
  it("binds reviewed start to the plan fingerprint and retries the exact paid request", async () => {
    const onStarted = vi.fn();
    api.create.mockRejectedValueOnce(new Error("Lost response"));
    mount(setup({ onStarted }));
    await configure();
    await reviewPlan();
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await screen.findByRole("button", { name: "Retry same submission" });
    const first = api.create.mock.calls[0][0];
    expect(first).toMatchObject({
      expectedFingerprint: "plan-fingerprint",
      inventoryReview: {
        fingerprint: "plan-fingerprint",
        reviewedAllFiles: true,
        noKnownAnswerKeys: true,
      },
    });
    expect(first.configuration.candidate.revision).toBe(7);
    expect(first.configuration.candidate.digest).toMatch(
      /^sha256:[a-f0-9]{64}$/,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retry same submission" }),
    );
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(api.create.mock.calls[1][0]).toBe(first);
  });
  it("rounds the reviewed plan's ceiling up and keeps its price source in Technical details", async () => {
    api.preview.mockResolvedValue({ ...preview, estimate: { upperBoundUsd: "0.23869440", pricingSource: "fixture-prices" } });
    mount(setup());
    await configure();
    await reviewPlan();
    expect(screen.getByText("$0.24")).toHaveAttribute("title", "Exact: USD 0.23869440");
    expect(screen.getByText("$1")).toHaveAttribute("title", "Exact: USD 1.00");
    expect(screen.queryByText(/fixture-prices/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Technical details" }));
    expect(screen.getByText("Price source: fixture-prices")).toBeInTheDocument();
    expect(screen.getByText(/Upper-bound estimate: USD 0\.23869440/)).toBeInTheDocument();
    // The one send tick names the rounded-up ceiling.
    expect(screen.getByLabelText(SEND)).toHaveAccessibleName(expect.stringContaining("up to $0.24"));
  });
  it("retains the idempotency key after capacity rejection when retrying the same plan", async () => {
    api.create.mockRejectedValueOnce({
      code: "skills_capacity_exceeded",
      response: { status: 429 },
    });
    mount(setup());
    await configure();
    await reviewPlan();
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await screen.findByText(/Workspace model capacity is busy/);
    const first = api.create.mock.calls[0][0];
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2));
    expect(api.create.mock.calls[1][0]).toBe(first);
  });
  it("does not preview or run when the editor flush returns a conflict", async () => {
    const onBeforePreview = vi.fn().mockResolvedValue(null);
    mount(setup({ onBeforePreview }));
    await configure();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview work and spend" }),
    );
    await screen.findByText(/Save your draft and resolve any conflict/);
    expect(onBeforePreview).toHaveBeenCalledTimes(1);
    expect(api.preview).not.toHaveBeenCalled();
    expect(api.create).not.toHaveBeenCalled();
  });
  it("waits for the acknowledged save revision before building the preview", async () => {
    let acknowledge;
    const onBeforePreview = vi.fn(
      () =>
        new Promise((resolve) => {
          acknowledge = resolve;
        }),
    );
    mount(setup({ onBeforePreview }));
    await configure();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview work and spend" }),
    );
    expect(api.preview).not.toHaveBeenCalled();
    await act(async () => acknowledge({ ...savedDraft, revision: 9 }));
    await waitFor(() => expect(api.preview).toHaveBeenCalled());
    expect(api.preview.mock.calls[0][0].candidate.revision).toBe(9);
  });
  it("accepts a flush that persisted edits made while an older save was in flight", async () => {
    let acknowledge;
    const onBeforePreview = vi.fn(
      () =>
        new Promise((resolve) => {
          acknowledge = resolve;
        }),
    );
    const onStarted = vi.fn();
    const view = mount(setup({ onBeforePreview, onStarted }));
    await configure();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview work and spend" }),
    );
    const latest = {
      ...savedDraft,
      revision: 9,
      files: [{ path: "SKILL.md", content: "Edits made during older save" }],
    };
    view.rerender(
      <MemoryRouter>
        {setup({ onBeforePreview, onStarted, draft: latest })}
      </MemoryRouter>,
    );
    await act(async () => acknowledge(latest));
    await screen.findByText("Confirm the reviewed plan");
    expect(api.preview.mock.calls[0][0].candidate.revision).toBe(9);
    expect(
      screen.queryByText("For earlier content. Preview again before starting."),
    ).not.toBeInTheDocument();
  });
  it("marks an already reviewed plan stale immediately on an editor change", async () => {
    const view = mount(setup());
    await configure();
    await reviewPlan();
    view.rerender(
      <MemoryRouter>
        {setup({
          draft: {
            ...savedDraft,
            files: [{ path: "SKILL.md", content: "Changed locally" }],
          },
        })}
      </MemoryRouter>,
    );
    expect(
      screen.getByText("For earlier content. Preview again before starting."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run quick check" }),
    ).toBeDisabled();
  });
  it("requires a separate explicit judge for semantic checks", async () => {
    api.suite.data.cases[0].checks = [
      {
        id: "semantic-1",
        kind: "semantic",
        critical: true,
        config: { requirement: "Use only supplied facts." },
      },
    ];
    mount(setup());
    await configure();
    expect(
      screen.getByRole("button", { name: "Preview work and spend" }),
    ).toBeDisabled();
    await choose(
      "AI judge for plain-language checks",
      "fixture-task · use selected task model as judge",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Preview work and spend" }),
    );
    await waitFor(() => expect(api.preview).toHaveBeenCalled());
    expect(api.preview.mock.calls[0][0].judge).toEqual({
      modelId: "fixture-task",
      temperature: 0,
      maxOutputTokens: 2048,
    });
  });
  it("uses effective deployment ceilings, rather than hard-coded defaults", async () => {
    api.options.data.limits.maxEstimatedCostUsd = "0.50";
    api.options.data.limits.profiles.quick.maxTaskOutputTokens = 512;
    mount(setup());
    await configure();
    expect(screen.getByLabelText("Task output tokens per call")).toHaveValue(
      512,
    );
    expect(
      screen.getByRole("button", { name: "Preview work and spend" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Estimated-cost stop (USD)"), {
      target: { value: "0.25" },
    });
    expect(
      screen.getByRole("button", { name: "Preview work and spend" }),
    ).toBeEnabled();
  });
  it("shows server finding IDs for both bundle and fixture findings and requires reasons", async () => {
    const findings = [
      {
        findingId: "bundle-finding",
        code: "secret_suspected",
        location: { kind: "bundle_file", path: "SKILL.md" },
        explanation: "Redacted bundle match",
      },
      {
        findingId: "fixture-finding",
        code: "secret_suspected",
        location: { kind: "fixture", caseId: ids.case, fixtureId: "fixture-1" },
        explanation: "Redacted fixture match",
      },
    ];
    api.preview.mockResolvedValue({ ...preview, outboundFindings: findings });
    mount(setup());
    await configure();
    await reviewPlan();
    expect(
      screen.getByRole("button", { name: "Run quick check" }),
    ).toBeDisabled();
    screen
      .getAllByLabelText("This is not a real secret")
      .forEach((checkbox) => fireEvent.click(checkbox));
    fireEvent.change(
      screen.getByLabelText("False-positive reason (bundle-finding)"),
      { target: { value: "Synthetic documented placeholder" } },
    );
    expect(
      screen.getByRole("button", { name: "Run quick check" }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByLabelText("False-positive reason (fixture-finding)"),
      { target: { value: "Synthetic fixture identifier" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await waitFor(() => expect(api.create).toHaveBeenCalled());
    expect(
      api.create.mock.calls[0][0].inventoryReview.falsePositiveSecretFindings.map(
        (item) => item.findingId,
      ),
    ).toEqual(["bundle-finding", "fixture-finding"]);
  });
  it("cannot use a reference classification to launder exact overlap into regression evidence", async () => {
    api.preview.mockResolvedValue({
      ...preview,
      inventory: [
        {
          ...preview.inventory[0],
          files: [
            { ...preview.inventory[0].files[0], flags: ["case_overlap"] },
          ],
        },
      ],
    });
    mount(setup());
    await configure();
    await reviewPlan();
    await choose("Classify SKILL.md (root-has)", "Reference material");
    fireEvent.change(
      screen.getByLabelText("Classification reason for SKILL.md (root-has)"),
      { target: { value: "Genuine policy" } },
    );
    expect(
      screen.getByRole("button", { name: "Run quick check" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/Case or expected-answer overlap makes this diagnostic/),
    ).toBeInTheDocument();
  });
  it("classifies every flagged file with one reviewed reason while keeping per-file records", async () => {
    const base = preview.inventory[0].files[0];
    api.preview.mockResolvedValue({
      ...preview,
      inventory: [
        {
          ...preview.inventory[0],
          files: [
            { ...base, path: "benchmark/a.md", sha256: "a".repeat(64), flags: ["evaluation_artifact"] },
            { ...base, path: "benchmark/b.md", sha256: "b".repeat(64), flags: ["evaluation_artifact"] },
            { ...base, path: "benchmark/c.md", sha256: "c".repeat(64), flags: ["evaluation_artifact"] },
          ],
        },
      ],
    });
    mount(setup());
    await configure();
    await reviewPlan();
    const apply = screen.getByRole("button", {
      name: "Apply to all 3 flagged files",
    });
    expect(apply).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Run quick check" }),
    ).toBeDisabled();
    await choose("Classify all 3 flagged files", "Reference material");
    fireEvent.change(screen.getByLabelText("Reason for all 3 flagged files"), {
      target: { value: "  Genuine policy references  " },
    });
    fireEvent.click(apply);
    // Each file keeps its own editable record.
    expect(
      screen.getByLabelText("Classification reason for benchmark/b.md (bbbbbbbb)"),
    ).toHaveValue("Genuine policy references");
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await waitFor(() => expect(api.create).toHaveBeenCalled());
    expect(
      api.create.mock.calls[0][0].inventoryReview.classifications.map(
        ({ path, classification, reason }) => [path, classification, reason],
      ),
    ).toEqual([
      ["benchmark/a.md", "reference_material", "Genuine policy references"],
      ["benchmark/b.md", "reference_material", "Genuine policy references"],
      ["benchmark/c.md", "reference_material", "Genuine policy references"],
    ]);
  });
  it("preserves model and spend inputs but discards a changed preview", async () => {
    api.create.mockRejectedValue({
      code: "test_plan_changed",
      response: { status: 409 },
    });
    mount(setup());
    await configure();
    await reviewPlan();
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await screen.findByText(
      /The draft, cases, baseline or model configuration changed/,
    );
    expect(screen.getByLabelText("Estimated-cost stop (USD)")).toHaveValue(
      "1.00",
    );
    expect(
      screen.queryByText("Confirm the reviewed plan"),
    ).not.toBeInTheDocument();
  });
});

describe("truthful result evidence", () => {
  it("keeps equal correct no-target answers equal while selection and delivery are not applicable", () => {
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} />);
    expect(
      screen.getByText(/0 percentage points \(descriptive answer difference\)/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Answer passed 1/1 · graded 1/1")).toHaveLength(
      2,
    );
    expect(screen.getByText("Selection: Not applicable")).toBeInTheDocument();
    expect(screen.getByText("Delivery: Not applicable")).toBeInTheDocument();
    expect(screen.getAllByText(/historical-task-2025/).length).toBeGreaterThan(
      0,
    );
    expect(screen.queryByText(/fixture-task/)).not.toBeInTheDocument();
  });
  it("includes mixed cases in the initial regressions view and labels both baseline comparisons", () => {
    api.run.data.decision.regressedCases = 1;
    api.run.data.answerComparisons = [
      {
        baseline: "production",
        matchedCases: 1,
        expectedCases: 1,
        deltaPercentagePoints: -100,
      },
      {
        baseline: "without_target",
        matchedCases: 1,
        expectedCases: 1,
        deltaPercentagePoints: 100,
      },
    ];
    api.results.data.results[0].change = "mixed";
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} />);
    expect(api.resultsHook).toHaveBeenCalledWith(
      ids.skill,
      ids.run,
      expect.objectContaining({ filter: "regressions" }),
    );
    expect(screen.getByText("Mixed")).toBeInTheDocument();
    expect(
      screen.getByText(/Against Production v2:.*-100/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Against Without any skills:.*\+100/),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 regressed or mixed/)).toBeInTheDocument();
  });
  it("does not render a diagnostic delta even if a malformed fixture provides one", () => {
    api.run.data.validity = "diagnostic";
    api.run.data.decision.state = "diagnostic";
    api.run.data.answerComparisons[0].deltaPercentagePoints = 100;
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} />);
    expect(screen.getByText(/No answer delta reported/)).toBeInTheDocument();
    expect(screen.queryByText(/100 percentage points/)).not.toBeInTheDocument();
  });
  it("expired evidence keeps frozen model configuration but does not fetch results", () => {
    api.run.data.payloadExpired = true;
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} />);
    expect(api.resultsHook).toHaveBeenCalledWith(
      ids.skill,
      undefined,
      expect.any(Object),
    );
    expect(
      screen.getByText(/Detailed evidence expired. Summary counts/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Task model: historical-task-2025/)).toBeInTheDocument();
    expect(screen.queryByText(/harness-recorded/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test runner versions" }));
    expect(screen.getByText(/Test runner harness-recorded/)).toBeInTheDocument();
  });
  it("shows run costs as money for people and keeps the exact estimate on hover", () => {
    api.run.data.metrics = {
      ...api.run.data.metrics,
      taskCost: { ...cost, knownEstimateUsd: "0.00004770", complete: true, unknownCalls: 0 },
      judgeCost: { ...cost, knownEstimateUsd: "0.00000000", complete: true, unknownCalls: 0 },
    };
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} />);
    expect(screen.getByText("under $0.01")).toHaveAttribute("title", "Exact: USD 0.00004770");
    expect(screen.getByText("$0")).toHaveAttribute("title", "Exact: USD 0.00000000");
    expect(screen.queryByText(/USD 0\.0000/)).not.toBeInTheDocument();
  });
  it("requests stop explicitly and never cancels on unmount", async () => {
    api.run.data.status = "running";
    const view = mount(
      <TestRunResults skillId={ids.skill} runId={ids.run} canWrite />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "2 of 2 attempts finished",
    );
    fireEvent.click(screen.getByRole("button", { name: "Stop", exact: true }));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledTimes(1));
    view.unmount();
    expect(api.cancel).toHaveBeenCalledTimes(1);
  });
  it("selection remains unknown after a provider failure before delivery and renders hostile text safely", () => {
    api.attempt.data = {
      ...attempt,
      answer: '<img src=x onerror="window.attack=true">',
      targetLoaded: null,
      rootFullyDelivered: null,
      error: { code: "provider_timeout", message: "Timed out before delivery" },
      status: "error",
    };
    mount(
      <TestAttemptDrawer
        open
        onClose={vi.fn()}
        skillId={ids.skill}
        runId={ids.run}
        attemptId={ids.attempt}
      />,
    );
    expect(screen.getByText("Selection unknown")).toBeInTheDocument();
    expect(
      screen.queryByText("Target root not delivered"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('<img src=x onerror="window.attack=true">'),
    ).toBeInTheDocument();
    expect(document.querySelector("img[src=x]")).toBeNull();
    expect(
      screen.getByText(
        /Requested model: requested-recorded · Returned model: returned-recorded/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/known estimate: Unknown/)).toBeInTheDocument();
    expect(screen.getByText("$0.20")).toHaveAttribute("title", "Exact: USD 0.20");
    expect(screen.queryByText(/recorded-fixture/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Technical details" }));
    expect(screen.getByText("Price source: recorded-fixture")).toBeInTheDocument();
  });
  it("opens a receipt in the editor while retaining immutable snapshot identity in file reads", () => {
    const onOpenFile = vi.fn();
    mount(
      <TestAttemptDrawer
        open
        onClose={vi.fn()}
        skillId={ids.skill}
        runId={ids.run}
        attemptId={ids.attempt}
        onOpenFile={onOpenFile}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open current file" }));
    expect(onOpenFile).toHaveBeenCalledWith("policy.md");
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect snapshot file" }),
    );
    expect(api.fileHook).toHaveBeenCalledWith(ids.skill, ids.run, {
      snapshotSkillId: ids.skill,
      variant: "candidate",
      path: "policy.md",
      offset: 0,
    });
    expect(screen.getByText("Policy text.")).toBeInTheDocument();
  });
  it("requires a distinct sharing confirmation and never auto-attaches private evidence", async () => {
    const onSelectRun = vi.fn();
    mount(
      <TestEvidence
        skillId={ids.skill}
        candidate={{ kind: "draft", revision: 7 }}
        onSelectRun={onSelectRun}
      />,
    );
    expect(api.share).not.toHaveBeenCalled();
    expect(onSelectRun).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Attach this evidence" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Share evidence" }));
    expect(
      screen.getByText(/Share the frozen draft, example inputs/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Share with workspace" }),
    );
    await waitFor(() =>
      expect(api.share).toHaveBeenCalledExactlyOnceWith({
        acknowledgeSnapshotSharing: true,
      }),
    );
    expect(onSelectRun).not.toHaveBeenCalled();
  });
  it("preserves an unavailable selected attachment until explicitly removed", () => {
    api.evidence.data.runs = [];
    const onSelectRun = vi.fn();
    mount(
      <TestEvidence
        skillId={ids.skill}
        candidate={{ kind: "draft", revision: 8 }}
        selectedRunId={ids.run}
        onSelectRun={onSelectRun}
      />,
    );
    expect(
      screen.getByText(/selected evidence no longer matches/),
    ).toBeInTheDocument();
    expect(onSelectRun).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Continue without attachment" }),
    );
    expect(onSelectRun).toHaveBeenCalledWith(null);
  });
  it("shows an explicit attached state for shared current evidence and removes it only on request", () => {
    Object.assign(api.evidence.data.runs[0], { visibility: "workspace", canAttach: true });
    const onSelectRun = vi.fn();
    const element = (selectedRunId) => (
      <MemoryRouter initialEntries={["/dashboard/skills/skill?tab=tests"]}>
        <TestEvidence
          skillId={ids.skill}
          candidate={{ kind: "draft", revision: 7 }}
          onSelectRun={onSelectRun}
          selectedRunId={selectedRunId}
          recordedWith="It is saved with the new version when you publish."
        />
      </MemoryRouter>
    );
    const view = render(element(undefined));
    expect(screen.queryByText(/Evidence attached/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Attach this evidence" }));
    expect(onSelectRun).toHaveBeenCalledExactlyOnceWith(ids.run);
    view.rerender(element(ids.run));
    const row = screen.getByText(/^Evidence attached: test run from/).closest('[role="status"]');
    expect(row).toHaveFocus();
    expect(within(row).getByText(/1 test case · 0 answer regressions · shared with workspace\. It is saved with the new version when you publish\./)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "View attached results" })).toHaveAttribute("href", expect.stringContaining(`run=${ids.run}`));
    expect(screen.queryByRole("button", { name: "Attach this evidence" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue without attachment" })).not.toBeInTheDocument();
    expect(onSelectRun).toHaveBeenCalledTimes(1);
    fireEvent.click(within(row).getByRole("button", { name: "Remove attachment" }));
    expect(onSelectRun).toHaveBeenLastCalledWith(null);
  });
  it("never shows selected evidence as attached once it stops matching the candidate", () => {
    Object.assign(api.evidence.data.runs[0], { visibility: "workspace", canAttach: true, currentness: { ...currentness, content: false } });
    const onSelectRun = vi.fn();
    mount(
      <TestEvidence
        skillId={ids.skill}
        candidate={{ kind: "draft", revision: 7 }}
        selectedRunId={ids.run}
        onSelectRun={onSelectRun}
      />,
    );
    expect(screen.queryByText(/Evidence attached/)).not.toBeInTheDocument();
    expect(screen.getByText(/selected evidence no longer matches/)).toBeInTheDocument();
    expect(onSelectRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue without attachment" }));
    expect(onSelectRun).toHaveBeenCalledExactlyOnceWith(null);
  });
});

describe("editor integration seam", () => {
  it("uses the supplied flush and marks the completed run stale on the very next edit", async () => {
    const onSave = vi.fn().mockResolvedValue({ ...savedDraft, revision: 8 });
    const props = {
      skill,
      draft: savedDraft,
      isDirty: true,
      onSave,
      onOpenFile: vi.fn(),
    };
    const view = mount(<EditorTestsPanel {...props} />);
    await choose("Example to run", "Eligible refund");
    fireEvent.click(screen.getByRole("button", { name: "Run example" }));
    await configure();
    await reviewPlan();
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await screen.findByText("Draft test evidence");
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(api.preview.mock.calls[0][0].candidate.revision).toBe(8);
    fireEvent.click(
      within(
        screen.getByRole("dialog", { name: "Draft test evidence" }),
      ).getByLabelText("Close"),
    );
    view.rerender(
      <MemoryRouter>
        <EditorTestsPanel
          {...props}
          draft={{
            ...savedDraft,
            files: [{ path: "SKILL.md", content: "New unsaved edit" }],
          }}
        />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(
        "For earlier content. Edit, save and run again to check the current draft.",
      ),
    ).toBeInTheDocument();
  });
});


it.each([400, 403])("unlocks normalized first-attempt admission rejection %s and rotates the operation", async (statusCode) => {
  api.create.mockRejectedValueOnce({ statusCode, code: "spend_policy_required" });
  // The re-read policy now shows an allowance (an admin set one), so the reviewer may try again.
  api.spend = { data: { enabled: true, periodLimitUsd: "20.00" }, refetch: vi.fn() };
  mount(setup());
  await configure();
  await reviewPlan();
  fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
  await screen.findByText("Paid runs need a workspace allowance");
  expect(screen.queryByText(/request may already have been accepted/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Preview work and spend" })).toBeEnabled();
  const first = api.create.mock.calls[0][0];
  fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
  await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2));
  expect(api.create.mock.calls[1][0].idempotencyKey).not.toBe(first.idempotencyKey);
});

it("does not discard an uncertain paid request after a normalized recovery denial", async () => {
  api.create.mockRejectedValueOnce(new Error("Lost response")).mockRejectedValueOnce({ statusCode: 403, code: "spend_policy_required" });
  mount(setup());
  await configure();
  await reviewPlan();
  fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry same submission" }));
  await screen.findByText("Paid runs need a workspace allowance");
  expect(screen.getByRole("button", { name: "Retry same submission" })).toBeEnabled();
  expect(api.create.mock.calls[1][0]).toBe(api.create.mock.calls[0][0]);
});

it("shows a missing allowance before consent and disables the paid run with its reason", async () => {
  api.spend = { data: { enabled: false, periodLimitUsd: null } };
  try {
    mount(setup());
    expect(screen.getByText("Paid runs need a workspace allowance")).toBeVisible();
    expect(screen.getByText("Ask a workspace admin to set an allowance.")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Set allowance" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy request for admin" })).toHaveClass("MuiButton-outlined");
    await configure();
    await reviewPlan();
    // The send tick covers this run's spend, so it is held back (and was not ticked by reviewPlan).
    const consent = screen.getByLabelText(SEND);
    expect(consent).toBeDisabled();
    expect(consent).not.toBeChecked();
    expect(consent).toHaveAccessibleDescription("Set an allowance first");
    const run = screen.getByRole("button", { name: "Run quick check" });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription("Paid runs are off for this workspace.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  } finally {
    api.spend = undefined;
  }
});

it("hands a member a request for an admin that names the test and links back to it", async () => {
  api.spend = { data: { enabled: false, periodLimitUsd: null } };
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  window.history.pushState({}, "", "/dashboard/skills/skill?tab=tests");
  try {
    mount(setup());
    await configure();
    fireEvent.click(screen.getByRole("button", { name: "Copy request for admin" }));
    expect(await screen.findByText("Copied the request for an admin.")).toHaveAttribute("role", "status");
    const text = writeText.mock.calls[0][0];
    expect(text).toMatch(new RegExp(`^Please set a Skills workspace allowance so I can run a test of ${api.skill.name} \\(up to \\$1\\.00\\): http`));
    const url = new URL(text.slice(text.indexOf("http")));
    expect(url.pathname).toBe("/dashboard/skills/allowance");
    expect(url.searchParams.get("returnTo")).toBe("/dashboard/skills/skill?tab=tests");
  } finally {
    api.spend = undefined;
    delete navigator.clipboard;
    window.history.pushState({}, "", "/");
  }
});

it("brings the provider acknowledgment back unticked once an allowance exists", async () => {
  api.spend = { data: { enabled: false, periodLimitUsd: null } };
  try {
    const view = mount(setup());
    await configure();
    await reviewPlan();
    const label = SEND;
    expect(screen.getByLabelText(label)).toBeDisabled();
    api.spend = { data: { enabled: true, periodLimitUsd: "20.00" } };
    view.rerender(<MemoryRouter initialEntries={["/dashboard/skills/skill?tab=tests"]}>{setup()}</MemoryRouter>);
    const consent = screen.getByLabelText(label);
    expect(consent).toBeEnabled();
    expect(consent).not.toBeChecked();
    expect(screen.queryByText("Set an allowance first")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run quick check" })).toBeDisabled();
    fireEvent.click(consent);
    expect(consent).toBeChecked();
  } finally {
    api.spend = undefined;
  }
});

it("links Set allowance back to the blocked test so the admin can return after saving", () => {
  api.spend = { data: { enabled: false, periodLimitUsd: null } };
  accessOverride.value = { ready: true, sameScope: true, isViewer: false, canWrite: true, canAdmin: true };
  window.history.pushState({}, "", "/dashboard/skills/skill?tab=tests");
  try {
    mount(setup());
    const link = screen.getByRole("link", { name: "Set allowance" });
    const url = new URL(link.href);
    expect(url.pathname).toBe("/dashboard/skills/allowance");
    expect(url.searchParams.get("returnTo")).toBe("/dashboard/skills/skill?tab=tests");
  } finally {
    api.spend = undefined;
    accessOverride.value = null;
    window.history.pushState({}, "", "/");
  }
});

// ---- UXF1 (SKILL-TRACES §16 UXF-03, UXF-04, UXF-11, UXF-12, UXF-14) ----
const visibleText = () => [...document.body.querySelectorAll("*")]
  .filter((element) => !element.closest("[hidden]") && element.children.length === 0)
  .map((element) => element.textContent)
  .join(" ");

describe("skill Tests sub-tabs", () => {
  it("shows Results, Run a test and Cases in that order, with Results first, in every state", () => {
    for (const entry of [
      "/dashboard/skills/skill?tab=tests",
      "/dashboard/skills/skill?tab=tests&state=loading",
      "/dashboard/skills/skill?tab=tests&state=error",
      "/dashboard/skills/skill?tab=tests&state=empty",
      `/dashboard/skills/skill?tab=tests&run=${ids.run}`,
    ]) {
      mount(<TestsTab skill={skill} />, entry);
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Results", "Run a test", "Cases"]);
      expect(screen.getByRole("tab", { name: "Results" })).toHaveAttribute("aria-selected", "true");
      cleanup();
    }
  });
  it("puts the latest result and History in Results, the setup in Run a test and the table in Cases", () => {
    mount(<TestsTab skill={skill} />, `/dashboard/skills/skill?tab=tests&run=${ids.run}`);
    expect(within(visiblePanel()).getByText("Expectations met on these test cases.")).toBeVisible();
    expect(within(visiblePanel()).getByText("History")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Preview work and spend" })).not.toBeInTheDocument();
    fireEvent.click(within(visiblePanel()).getByRole("button", { name: "Run a test" }));
    expect(screen.getByRole("tab", { name: "Run a test" })).toHaveAttribute("aria-selected", "true");
    expect(within(visiblePanel()).getByRole("button", { name: "Preview work and spend" })).toBeVisible();
    expect(within(visiblePanel()).getByRole("checkbox", { name: `Select ${testCase.title}` })).toBeChecked();
    openTab("Cases");
    expect(within(visiblePanel()).getByRole("table", { name: "Saved test cases" })).toBeVisible();
  });
  it("keeps the run setup mounted while another tab is open", async () => {
    mount(<TestsTab skill={skill} />);
    openTab("Run a test");
    await configure();
    openTab("Cases");
    openTab("Run a test");
    expect(screen.getByLabelText("Estimated-cost stop (USD)")).toHaveValue("1.00");
  });
  it("opens a test case from its row, with Edit and Remove in the row's ⋯", () => {
    mount(<TestsTab skill={skill} />);
    openTab("Cases");
    const row = within(visiblePanel()).getByRole("row", { name: /Eligible refund/ });
    fireEvent.click(within(row).getByLabelText("Actions for Eligible refund"));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Edit test case", "Remove test case"]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.click(within(row).getByText("Eligible refund"));
    expect(screen.getByLabelText("Request")).toHaveValue(testCase.prompt);
  });
  it("calls test cases Approved or Needs approval, never Reviewed or Needs review", () => {
    api.suite.data.cases = [testCase, { ...testCase, id: "draft-case", title: "Unapproved", reviewed: false }];
    mount(<TestsTab skill={skill} />);
    openTab("Cases");
    const table = within(visiblePanel()).getByRole("table", { name: "Saved test cases" });
    expect(within(table).getByText("Approved")).toBeVisible();
    expect(within(table).getByText("Needs approval")).toBeVisible();
    expect(within(table).queryByText("Reviewed")).not.toBeInTheDocument();
    const text = visibleText();
    for (const term of RETIRED_TERMS) expect(text).not.toContain(term);
  });
});

describe("one send tick (UXF-03)", () => {
  const sendBoxes = () => screen.getAllByRole("checkbox").filter((box) => /^Send /.test(box.closest("label")?.textContent || ""));
  it("asks one tick for saved test cases and sends every server field bound to the preview", async () => {
    const onStarted = vi.fn();
    mount(setup({ onStarted }));
    await configure();
    fireEvent.click(screen.getByRole("button", { name: "Preview work and spend" }));
    await screen.findByText("Confirm the reviewed plan");
    // Saved, approved test cases ask no case tick; files, answer keys and provider consent are one tick.
    expect(sendBoxes()).toHaveLength(1);
    expect(screen.queryByLabelText("The files sent to the model contain no answer keys or grading material.")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("I reviewed every file and input that will be sent to the model.")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "View all files sent to the model" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Run quick check" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText(SEND));
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    const request = api.create.mock.calls[0][0];
    expect(request.expectedFingerprint).toBe("plan-fingerprint");
    expect(request.acknowledgeProviderProcessing).toBe(true);
    expect(request.inventoryReview).toEqual({ fingerprint: "plan-fingerprint", reviewedAllFiles: true, noKnownAnswerKeys: true, classifications: [], falsePositiveSecretFindings: [] });
  });
  it("adds one act per flagged file and per secret finding", async () => {
    const findings = [{ findingId: "f1", code: "secret_suspected", location: { kind: "bundle_file", path: "SKILL.md" }, explanation: "Match" }];
    const base = preview.inventory[0].files[0];
    api.preview.mockResolvedValue({ ...preview, outboundFindings: findings, inventory: [{ ...preview.inventory[0], files: [base, { ...base, path: "eval.md", sha256: "e".repeat(64), flags: ["evaluation_artifact"] }] }] });
    mount(setup());
    await configure();
    fireEvent.click(screen.getByRole("button", { name: "Preview work and spend" }));
    await screen.findByText("Confirm the reviewed plan");
    expect(sendBoxes()).toHaveLength(1);
    expect(screen.getAllByLabelText("This is not a real secret")).toHaveLength(1);
    expect(screen.getByLabelText(`Classify eval.md (${"e".repeat(8)})`)).toBeInTheDocument();
  });
  it("leaves the answer-key statement out of a diagnostic check and sends it as false", async () => {
    const onStarted = vi.fn();
    mount(setup({ onStarted, parentRunId: ids.run }));
    await configure();
    fireEvent.click(screen.getByRole("button", { name: "Preview work and spend" }));
    await screen.findByText("Confirm the reviewed plan");
    const tick = screen.getByLabelText(/^Send this file and 1 test case to Fixture, up to \$0\.10\.$/);
    fireEvent.click(tick);
    fireEvent.click(screen.getByRole("button", { name: "Run quick check" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(api.create.mock.calls[0][0].inventoryReview).toMatchObject({ reviewedAllFiles: true, noKnownAnswerKeys: false });
  });
  it("unticks when the preview fingerprint changes", () => {
    const onChange = vi.fn();
    const review = { fingerprint: "first", reviewedAllFiles: true, noKnownAnswerKeys: true, classifications: [], falsePositiveSecretFindings: [] };
    const first = { ...preview, fingerprint: "first" };
    const view = render(<TestInventoryReview preview={first} value={review} onChange={onChange} sendLabel="Send this file and 1 test case to Fixture, up to $0.10. It contains no answer keys or grading material." />);
    expect(screen.getByRole("checkbox", { name: /^Send this file/ })).toBeChecked();
    expect(onChange).not.toHaveBeenCalled();
    view.rerender(<TestInventoryReview preview={{ ...preview, fingerprint: "second" }} value={review} onChange={onChange} sendLabel="Send this file and 1 test case to Fixture, up to $0.10. It contains no answer keys or grading material." />);
    expect(screen.getByRole("checkbox", { name: /^Send this file/ })).not.toBeChecked();
    expect(onChange).toHaveBeenCalledWith({ review: { fingerprint: "second", reviewedAllFiles: false, noKnownAnswerKeys: false, classifications: [], falsePositiveSecretFindings: [] }, complete: false, diagnostic: true });
  });
  it("words the send tick for one or several files", () => {
    expect(sendSentence({ fileCount: 3, what: "the request", provider: "OpenAI", upTo: "$0.24" }))
      .toBe("Send these 3 files and the request to OpenAI, up to $0.24. They contain no answer keys or grading material.");
    expect(sendSentence({ fileCount: 1, what: "the test case", provider: "OpenAI", upTo: "$0.24", attestsNoAnswerKeys: false }))
      .toBe("Send this file and the test case to OpenAI, up to $0.24.");
    expect(centsUp("0.23869440")).toBe("$0.24");
    expect(providerName("openai")).toBe("OpenAI");
  });
});

describe("evidence summary (UXF-11)", () => {
  it("shows one summary line and keeps counts, model, temperature and token limit under Technical details", () => {
    mount(<TestEvidence variant="summary" skillId={ids.skill} candidate={{ kind: "version", version: 3 }} onSelectRun={vi.fn()} />);
    expect(screen.getByTestId("evidence-summary")).toHaveTextContent("Passed: 1 of 1 test case, no regressions. Private, 6 Nov.");
    expect(screen.queryByText(/answer regression/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Task model:/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Technical details" }));
    expect(screen.getByText(/0 answer regressions \(including mixed\) · 0 skill-selection failures/)).toBeInTheDocument();
    expect(screen.getByText(/Task model: historical-task-2025 · temperature 0\.3 · output allowance 1536 tokens\/call/)).toBeInTheDocument();
    expect(screen.getByText(/1 attempt per version/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Attach this evidence" })).toBeInTheDocument();
    const text = document.body.textContent;
    expect(text).not.toMatch(/per arm|attempt\(s\)|\bexact\b|1 test cases/i);
  });
  it("says what the server reported, with plain plurals", () => {
    const row = { ...evidenceRow, visibility: "workspace", selectedCases: 3, createdAt: "2026-11-16T12:00:00Z" };
    expect(evidenceSummary(row, { state: "works_on_examples", regressedCases: 0 })).toBe("Passed: 3 of 3 test cases, no regressions. Shared with workspace, 16 Nov.");
    expect(evidenceSummary({ ...row, selectedCases: 1 }, { state: "works_on_examples", regressedCases: 0 })).toBe("Passed: 1 of 1 test case, no regressions. Shared with workspace, 16 Nov.");
    expect(evidenceSummary(row, { state: "needs_changes", regressedCases: 1 })).toBe("Needs changes: 1 of 3 test cases regressed. Shared with workspace, 16 Nov.");
    // Without the run's decision it never says "Passed".
    expect(evidenceSummary(row)).toBe("Completed: 3 test cases, no regressions. Shared with workspace, 16 Nov.");
    expect(evidenceSummary({ ...row, status: "running" })).toBe("Running: 3 test cases. Shared with workspace, 16 Nov.");
  });
  it("keeps the full evidence view free of harness words", () => {
    mount(<TestEvidence skillId={ids.skill} candidate={{ kind: "version", version: 3 }} onSelectRun={vi.fn()} />);
    expect(screen.getByText(/1 attempt per version/)).toBeInTheDocument();
    expect(screen.getByText("Checked this content · 1 of 3 saved test cases")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/per arm|attempt\(s\)|\bexact\b/i);
  });
  it("keeps run results and the setup free of harness words", async () => {
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} />);
    expect(screen.getByText(/1 test case · 1 attempt per version · 0 selected competitors/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/per arm|attempt\(s\)|\bexact\b/i);
    cleanup();
    mount(setup());
    await configure();
    fireEvent.click(screen.getByRole("button", { name: "Preview work and spend" }));
    await screen.findByText("Confirm the reviewed plan");
    expect(screen.getByText("1 test case × 2 versions × 1 attempt per version = 2 attempts")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/per arm|attempt\(s\)|\bexact\b/i);
  });
});

describe("result rows (UXF-12)", () => {
  it("opens a test case result from its row and keeps Inspect in the row's ⋯", () => {
    const onSelectionChange = vi.fn();
    mount(<TestRunResults skillId={ids.skill} runId={ids.run} onSelectionChange={onSelectionChange} />);
    const table = screen.getByRole("table", { name: "Test case comparisons" });
    expect(within(table).queryByRole("button", { name: /^Inspect/ })).not.toBeInTheDocument();
    fireEvent.click(within(table).getByLabelText("Actions for Eligible refund"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Inspect" }));
    expect(onSelectionChange).toHaveBeenCalledWith({ caseId: ids.case, attemptId: null });
  });
});
