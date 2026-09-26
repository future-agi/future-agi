import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/harness/harness", () => ({
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  uploadHarnessSecretFile: vi.fn(),
}));
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  getHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(),
  getAvailableEvaluations: vi.fn(),
  addEvaluation: vi.fn(),
  addRunEvaluation: vi.fn(),
}));

const { createHarnessJob, uploadHarnessSecretFile } = await import(
  "src/api/harness/harness"
);
const {
  listHarnessEnvironments,
  deleteHarnessEnvironment,
  deleteAppliedEvaluation,
  addEvaluation,
  addRunEvaluation,
} = await import("src/api/simulate-environments/harnessEnvironments");
const {
  useMyEnvironments,
  useDeleteEnvironment,
  useBuildEnvironment,
  useUploadSecretFile,
  useRunSimulation,
  useAdoptTemplate,
  useAddEvaluation,
  useAddRunEvaluation,
  useRemoveAppliedEvaluation,
  SIMULATE_ENVIRONMENTS_KEY,
  availableEvaluationsKey,
  myEnvironmentsQueryKey,
} = await import("../environments");

// The paginated harness-environments payload the hook maps into table rows.
const HARNESS_ENVS = {
  count: 2,
  next: null,
  previous: null,
  total_pages: 1,
  current_page: 1,
  results: [
    {
      id: "env-voice",
      name: "Customer Support Line",
      description: "Handles inbound billing calls",
      source_kind: "provider",
      agent_type: "voice",
      status: "completed",
      stage: "completed",
      scenario_count: 12,
      tools_count: 4,
      last_updated: "2026-09-15T09:00:00Z",
      created_at: "2026-09-10T09:00:00Z",
    },
    {
      id: "env-chat",
      name: "Billing Chat Agent",
      description: null,
      source_kind: "github",
      agent_type: "chat",
      status: "running",
      stage: "running",
      scenario_count: 0,
      tools_count: null,
      last_updated: null,
      created_at: "2026-09-15T11:00:00Z",
    },
  ],
};

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return { queryClient, Wrapper };
};

beforeEach(() => {
  listHarnessEnvironments.mockReset();
  listHarnessEnvironments.mockResolvedValue(HARNESS_ENVS);
  deleteHarnessEnvironment.mockReset();
  deleteHarnessEnvironment.mockResolvedValue(undefined);
  createHarnessJob.mockReset();
  createHarnessJob.mockResolvedValue({ job: { job_id: "job-real" } });
});

