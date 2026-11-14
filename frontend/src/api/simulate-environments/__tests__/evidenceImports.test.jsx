import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
// Add keys are counted so a test can tell a reused key from a fresh one.
let keyCount = 0;
vi.mock("src/api/harness/harness", async (importOriginal) => ({
  ...(await importOriginal()),
  harnessIdempotencyKey: vi.fn(() => {
    keyCount += 1;
    return `key-${keyCount}`;
  }),
}));

const { default: axios } = await import("src/utils/axios");
const {
  evidenceImportKey,
  evidenceImportsKey,
  expectedDraftsOf,
  useAddEvidenceCases,
  useCreateEvidenceImport,
  useDiscardEvidenceImport,
  useDraftEvidenceCases,
  useEvidenceImport,
} = await import("../evidenceImports");
const { draftsKey } = await import("../versions");
const { validateContractedRequestConfig, validateContractedResponse } =
  await import("src/api/contracts/openapi-contract");

const ENV = "11111111-1111-1111-1111-111111111111";
const IMPORT = "22222222-2222-2222-2222-222222222222";
const BASE = `/simulate/api/harness-environments/${ENV}/evidence-imports/`;

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const bundle = (overrides = {}) => ({
  id: IMPORT,
  source_kind: "session",
  drafting: false,
  cases: [],
  redaction: { method: "presidio", reason: "", findings: [], total: 0 },
  ...overrides,
});

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  axios.get.mockReset();
  axios.post.mockReset();
  keyCount = 0;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("expectedDraftsOf", () => {
  it("sends a piece with no open draft as id null, revision 0", () => {
    expect(
      expectedDraftsOf({ environment: null, suite: { id: "s1", revision: 3 } }),
    ).toEqual({
      environment: { id: null, revision: 0 },
      suite: { id: "s1", revision: 3 },
    });
  });

  it("reads no drafts at all as nothing open", () => {
    expect(expectedDraftsOf(undefined)).toEqual({
      environment: { id: null, revision: 0 },
      suite: { id: null, revision: 0 },
    });
  });
});

describe("useCreateEvidenceImport", () => {
  it("posts the picked sources and seeds the review's cache", async () => {
    const made = bundle();
    axios.post.mockResolvedValue({ data: made });
    const { result } = renderHook(() => useCreateEvidenceImport(), { wrapper });

    await act(() =>
      result.current.mutateAsync({
        envId: ENV,
        projectId: "p1",
        sourceKind: "call",
        ids: ["t1", "t2"],
      }),
    );

    expect(axios.post).toHaveBeenCalledWith(BASE, {
      project_id: "p1",
      source_kind: "call",
      ids: ["t1", "t2"],
    });
    expect(client.getQueryData(evidenceImportKey(ENV, IMPORT))).toEqual(made);
  });

  it("a retry makes an earlier review read again when revisited, but not its own", async () => {
    // Importing a blocked case's source again (B) supersedes the blocked case
    // of the earlier import (A), so A's review read before is out of date.
    const EARLIER = "33333333-3333-3333-3333-333333333333";
    const blocked = bundle({
      id: EARLIER,
      cases: [{ id: "c1", status: "blocked" }],
    });
    const retried = bundle({
      id: EARLIER,
      cases: [{ id: "c1", status: "discarded" }],
    });
    axios.get.mockResolvedValueOnce({ data: blocked });
    const first = renderHook(() => useEvidenceImport(ENV, EARLIER), {
      wrapper,
    });
    await waitFor(() => expect(first.result.current.data).toEqual(blocked));
    first.unmount();

    const made = bundle();
    axios.post.mockResolvedValue({ data: made });
    const { result } = renderHook(() => useCreateEvidenceImport(), { wrapper });
    await act(() =>
      result.current.mutateAsync({
        envId: ENV,
        projectId: "p1",
        sourceKind: "session",
        ids: ["s1"],
      }),
    );

    axios.get.mockResolvedValueOnce({ data: retried });
    const revisit = renderHook(() => useEvidenceImport(ENV, EARLIER), {
      wrapper,
    });
    await waitFor(() => expect(revisit.result.current.data).toEqual(retried));
    // The new import's review is the one the create returned: opening it reads
    // nothing, since its bundle re-reads the sources and runs redaction.
    const opened = renderHook(() => useEvidenceImport(ENV, IMPORT), {
      wrapper,
    });
    expect(opened.result.current.data).toEqual(made);
    expect(axios.get).toHaveBeenCalledTimes(2);
  });
});

