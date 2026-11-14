import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
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
  expectedDraftsOf,
  useAddEvidenceCases,
  useCreateEvidenceImport,
  useDraftEvidenceCases,
  useEvidenceImport,
} = await import("../evidenceImports");
const { draftsKey } = await import("../versions");
const { validateContractedRequestConfig } = await import(
  "src/api/contracts/openapi-contract"
);

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

  it("refuses a record the contract does not name", () => {
    const result = validateContractedRequestConfig({
      url: `${BASE}${IMPORT}/draft/`,
      method: "post",
      data: { case_ids: [CASE], shows: { [CASE]: "unknown" } },
    });
    expect(result.ok).toBe(false);
  });
});