describe("useMyEnvironments", () => {
  it("maps the harness-environments results into a page of flat rows + total", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useMyEnvironments({ page: 0, pageSize: 25 }), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    // The endpoint is 1-indexed; the table pager is 0-indexed.
    expect(listHarnessEnvironments).toHaveBeenCalledWith({ page: 1, limit: 25 });
    expect(result.current.data.total).toBe(2);
    expect(result.current.data.rows).toHaveLength(2);
    expect(result.current.data.rows[0]).toMatchObject({
      id: "env-voice",
      name: "Customer Support Line",
      description: "Handles inbound billing calls",
      status: "ready",
      agentType: "voice",
      tools: 4,
      scenarios: 12,
      updatedAt: "2026-09-15T09:00:00Z",
    });
    // last_updated is null → fall back to created_at; chat → AGENT_TYPES text.
    expect(result.current.data.rows[1]).toMatchObject({
      id: "env-chat",
      status: "running",
      agentType: "text",
      updatedAt: "2026-09-15T11:00:00Z",
    });
  });

  it("requests the 1-indexed page for a later table page", async () => {
    const { Wrapper } = makeWrapper();
    renderHook(() => useMyEnvironments({ page: 2, pageSize: 10 }), {
      wrapper: Wrapper,
    });
    await waitFor(() =>
      expect(listHarnessEnvironments).toHaveBeenCalledWith({ page: 3, limit: 10 }),
    );
  });

  it("maps a payload without results to an empty page", async () => {
    listHarnessEnvironments.mockResolvedValue({});
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useMyEnvironments(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ rows: [], total: 0 });
  });

  describe("polling", () => {
    const envRow = (id, status) => ({
      id,
      name: id,
      agent_type: "chat",
      status,
      stage: status,
    });

    const renderList = async () => {
      const { queryClient, Wrapper } = makeWrapper();
      const view = renderHook(() => useMyEnvironments(), { wrapper: Wrapper });
      await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
      const query = queryClient
        .getQueryCache()
        .find({ queryKey: myEnvironmentsQueryKey(0, 25) });
      return { queryClient, query, unmount: view.unmount };
    };

    it("polls every 3s while a row is still building", async () => {
      listHarnessEnvironments.mockResolvedValue({
        count: 1,
        results: [envRow("env-b", "building")],
      });
      const { query, unmount } = await renderList();
      expect(query.options.refetchInterval(query)).toBe(3000);
      unmount();
    });

    it("polls while one row is running among finished ones", async () => {
      const { query, unmount } = await renderList();
      expect(query.options.refetchInterval(query)).toBe(3000);
      unmount();
    });

    it("stops polling once every row is finished", async () => {
      listHarnessEnvironments.mockResolvedValue({
        count: 1,
        results: [envRow("env-b", "building")],
      });
      const { queryClient, query, unmount } = await renderList();
      queryClient.setQueryData(query.queryKey, {
        count: 2,
        results: [envRow("a", "completed"), envRow("b", "failed")],
      });
      expect(query.options.refetchInterval(query)).toBe(false);
      unmount();
    });

    it("does not poll an empty list or a payload without results", async () => {
      listHarnessEnvironments.mockResolvedValue({ count: 0, results: [] });
      const empty = await renderList();
      expect(empty.query.options.refetchInterval(empty.query)).toBe(false);
      empty.unmount();

      listHarnessEnvironments.mockResolvedValue({});
      const bare = await renderList();
      expect(bare.query.options.refetchInterval(bare.query)).toBe(false);
      bare.unmount();
    });
  });
});

describe("useDeleteEnvironment", () => {
  it("deletes by id and invalidates the list so the page refetches", async () => {
    const { queryClient, Wrapper } = makeWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(
      () => ({
        list: useMyEnvironments(),
        del: useDeleteEnvironment(),
      }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await result.current.del.mutateAsync("env-chat");

    expect(deleteHarnessEnvironment).toHaveBeenCalledWith("env-chat");
    // The visible page is refetched (rows shift up from later pages) rather than
    // filtered in place.
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: [...SIMULATE_ENVIRONMENTS_KEY, "list"],
    });
  });
});

describe("useBuildEnvironment", () => {
  it("creates a real job from a source-repo draft and returns its job id", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useBuildEnvironment(), {
      wrapper: Wrapper,
    });
    const out = await result.current.mutateAsync({
      kind: "repo",
      value: "acme/agent",
    });

    expect(createHarnessJob).toHaveBeenCalledTimes(1);
    const [body, idem] = createHarnessJob.mock.calls[0];
    expect(body.source).toMatchObject({ kind: "github", repository: "acme/agent" });
    expect(body.agent.connector).toBe("auto");
    expect(idem).toBe("idem-test");
    // The real job id is returned, never the raw draft.
    expect(out.envId).toBe("job-real");
    expect(out.source).toBeUndefined();
  });

  it("carries the exchanged hosted credential into the create body's secret_refs", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useBuildEnvironment(), {
      wrapper: Wrapper,
    });
    const secretRefs = {
      VAPI_API_KEY: {
        manager: "platform-vault",
        key: "harness-vapi_api_key-abc",
        version: "1",
        purpose: "target_provider",
      },
    };
    await result.current.mutateAsync({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_1",
      secret_refs: secretRefs,
    });

    const [body] = createHarnessJob.mock.calls[0];
    expect(body.agent.connector).toBe("vapi");
    expect(body.agent.config).toMatchObject({ assistant_id: "asst_1" });
    expect(body.agent.secret_refs).toEqual(secretRefs);
  });

  it("keeps the client-minted id and skips create for a non-buildable draft", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useBuildEnvironment(), {
      wrapper: Wrapper,
    });
    // A platform draft with no recognised provider cannot be built for real.
    const out = await result.current.mutateAsync({
      kind: "platform",
      apiKey: "sk-secret",
    });
    expect(createHarnessJob).not.toHaveBeenCalled();
    expect(out.envId).toMatch(/^env-/);
    expect(out.source).toBeUndefined();
  });
});

