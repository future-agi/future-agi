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
import TestEvidence from "./TestEvidence";
import EditorTestsPanel from "./EditorTestsPanel";

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
const reviewPlan = async () => {
  fireEvent.click(
    screen.getByRole("button", { name: "Preview work and spend" }),
  );
  await screen.findByText("Confirm the reviewed plan");
  fireEvent.click(
    screen.getByLabelText(
      "I know of no bundled answer keys or evaluation material",
    ),
  );
  fireEvent.click(
    screen.getByLabelText(
      "I reviewed the entire inventory and outbound inputs",
    ),
  );
  fireEvent.click(
    screen.getByLabelText(
      "Send the reviewed files and example inputs to these model providers within this spend stop",
    ),
  );
};
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

describe("reviewed examples", () => {
  it("shows the first-run action and three editable starters without paid work", () => {
    api.suite.data.cases = [];
    mount(<TestsTab skill={skill} />);
    expect(
      screen.getByText("Does this skill handle your real requests?"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add first example" }),
    ).toBeEnabled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Structured output with a missing value",
      }),
    );
    expect(screen.getByLabelText("Request").value).toContain("null");
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
    fireEvent.click(screen.getByLabelText("Actions for Eligible refund"));
    fireEvent.click(screen.getByText("Review or edit example"));
    fireEvent.change(screen.getByLabelText("Request"), {
      target: { value: "Keep my unsaved request" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save example" }));
    await screen.findAllByText(/Someone changed the saved examples/);
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
      if (state === "loading")
        expect(
          screen.getByRole("progressbar", { name: "Loading skill tests" }),
        ).toBeInTheDocument();
      else
        expect(
          screen.getByText(
            state === "error"
              ? "Could not load saved examples"
              : "Does this skill handle your real requests?",
          ),
        ).toBeInTheDocument();
    },
  );
  it("clears private editor form state when the principal changes", () => {
    const view = mount(<TestsTab skill={skill} />);
    fireEvent.click(screen.getByRole("button", { name: "Add example" }));
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
    expect(screen.getByText(/harness-recorded/)).toBeInTheDocument();
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