describe("useEvidenceImport", () => {
  it("reads once and asks again only while the builder drafts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    axios.get
      .mockResolvedValueOnce({ data: bundle({ drafting: true }) })
      .mockResolvedValue({ data: bundle({ drafting: false }) });
    const { result } = renderHook(() => useEvidenceImport(ENV, IMPORT), {
      wrapper,
    });
    await waitFor(() => expect(result.current.data?.drafting).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(`${BASE}${IMPORT}/`);

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    await waitFor(() => expect(result.current.data?.drafting).toBe(false));
    expect(axios.get).toHaveBeenCalledTimes(2);

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("asks for nothing without an import", () => {
    renderHook(() => useEvidenceImport(ENV, null), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe("useDraftEvidenceCases", () => {
  it("sends what each record shows and keeps the answer", async () => {
    const answer = bundle({ drafting: true });
    axios.post.mockResolvedValue({ data: answer });
    const { result } = renderHook(() => useDraftEvidenceCases(ENV, IMPORT), {
      wrapper,
    });

    await act(() =>
      result.current.mutateAsync({ caseIds: ["c1"], shows: { c1: "success" } }),
    );

    expect(axios.post).toHaveBeenCalledWith(`${BASE}${IMPORT}/draft/`, {
      case_ids: ["c1"],
      shows: { c1: "success" },
    });
    expect(client.getQueryData(evidenceImportKey(ENV, IMPORT))).toEqual(answer);
  });
});

describe("useAddEvidenceCases", () => {
  const drafts = { environment: null, suite: { id: "s1", revision: 2 } };

  it("sends the drafts the review saw and refreshes them after", async () => {
    const after = bundle();
    axios.post.mockResolvedValue({
      data: { saved: false, evidence_import: after },
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useAddEvidenceCases(ENV, IMPORT), {
      wrapper,
    });

    await act(() => result.current.mutateAsync({ caseIds: ["c1"], drafts }));

    expect(axios.post).toHaveBeenCalledWith(`${BASE}${IMPORT}/add/`, {
      case_ids: ["c1"],
      expected_drafts: {
        environment: { id: null, revision: 0 },
        suite: { id: "s1", revision: 2 },
      },
      idempotency_key: "key-1",
    });
    expect(client.getQueryData(evidenceImportKey(ENV, IMPORT))).toEqual(after);
    expect(invalidate).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: draftsKey(ENV) }),
    );
  });

  it("keeps the key when the server may not have seen the add", async () => {
    axios.post
      .mockRejectedValueOnce({ transportCode: "ERR_NETWORK" })
      .mockResolvedValue({ data: { saved: true, evidence_import: bundle() } });
    const { result } = renderHook(() => useAddEvidenceCases(ENV, IMPORT), {
      wrapper,
    });

    await act(() =>
      result.current.mutateAsync({ caseIds: ["c1"], drafts }).catch(() => {}),
    );
    await act(() => result.current.mutateAsync({ caseIds: ["c1"], drafts }));

    const keys = axios.post.mock.calls.map(([, body]) => body.idempotency_key);
    expect(keys).toEqual(["key-1", "key-1"]);
  });

  it("mints a new key after a refusal", async () => {
    axios.post
      .mockRejectedValueOnce({ statusCode: 409, error: "draft_changed" })
      .mockResolvedValue({ data: { saved: true, evidence_import: bundle() } });
    const { result } = renderHook(() => useAddEvidenceCases(ENV, IMPORT), {
      wrapper,
    });

    await act(() =>
      result.current.mutateAsync({ caseIds: ["c1"], drafts }).catch(() => {}),
    );
    await act(() => result.current.mutateAsync({ caseIds: ["c1"], drafts }));

    const keys = axios.post.mock.calls.map(([, body]) => body.idempotency_key);
    expect(keys).toEqual(["key-1", "key-2"]);
  });
});

// The dev request guard reads the published contract before a body is sent;
// a body it refuses never reaches the server, so each one is checked here.
describe("evidence request bodies match the published contract", () => {
  const CASE = "33333333-3333-3333-3333-333333333333";
  const SUITE = "44444444-4444-4444-4444-444444444444";

  it("accepts the create, draft and add bodies the hooks send", async () => {
    axios.post.mockResolvedValue({
      data: { ...bundle(), saved: false, evidence_import: bundle() },
    });
    const { result: create } = renderHook(() => useCreateEvidenceImport(), {
      wrapper,
    });
    const { result: draft } = renderHook(
      () => useDraftEvidenceCases(ENV, IMPORT),
      { wrapper },
    );
    const { result: add } = renderHook(() => useAddEvidenceCases(ENV, IMPORT), {
      wrapper,
    });

    await act(() =>
      create.current.mutateAsync({
        envId: ENV,
        projectId: "55555555-5555-5555-5555-555555555555",
        sourceKind: "call",
        ids: [CASE],
      }),
    );
    await act(() =>
      draft.current.mutateAsync({
        caseIds: [CASE],
        shows: { [CASE]: "partial" },
      }),
    );
    await act(() =>
      add.current.mutateAsync({
        caseIds: [CASE],
        drafts: { environment: null, suite: { id: SUITE, revision: 1 } },
      }),
    );

    const results = axios.post.mock.calls.map(([url, data]) =>
      validateContractedRequestConfig({ url, method: "post", data }),
    );
    expect(results).toHaveLength(3);
    results.forEach((one) => {
      expect(one.skipped).toBeFalsy();
      expect(one.ok).toBe(true);
    });
  });

  it("accepts a dataset create body, with or without an expected column", async () => {
    axios.post.mockResolvedValue({ data: bundle() });
    const { result: create } = renderHook(() => useCreateEvidenceImport(), {
      wrapper,
    });

    for (const expected of [null, SUITE]) {
      await act(() =>
        create.current.mutateAsync({
          envId: ENV,
          datasetId: "55555555-5555-5555-5555-555555555555",
          sourceKind: "dataset",
          ids: [CASE],
          columns: { conversation: SUITE, labels: [], expected },
          production: false,
        }),
      );
    }

    const [first] = axios.post.mock.calls;
    expect(first[1]).toEqual({
      source_kind: "dataset",
      dataset_id: "55555555-5555-5555-5555-555555555555",
      ids: [CASE],
      columns: { conversation: SUITE, labels: [], expected: null },
      production: false,
    });
    axios.post.mock.calls.forEach(([url, data]) => {
      expect(
        validateContractedRequestConfig({ url, method: "post", data }).ok,
      ).toBe(true);
    });
  });

  it("refuses a record the contract does not name", () => {
    const result = validateContractedRequestConfig({
      url: `${BASE}${IMPORT}/draft/`,
      method: "post",
      data: { case_ids: [CASE], shows: { [CASE]: "unknown" } },
    });
    expect(result.ok).toBe(false);
  });
});

describe("evidence review bundles match the published contract", () => {
  const part = (status = "missing", reason = "") => ({ status, reason });
  const reviewed = (overrides = {}) => ({
    id: "33333333-3333-3333-3333-333333333333",
    source_id: "row-1",
    name: "",
    status: "pending",
    reason: "",
    source_available: true,
    scenario_key: "",
    scenario_name: "",
    new_checks: [],
    changed_since_import: false,
    also_from: 0,
    duplicate_of: null,
    trace_ids: [],
    started_at: null,
    ended_at: null,
    transcript: {
      ...part("present"),
      turns: [
        { role: "user", text: "hi", truncated: false, trace_id: "", at: null },
      ],
    },
    tool_calls: { ...part(), calls: [] },
    tool_inputs: part(),
    tool_outputs: part(),
    starting_state: part(),
    labels: {
      ...part("present"),
      labels: [
        {
          name: "intent",
          kind: "text",
          value: "refund",
          notes: "",
          target: "row",
        },
      ],
    },
    expected_outcome: { ...part("present"), text: "Refuse it." },
    ...overrides,
  });
  const full = (overrides = {}) => ({
    id: IMPORT,
    environment_id: ENV,
    project_id: "55555555-5555-5555-5555-555555555555",
    dataset_id: null,
    agent_version_id: null,
    source_kind: "session",
    created_at: "2026-11-12T10:00:00Z",
    simulated: false,
    production: true,
    drafting: false,
    cases: [reviewed()],
    redaction: { method: "presidio", reason: "", findings: [], total: 0 },
    ...overrides,
  });
  const check = (data) =>
    validateContractedResponse({
      status: 200,
      data,
      config: { method: "get", url: `${BASE}${IMPORT}/` },
    });

  it("accepts an Observe bundle, whose dataset is null, and a dataset bundle", () => {
    for (const data of [
      full(),
      full({
        source_kind: "dataset",
        project_id: null,
        dataset_id: "55555555-5555-5555-5555-555555555555",
        production: false,
      }),
    ]) {
      const result = check(data);
      expect(result.skipped).toBeFalsy();
      expect(result.ok).toBe(true);
    }
  });

  it("accepts a case more sources hold, and one already imported", () => {
    const repeated = reviewed({
      id: "66666666-6666-6666-6666-666666666666",
      status: "duplicate",
      duplicate_of: {
        import_id: "77777777-7777-7777-7777-777777777777",
        case_id: "88888888-8888-8888-8888-888888888888",
        scenario_name: "",
      },
    });
    expect(
      check(full({ cases: [reviewed({ also_from: 2 }), repeated] })).ok,
    ).toBe(true);
    // A count of sources is never negative.
    expect(check(full({ cases: [reviewed({ also_from: -1 })] })).ok).toBe(
      false,
    );
  });

  it("accepts a label of any JSON value", () => {
    for (const value of ["refund", 4, true, { score: 1 }]) {
      const labels = { ...part("present"), labels: [] };
      labels.labels.push({
        name: "x",
        kind: "text",
        value,
        notes: "",
        target: "span",
      });
      expect(check(full({ cases: [reviewed({ labels })] })).ok).toBe(true);
    }
  });
});

describe("the environment's unfinished imports", () => {
  const listed = {
    id: IMPORT,
    source_kind: "dataset",
    source_name: "Support evals",
    source_available: true,
    case_count: 3,
    added_count: 1,
    status: "partly_added",
    created_at: "2026-11-12T10:00:00Z",
    can_discard: true,
  };

  it("matches the published contract, list and discard", () => {
    // A transcript import is named by its first transcript; one with only
    // blocked cases left stays listed.
    const blocked = {
      ...listed,
      id: "66666666-6666-6666-6666-666666666666",
      source_kind: "transcript",
      source_name: "Refund call",
      added_count: 0,
      status: "blocked",
    };
    const list = validateContractedResponse({
      status: 200,
      data: [listed, blocked],
      config: { method: "get", url: BASE },
    });
    expect(list.skipped).toBeFalsy();
    expect(list.ok).toBe(true);
    const discard = validateContractedRequestConfig({
      url: `${BASE}${IMPORT}/`,
      method: "delete",
    });
    // A DELETE carries no body, so only the route itself is checked: the
    // contract must publish it, or no endpoint is found.
    expect(discard.endpoint?.template).toBe(
      "/simulate/api/harness-environments/{id}/evidence-imports/{import_id}/",
    );
    expect(discard.ok).toBe(true);
  });

  it("discarding one forgets its review and lists again", async () => {
    axios.delete.mockResolvedValue({ data: undefined });
    client.setQueryData(evidenceImportKey(ENV, IMPORT), bundle());
    client.setQueryData(evidenceImportsKey(ENV), [listed]);
    const { result } = renderHook(() => useDiscardEvidenceImport(ENV), {
      wrapper,
    });

    await act(() => result.current.mutateAsync(IMPORT));

    expect(axios.delete).toHaveBeenCalledWith(`${BASE}${IMPORT}/`);
    expect(client.getQueryData(evidenceImportKey(ENV, IMPORT))).toBeUndefined();
    expect(client.getQueryState(evidenceImportsKey(ENV)).isInvalidated).toBe(
      true,
    );
  });

  it("discarding one asks again for the environment's other reviews", async () => {
    // Discard can promote another import's repeat to the case to draft, so a
    // review of that import read before now says "already imported" wrongly.
    const OTHER = "33333333-3333-3333-3333-333333333333";
    const ELSEWHERE = "44444444-4444-4444-4444-444444444444";
    axios.delete.mockResolvedValue({ data: undefined });
    client.setQueryData(evidenceImportKey(ENV, OTHER), bundle({ id: OTHER }));
    client.setQueryData(evidenceImportKey(ELSEWHERE, OTHER), bundle());
    const { result } = renderHook(() => useDiscardEvidenceImport(ENV), {
      wrapper,
    });

    await act(() => result.current.mutateAsync(IMPORT));

    expect(
      client.getQueryState(evidenceImportKey(ENV, OTHER)).isInvalidated,
    ).toBe(true);
    // Another environment's reviews are left alone.
    expect(
      client.getQueryState(evidenceImportKey(ELSEWHERE, OTHER)).isInvalidated,
    ).toBe(false);
  });
});