describe("useUploadSecretFile", () => {
  it("posts the file as multipart and returns the real ref, never the contents", async () => {
    uploadHarnessSecretFile.mockResolvedValue({
      secret_ref: { manager: "platform-vault", key: "ref-9", version: "1", purpose: "target_provider" },
      environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON",
      size: 42,
    });
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useUploadSecretFile(), {
      wrapper: Wrapper,
    });
    const file = new File(["SECRET=1"], "creds.json", {
      type: "application/json",
    });
    const out = await result.current.mutateAsync({ file });

    // The request is FormData carrying the file + the alias, never inline bytes.
    const form = uploadHarnessSecretFile.mock.calls[0][0];
    expect(form).toBeInstanceOf(FormData);
    expect(form.get("file")).toBe(file);
    // The upload endpoint accepts exactly one label, Google's own variable name.
    // `_JSON` is what the REPLY names the stored secret, and is the key
    // `secret_refs` must use — not the label to upload with.
    expect(form.get("environment_name")).toBe("GOOGLE_APPLICATION_CREDENTIALS");
    expect(out.environment_name).toBe("GOOGLE_APPLICATION_CREDENTIALS_JSON");

    expect(out.secret_ref).toEqual({ manager: "platform-vault", key: "ref-9", version: "1", purpose: "target_provider" });
    expect(out.environment_name).toBe("GOOGLE_APPLICATION_CREDENTIALS_JSON");
    expect(out.name).toBe("creds.json");
    expect(out.size).toBe(42);
    expect(out).not.toHaveProperty("contents");
  });

  it("surfaces a rejected upload rather than swallowing it", async () => {
    const rejection = { statusCode: 422, message: "Hosted credential uploads…" };
    uploadHarnessSecretFile.mockRejectedValue(rejection);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useUploadSecretFile(), {
      wrapper: Wrapper,
    });
    const file = new File(["{}"], "creds.json", { type: "application/json" });

    // `meta.errorHandled` suppresses the global error toast, so the caller has
    // to see the rejection or the user is told nothing at all.
    await expect(result.current.mutateAsync({ file })).rejects.toBe(rejection);
  });
});

describe("useRunSimulation", () => {
  it("resolves the env id and a run id", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useRunSimulation(), {
      wrapper: Wrapper,
    });
    const out = await result.current.mutateAsync("env-x");
    expect(out.envId).toBe("env-x");
    expect(out.runId).toMatch(/^run-/);
  });
});

describe("useAdoptTemplate", () => {
  it("mints an env id from a template id without echoing the template", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useAdoptTemplate(), {
      wrapper: Wrapper,
    });
    const out = await result.current.mutateAsync("env-voice-support");
    expect(out.envId).toMatch(/^env-/);
    expect(out.templateId).toBeUndefined();
  });
});

describe("useAddEvaluation", () => {
  it("seeds the detail from the 201 body and does NOT invalidate it in the same tick", async () => {
    const detail = { evaluations: { selected: [{ id: "cfg-1", name: "no_misselling" }] } };
    addEvaluation.mockResolvedValue(detail);
    const { queryClient, Wrapper } = makeWrapper();
    const setData = vi.spyOn(queryClient, "setQueryData");
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAddEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", name: "no_misselling" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(setData).toHaveBeenCalledWith(["harness-environment", "env-1"], detail);
    // A refetch here races the write it is meant to confirm and can flip the
    // row back from "Added"; the drawer refetches on close instead.
    expect(invalidate).not.toHaveBeenCalledWith({
      queryKey: ["harness-environment", "env-1"],
    });
  });

  // `available` is the drawer's only active observer of this query, so
  // invalidating it here would refetch it in the same tick and drop the
  // just-added row off the offer. `boundEntries`'s filter in
  // `AddEvaluationDrawer.jsx` already prevents the duplicate without this.
  it("does NOT invalidate the available list on add — the offer stays in place, marked Added", async () => {
    const detail = { evaluations: { selected: [{ id: "cfg-1", name: "no_misselling" }] } };
    addEvaluation.mockResolvedValue(detail);
    const { queryClient, Wrapper } = makeWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAddEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", name: "no_misselling" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidate).not.toHaveBeenCalledWith({
      queryKey: availableEvaluationsKey("env-1"),
    });
  });
});

describe("useAddRunEvaluation", () => {
  it("posts the name on the run and returns the five counts", async () => {
    addRunEvaluation.mockResolvedValue({
      queued: 12,
      skipped_existing: 3,
      skipped_in_flight: 0,
      skipped_pending: 1,
      completed_calls: 16,
    });
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useAddRunEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", executionId: "ex-1", name: "no_misselling" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(addRunEvaluation).toHaveBeenCalledWith("env-1", "ex-1", "no_misselling");
    expect(result.current.data).toMatchObject({ queued: 12, completed_calls: 16 });
  });

  // The same rule the environment-level add follows, for the same reason:
  // both adds are pressed from the picker, so neither refetches anything the
  // open picker is observing. The run path has no body to seed from either —
  // its receipt is the click's confirmation, and the drawer's close is what
  // refetches the detail.
  it("refetches neither list while the picker that triggered it can still be open", async () => {
    addRunEvaluation.mockResolvedValue({
      queued: 1,
      skipped_existing: 0,
      skipped_in_flight: 0,
      skipped_pending: 0,
      completed_calls: 1,
    });
    const { queryClient, Wrapper } = makeWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAddRunEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", executionId: "ex-1", name: "no_misselling" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidate).not.toHaveBeenCalledWith({
      queryKey: ["harness-environment", "env-1"],
    });
    expect(invalidate).not.toHaveBeenCalledWith({
      queryKey: availableEvaluationsKey("env-1"),
    });
  });

  // The 202 carries counts, not the detail — so nothing may be written into
  // the detail cache from it either.
  it("never patches the detail cache from the 202 counts", async () => {
    addRunEvaluation.mockResolvedValue({
      queued: 1,
      skipped_existing: 0,
      skipped_in_flight: 0,
      skipped_pending: 0,
      completed_calls: 1,
    });
    const { queryClient, Wrapper } = makeWrapper();
    const setData = vi.spyOn(queryClient, "setQueryData");
    const { result } = renderHook(() => useAddRunEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", executionId: "ex-1", name: "no_misselling" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(setData).not.toHaveBeenCalled();
  });
});

describe("useRemoveAppliedEvaluation", () => {
  const KEYS = (id) => [["harness-environment", id], availableEvaluationsKey(id)];

  it("refetches the applied list and stales the offer list after a remove", async () => {
    deleteAppliedEvaluation.mockResolvedValue(undefined);
    const { queryClient, Wrapper } = makeWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useRemoveAppliedEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", evalConfigId: "cfg-1" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteAppliedEvaluation).toHaveBeenCalledWith("env-1", "cfg-1");
    // The removed eval can be added again, so the offer list is stale too.
    // Nothing observes it here (remove is pressed with the picker closed), so
    // this only marks it.
    KEYS("env-1").forEach((queryKey) => expect(invalidate).toHaveBeenCalledWith({ queryKey }));
  });

  // A 404 means the row is already gone server-side, so the list on screen is
  // the stale one — refetching is exactly what reconciles it. Invalidating
  // only on success left the row there, refusing every retry the same way.
  it("still reconciles the list when the remove 404s because the row is already gone", async () => {
    deleteAppliedEvaluation.mockRejectedValue({ detail: "Not found", statusCode: 404 });
    const { queryClient, Wrapper } = makeWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useRemoveAppliedEvaluation(), { wrapper: Wrapper });

    result.current.mutate({ id: "env-1", evalConfigId: "cfg-1" });

    await waitFor(() => expect(result.current.isError).toBe(true));
    KEYS("env-1").forEach((queryKey) => expect(invalidate).toHaveBeenCalledWith({ queryKey }));
  });
});
