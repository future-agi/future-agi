import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
// Save keys are counted so a test can tell a reused key from a fresh one.
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
  useEnvironmentVersions,
  useVersionDiff,
  useSuiteMembers,
  useRunPlan,
  useMoveDefault,
  useDiscardAgentVersion,
  useRetryAgentVersion,
  useChangeCredentials,
  useCreateAgentVersion,
  useRefreshWhenChecked,
  useNudges,
  useAnswerNudge,
  useStopNudge,
  useDrafts,
  useSaveDrafts,
  useDiscardDraft,
  draftsKey,
  toDrafts,
  nudgesKey,
  nudgeFromApi,
  toNudges,
  canMakeDefault,
  runPlanKey,
  environmentVersionsKey,
  versionDiffKey,
  suiteMembersKey,
  toVersions,
  versionFromApi,
  isVersioningDisabled,
  isStuck,
} = await import("../versions");
const { harnessEnvironmentKey, harnessJobQuery } = await import(
  "../environment"
);
const { harnessScenariosListKey } = await import("../scenariosHooks");

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const RESPONSE = {
  agent: [
    {
      id: "a1",
      label: "v4",
      status: "active",
      is_default: true,
      created_at: "2026-10-15T10:00:00Z",
    },
  ],
  environment: [
    {
      id: "e2",
      label: "v2",
      status: "active",
      is_default: true,
      created_by: { id: "u1", name: "Asha" },
      origin: "chat",
      summary: "1 tool added",
      parent_id: "e1",
      derived_from_agent_version_id: "a1",
    },
    {
      id: "e1",
      label: "v1",
      status: "active",
      is_default: false,
      origin: "backfill",
    },
  ],
  suite: [
    {
      id: "s3",
      label: "v3",
      status: "draft",
      is_default: true,
      scenario_count: 12,
    },
  ],
};

beforeEach(() => {
  // The hooks bring their own retry rule; no delay keeps retries fast here.
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  axios.get.mockReset();
  axios.post.mockReset();
  axios.delete.mockReset();
  keyCount = 0;
});

describe("versionFromApi / toVersions", () => {
  it("maps the wire shape to camelCase", () => {
    expect(versionFromApi(RESPONSE.environment[0])).toEqual({
      id: "e2",
      label: "v2",
      status: "active",
      isDefault: true,
      createdAt: null,
      createdBy: "Asha",
      origin: "chat",
      summary: "1 tool added",
      hash: null,
      parentId: "e1",
      derivedFromAgentVersionId: "a1",
      scenarioCount: null,
      source: null,
      prompt: null,
      preflightStatus: null,
      preflightReport: null,
      preflightedAt: null,
      checkStartedAt: null,
      credentialNames: null,
      requiredVariableNames: null,
      activationOutcome: null,
      requestedHere: false,
      pickable: false,
      discardable: false,
    });
  });

  it("maps an agent version's check, credential names and activation", () => {
    const report = {
      checks: [
        {
          id: "credentials_present",
          label: "Target credentials",
          status: "failed",
          missing: ["API_KEY"],
        },
      ],
      missing: ["API_KEY"],
      invalid: [],
      error: null,
    };
    expect(
      versionFromApi({
        id: "a2",
        status: "draft",
        preflight_status: "failed",
        preflight_report: report,
        preflighted_at: "2026-10-16T10:00:00Z",
        check_started_at: "2026-10-16T10:05:00Z",
        credential_names: ["API_KEY", "REGION"],
        required_variable_names: ["API_KEY"],
        activation_outcome: "environment_busy",
        requested_here: true,
        pickable: false,
      }),
    ).toMatchObject({
      preflightStatus: "failed",
      preflightReport: report,
      preflightedAt: "2026-10-16T10:00:00Z",
      checkStartedAt: "2026-10-16T10:05:00Z",
      credentialNames: ["API_KEY", "REGION"],
      requiredVariableNames: ["API_KEY"],
      activationOutcome: "environment_busy",
      requestedHere: true,
      pickable: false,
    });
    expect(versionFromApi({ id: "a3", pickable: true }).pickable).toBe(true);
    expect(versionFromApi({ id: "a3", discardable: true }).discardable).toBe(
      true,
    );
  });

  it("finds each piece's default and tells an empty history apart", () => {
    const versions = toVersions(RESPONSE);
    expect(versions.defaults.environment.label).toBe("v2");
    expect(versions.defaults.agent.label).toBe("v4");
    expect(versions.defaults.suite.scenarioCount).toBe(12);
    expect(versions.empty).toBe(false);

    const none = toVersions({ agent: [], environment: [], suite: [] });
    expect(none.empty).toBe(true);
    expect(none.defaults.environment).toBeNull();
    expect(toVersions(undefined).empty).toBe(true);
  });
});

describe("useEnvironmentVersions", () => {
  it("reads the environment's versions under its own key", async () => {
    axios.get.mockResolvedValue({ data: RESPONSE });
    const { result } = renderHook(() => useEnvironmentVersions("env-1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/versions/",
    );
    expect(result.current.data.environment.map((v) => v.label)).toEqual([
      "v2",
      "v1",
    ]);
    expect(client.getQueryData(environmentVersionsKey("env-1"))).toEqual(
      RESPONSE,
    );
    expect(environmentVersionsKey("env-1")).toEqual([
      "harness-env-versions",
      "env-1",
    ]);
  });

  it("asks nothing while versioning is off or without an id", () => {
    renderHook(() => useEnvironmentVersions("env-1", { enabled: false }), {
      wrapper,
    });
    renderHook(() => useEnvironmentVersions(undefined), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe("useVersionDiff", () => {
  it("reads one version's changes against its parent", async () => {
    axios.get.mockResolvedValue({
      data: { summary: "1 tool added", diff: {} },
    });
    const { result } = renderHook(
      () => useVersionDiff("env-1", "environment", "e2"),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/versions/environment/e2/diff/",
      { params: undefined },
    );
  });

  it("passes the version to compare against and keys on it", async () => {
    axios.get.mockResolvedValue({ data: { summary: "", diff: null } });
    const { result } = renderHook(
      () => useVersionDiff("env-1", "suite", "s3", "s1"),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/versions/suite/s3/diff/",
      { params: { against: "s1" } },
    );
    expect(versionDiffKey("env-1", "suite", "s3", "s1")).toEqual([
      "harness-env-versions",
      "env-1",
      "diff",
      "suite",
      "s3",
      "s1",
    ]);
  });

  it("waits for a version id", () => {
    renderHook(() => useVersionDiff("env-1", "suite", undefined), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe("useSuiteMembers", () => {
  it("reads a suite version's scenarios", async () => {
    const members = [
      {
        scenario_key: "refund",
        number: 1,
        name: "Refund",
        use_case: "billing",
      },
    ];
    axios.get.mockResolvedValue({ data: members });
    const { result } = renderHook(() => useSuiteMembers("env-1", "s3"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/versions/suite/s3/members/",
    );
    expect(result.current.data).toEqual(members);
    expect(suiteMembersKey("env-1", "s3")).toEqual([
      "harness-env-versions",
      "env-1",
      "members",
      "s3",
    ]);
  });

  it("treats an odd response as no scenarios", async () => {
    axios.get.mockResolvedValue({ data: { unexpected: true } });
    const { result } = renderHook(() => useSuiteMembers("env-1", "s3"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});

describe("useRunPlan", () => {
  const PLAN = {
    versions: {
      agent: { id: "a1", label: "v4" },
      environment: { id: "e1", label: "v1" },
      suite: { id: "s3", label: "v3" },
    },
    warnings: [
      { code: "suite_unproved", message: "Suite v3 unproved", piece: "suite" },
    ],
    bundle_cached: false,
  };

  it("asks with only the picks that are set, keyed under the versions", async () => {
    axios.get.mockResolvedValue({ data: PLAN });
    const { result } = renderHook(
      () => useRunPlan("env-1", { environment: "e1", suite: undefined }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/run-plan/",
      { params: { environment_version_id: "e1" } },
    );
    expect(result.current.data).toEqual(PLAN);
    expect(runPlanKey("env-1", { environment: "e1" })).toEqual([
      "harness-env-versions",
      "env-1",
      "run-plan",
      null,
      "e1",
      null,
    ]);
    expect(
      client.getQueryData(runPlanKey("env-1", { environment: "e1" })),
    ).toEqual(PLAN);
  });

  it("asks with no params for the defaults", async () => {
    axios.get.mockResolvedValue({ data: PLAN });
    const { result } = renderHook(() => useRunPlan("env-1", {}), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/run-plan/",
      { params: {} },
    );
  });

  it("asks nothing until enabled", () => {
    renderHook(() => useRunPlan("env-1", {}, { enabled: false }), {
      wrapper,
    });
    expect(axios.get).not.toHaveBeenCalled();
  });

  it.each([
    ["a refusal", { statusCode: 422, error: "version_not_runnable" }, 1],
    ["a conflict", { statusCode: 409 }, 1],
    ["a server failure", { statusCode: 500 }, 3],
    ["a network failure", { message: "Network Error" }, 3],
  ])(
    "asks again only after a server or network failure: %s",
    async (_, error, calls) => {
      axios.get.mockRejectedValue(error);
      const { result } = renderHook(() => useRunPlan("env-1", {}), { wrapper });
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(axios.get).toHaveBeenCalledTimes(calls);
    },
  );
});

describe("useMoveDefault", () => {
  const PATH =
    "/simulate/api/harness-environments/env-1/versions/environment/e1/default/";
  // The factories the followers' queries use, so a drifted literal fails here.
  const FOLLOWERS = [
    environmentVersionsKey("env-1"),
    harnessEnvironmentKey("env-1"),
    harnessJobQuery("env-1").queryKey,
    harnessScenariosListKey("env-1"),
  ];
  const invalidated = (spy) => spy.mock.calls.map(([filters]) => filters);

  it("posts the default the user saw and seeds the versions with the answer", async () => {
    axios.post.mockResolvedValue({ data: RESPONSE });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });

    await act(() =>
      result.current.mutateAsync({
        piece: "environment",
        versionId: "e1",
        expectedDefaultId: "e2",
      }),
    );

    expect(axios.post).toHaveBeenCalledWith(PATH, {
      expected_default_id: "e2",
    });
    expect(client.getQueryData(environmentVersionsKey("env-1"))).toEqual(
      RESPONSE,
    );
    // Prefixes, never exact: the run plan, diffs and members sit under them.
    expect(invalidated(spy)).toEqual(
      FOLLOWERS.map((queryKey) => ({ queryKey })),
    );
  });

  it("refreshes the nested queries a move makes stale", async () => {
    axios.post.mockResolvedValue({ data: RESPONSE });
    const planKey = runPlanKey("env-1", { environment: "e1" });
    client.setQueryData(planKey, { warnings: [] });
    client.setQueryData(["harness-scenarios", "env-1", { page: 0 }], {});
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });

    await act(() =>
      result.current.mutateAsync({
        piece: "suite",
        versionId: "s1",
        expectedDefaultId: null,
      }),
    );

    expect(client.getQueryState(planKey).isInvalidated).toBe(true);
    expect(
      client.getQueryState(["harness-scenarios", "env-1", { page: 0 }])
        .isInvalidated,
    ).toBe(true);
  });

  it("sends a null default when none was shown", async () => {
    axios.post.mockResolvedValue({ data: RESPONSE });
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync({ piece: "environment", versionId: "e1" }),
    );
    expect(axios.post).toHaveBeenCalledWith(PATH, {
      expected_default_id: null,
    });
  });

  it("refreshes after a conflict so the user picks from what is current", async () => {
    const conflict = {
      statusCode: 409,
      error: "version_conflict",
      message: "The default changed to v3 since you opened this; pick again",
      piece: "environment",
    };
    axios.post.mockRejectedValue(conflict);
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });

    await act(() =>
      result.current
        .mutateAsync({
          piece: "environment",
          versionId: "e1",
          expectedDefaultId: "e2",
        })
        .catch(() => {}),
    );

    await waitFor(() => expect(result.current.error).toEqual(conflict));
    expect(invalidated(spy)).toEqual(
      FOLLOWERS.map((queryKey) => ({ queryKey })),
    );
  });

  it("refreshes the versions when the version can no longer be picked", async () => {
    axios.post.mockRejectedValue({
      statusCode: 422,
      error: "version_not_pickable",
      message: "This version was discarded",
      piece: "environment",
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });

    await act(() =>
      result.current
        .mutateAsync({
          piece: "environment",
          versionId: "e1",
          expectedDefaultId: "e2",
        })
        .catch(() => {}),
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidated(spy)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
    ]);
  });

  it.each([
    [
      "the combination isn't prepared",
      {
        statusCode: 422,
        error: "rollback_unavailable",
        message: "This combination hasn't been prepared yet",
        piece: "environment",
      },
    ],
    [
      "the environment is busy",
      {
        statusCode: 409,
        error: "environment_busy",
        message:
          "The environment is being built or extended; try again when it finishes",
      },
    ],
  ])("leaves the cache alone when %s", async (_, refusal) => {
    axios.post.mockRejectedValue(refusal);
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });

    await act(() =>
      result.current
        .mutateAsync({
          piece: "environment",
          versionId: "e1",
          expectedDefaultId: "e2",
        })
        .catch(() => {}),
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).not.toHaveBeenCalled();
  });

  it("handles its own errors, so no generic toast shows", () => {
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });
    act(() => {
      result.current.mutate({ piece: "environment", versionId: "e1" });
    });
    const [mutation] = client.getMutationCache().getAll();
    expect(mutation.options.meta).toEqual({ errorHandled: true });
  });
});

describe("canMakeDefault", () => {
  const version = (overrides) =>
    versionFromApi({ id: "x", status: "active", ...overrides });

  it("offers an active agent, environment or suite version that isn't the default", () => {
    expect(canMakeDefault("agent", version({ pickable: true }))).toBe(true);
    expect(canMakeDefault("environment", version())).toBe(true);
    expect(canMakeDefault("suite", version())).toBe(true);
  });

  it("offers an agent version only when the server says it can be picked", () => {
    expect(canMakeDefault("agent", version())).toBe(false);
    expect(canMakeDefault("agent", version({ pickable: false }))).toBe(false);
  });

  it("offers nothing for the default, a draft or a discarded one", () => {
    expect(canMakeDefault("environment", version({ is_default: true }))).toBe(
      false,
    );
    expect(canMakeDefault("environment", version({ status: "draft" }))).toBe(
      false,
    );
    expect(canMakeDefault("suite", version({ status: "discarded" }))).toBe(
      false,
    );
    expect(canMakeDefault("agent", version({ status: "draft" }))).toBe(false);
    expect(canMakeDefault("piece", version())).toBe(false);
  });
});

describe("errors", () => {
  const notFound = Object.assign(new Error("not found"), { statusCode: 404 });
  const serverError = Object.assign(new Error("boom"), { statusCode: 500 });

  it("does not ask again after a 404 (versioning off or gone)", async () => {
    axios.get.mockRejectedValue(notFound);
    const { result } = renderHook(() => useEnvironmentVersions("env-1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("retries other failures twice", async () => {
    axios.get.mockRejectedValue(serverError);
    const { result } = renderHook(
      () => useVersionDiff("env-1", "environment", "e2"),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(axios.get).toHaveBeenCalledTimes(3);
  });

  it("marks every query as handled so no generic error toast shows", async () => {
    axios.get.mockResolvedValue({ data: [] });
    renderHook(() => useEnvironmentVersions("env-1"), { wrapper });
    renderHook(() => useVersionDiff("env-1", "suite", "s3"), { wrapper });
    renderHook(() => useSuiteMembers("env-1", "s3"), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(3));
    const metas = client
      .getQueryCache()
      .getAll()
      .map((query) => query.meta?.errorHandled);
    expect(metas).toEqual([true, true, true]);
  });
});

describe("isVersioningDisabled", () => {
  it("recognises the server switching versioning off", () => {
    expect(
      isVersioningDisabled({ statusCode: 404, error: "versioning_disabled" }),
    ).toBe(true);
  });

  it("does not treat any other failure as versioning off", () => {
    expect(isVersioningDisabled({ statusCode: 404 })).toBe(false);
    expect(
      isVersioningDisabled({ statusCode: 404, error: "version_not_found" }),
    ).toBe(false);
    expect(
      isVersioningDisabled({ statusCode: 500, error: "versioning_disabled" }),
    ).toBe(false);
    expect(isVersioningDisabled(undefined)).toBe(false);
    expect(isVersioningDisabled(null)).toBe(false);
  });
});

describe("agent version checks", () => {
  const agentItem = (overrides) => ({
    id: "a5",
    label: "v5",
    status: "draft",
    is_default: false,
    ...overrides,
  });
  const intervalOf = () => {
    const query = client
      .getQueryCache()
      .find({ queryKey: environmentVersionsKey("env-1"), exact: true });
    return query.options.refetchInterval(query);
  };

  const pendingHere = (overrides) =>
    agentItem({
      preflight_status: "pending",
      requested_here: true,
      created_at: new Date().toISOString(),
      ...overrides,
    });
  const intervalFor = async (agent) => {
    axios.get.mockResolvedValue({
      data: { ...RESPONSE, agent: [...agent, ...RESPONSE.agent] },
    });
    const { result } = renderHook(() => useEnvironmentVersions("env-1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    return intervalOf();
  };

  it("polls the versions while a check this environment asked for runs", async () => {
    expect(await intervalFor([pendingHere()])).toBe(5000);
  });

  it("does not poll for another environment's check", async () => {
    expect(await intervalFor([pendingHere({ requested_here: false })])).toBe(
      false,
    );
  });

  const longAgo = () => new Date(Date.now() - 16 * 60 * 1000).toISOString();

  it("stops polling once a check is stuck", async () => {
    expect(await intervalFor([pendingHere({ created_at: longAgo() })])).toBe(
      false,
    );
  });

  it("dates a stuck check from its last start", async () => {
    const restarted = pendingHere({
      created_at: longAgo(),
      preflighted_at: new Date().toISOString(),
    });
    expect(await intervalFor([restarted])).toBe(5000);
  });

  it("dates a check asked for again from the server's record, so a reload keeps polling", async () => {
    const restarted = pendingHere({
      created_at: longAgo(),
      preflighted_at: longAgo(),
      check_started_at: new Date().toISOString(),
    });
    expect(await intervalFor([restarted])).toBe(5000);
    expect(isStuck(versionFromApi(restarted))).toBe(false);
    expect(
      isStuck(versionFromApi({ ...restarted, check_started_at: longAgo() })),
    ).toBe(true);
  });

  it("polls until a passed check's move of the default is recorded", async () => {
    const passedHere = pendingHere({
      preflight_status: "passed",
      preflighted_at: new Date().toISOString(),
    });
    expect(await intervalFor([passedHere])).toBe(5000);
    expect(
      await intervalFor([{ ...passedHere, activation_outcome: "activated" }]),
    ).toBe(false);
  });

  it.each(["passed", "failed", "legacy", "discarded", null])(
    "does not poll when no agent version is pending (%s)",
    async (status) => {
      axios.get.mockResolvedValue({
        data: {
          ...RESPONSE,
          agent: [agentItem({ preflight_status: status })],
        },
      });
      const { result } = renderHook(() => useEnvironmentVersions("env-1"), {
        wrapper,
      });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(intervalOf()).toBe(false);
    },
  );

  it("refreshes the detail and the job once the last check lands", () => {
    const spy = vi.spyOn(client, "invalidateQueries");
    const pending = [
      versionFromApi(
        agentItem({ preflight_status: "pending", requested_here: true }),
      ),
    ];
    const passed = [versionFromApi(agentItem({ preflight_status: "passed" }))];
    const { rerender } = renderHook(
      ({ versions }) => useRefreshWhenChecked("env-1", versions),
      { wrapper, initialProps: { versions: pending } },
    );
    expect(spy).not.toHaveBeenCalled();
    rerender({ versions: passed });
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: harnessEnvironmentKey("env-1") },
      { queryKey: harnessJobQuery("env-1").queryKey },
      { queryKey: nudgesKey("env-1") },
    ]);
    rerender({ versions: passed });
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("refreshes nothing when nothing was pending", () => {
    const spy = vi.spyOn(client, "invalidateQueries");
    const passed = [versionFromApi(agentItem({ preflight_status: "passed" }))];
    const { rerender } = renderHook(
      ({ versions }) => useRefreshWhenChecked("env-1", versions),
      { wrapper, initialProps: { versions: undefined } },
    );
    rerender({ versions: passed });
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("agent version actions", () => {
  const invalidated = (spy) => spy.mock.calls.map(([filters]) => filters);

  it("moves the agent default through the shared default path", async () => {
    axios.post.mockResolvedValue({ data: RESPONSE });
    const { result } = renderHook(() => useMoveDefault("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync({
        piece: "agent",
        versionId: "a3",
        expectedDefaultId: "a1",
      }),
    );
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/versions/agent/a3/default/",
      { expected_default_id: "a1" },
    );
  });

  it.each([
    ["discards", useDiscardAgentVersion, "discard"],
    ["checks again", useRetryAgentVersion, "retry"],
  ])(
    "%s a version and seeds the versions with the answer",
    async (_, useAction, action) => {
      axios.post.mockResolvedValue({ data: RESPONSE });
      const spy = vi.spyOn(client, "invalidateQueries");
      const planKey = runPlanKey("env-1", { agent: "a3" });
      client.setQueryData(planKey, { warnings: [] });
      const { result } = renderHook(() => useAction("env-1"), { wrapper });

      await act(() => result.current.mutateAsync({ versionId: "a3" }));

      expect(axios.post).toHaveBeenCalledWith(
        `/simulate/api/harness-environments/env-1/versions/agent/a3/${action}/`,
        {},
      );
      expect(client.getQueryData(environmentVersionsKey("env-1"))).toEqual(
        RESPONSE,
      );
      // A prefix, so the run plan under it is asked again too.
      expect(invalidated(spy)).toEqual([
        { queryKey: environmentVersionsKey("env-1") },
      ]);
      expect(client.getQueryState(planKey).isInvalidated).toBe(true);
    },
  );

  it.each([
    [useDiscardAgentVersion, "agent_version_in_use"],
    [useDiscardAgentVersion, "version_not_discardable"],
    [useRetryAgentVersion, "version_not_retryable"],
  ])("refreshes the versions after a 422 (%#)", async (useAction, code) => {
    axios.post.mockRejectedValue({ statusCode: 422, error: code });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useAction("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync({ versionId: "a3" }).catch(() => {}),
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidated(spy)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
    ]);
  });

  it("leaves the cache alone after a server failure", async () => {
    axios.post.mockRejectedValue({ statusCode: 500 });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useRetryAgentVersion("env-1"), {
      wrapper,
    });
    await act(() =>
      result.current.mutateAsync({ versionId: "a3" }).catch(() => {}),
    );
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("versionFromApi source and prompt", () => {
  it("maps an agent version's source and the default's prompt", () => {
    const source = {
      kind: "github",
      repository: "acme/agent",
      ref: "main",
      commit_sha: "a1b2c3d4e5",
      visibility: "public",
    };
    expect(
      versionFromApi({ id: "a2", source, prompt: "Be brief." }),
    ).toMatchObject({ source, prompt: "Be brief." });
    expect(
      versionFromApi({ id: "a2", source: null, prompt: null }),
    ).toMatchObject({ source: null, prompt: null });
    expect(versionFromApi({ id: "a2", prompt: "" }).prompt).toBe("");
  });
});

describe("useCreateAgentVersion", () => {
  const PATH = "/simulate/api/harness-environments/env-1/versions/agent/";

  it("posts the change for the environment and refreshes what follows it", async () => {
    const answer = { agent_version: { id: "a7", label: "v7" } };
    axios.post.mockResolvedValue({ data: answer });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useCreateAgentVersion("env-1"), {
      wrapper,
    });

    let data;
    await act(async () => {
      data = await result.current.mutateAsync({ prompt: "Be brief." });
    });

    expect(axios.post).toHaveBeenCalledWith(PATH, { prompt: "Be brief." });
    expect(data).toEqual(answer);
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
      { queryKey: ["harness-environment", "env-1"] },
      { queryKey: ["harness-job", "env-1"] },
    ]);
  });

  it("refreshes nothing when the change is refused", async () => {
    axios.post.mockRejectedValue({ statusCode: 422, error: "agent_unchanged" });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useCreateAgentVersion("env-1"), {
      wrapper,
    });
    await act(async () => {
      await result.current
        .mutateAsync({ archive_artifact_id: "src-1" })
        .catch(() => {});
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useChangeCredentials", () => {
  const PATH = "/simulate/api/harness-jobs/secret-values/";
  const NEW_VERSION = { id: "a6", label: "v6", status: "draft" };

  it("posts the values for the environment and refreshes what follows them", async () => {
    axios.post.mockResolvedValue({ data: { agent_version: NEW_VERSION } });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useChangeCredentials("env-1"), {
      wrapper,
    });

    let answer;
    await act(async () => {
      answer = await result.current.mutateAsync({
        values: { API_KEY: "sk-secret" },
      });
    });

    expect(axios.post).toHaveBeenCalledWith(PATH, {
      environment_id: "env-1",
      environment_values: { API_KEY: "sk-secret" },
    });
    expect(answer).toEqual({ agent_version: NEW_VERSION });
    // By prefix: every query under the environment detail and job follows.
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
      { queryKey: ["harness-environment", "env-1"] },
      { queryKey: ["harness-job", "env-1"] },
    ]);
  });

  it("sends staged credential files by their refs", async () => {
    axios.post.mockResolvedValue({ data: { agent_version: NEW_VERSION } });
    const ref = {
      manager: "platform-vault",
      key: "harness-google-adc-1",
      version: "1",
      purpose: "target_provider",
    };
    const { result } = renderHook(() => useChangeCredentials("env-1"), {
      wrapper,
    });
    await act(() => result.current.mutateAsync({ values: {}, files: [ref] }));
    expect(axios.post).toHaveBeenCalledWith(PATH, {
      environment_id: "env-1",
      environment_values: {},
      files: [ref],
    });
  });

  it("keeps no settled request, values included, in the cache", async () => {
    axios.post.mockResolvedValue({ data: { agent_version: NEW_VERSION } });
    const { result, unmount } = renderHook(
      () => useChangeCredentials("env-1"),
      { wrapper },
    );
    await act(() =>
      result.current.mutateAsync({ values: { API_KEY: "sk-secret" } }),
    );
    const [mutation] = client.getMutationCache().getAll();
    expect(mutation.options.meta).toEqual({ errorHandled: true });
    expect(mutation.options.gcTime).toBe(0);
    unmount();
    await waitFor(() =>
      expect(client.getMutationCache().getAll()).toHaveLength(0),
    );
  });

  it("refreshes nothing when the values are refused", async () => {
    axios.post.mockRejectedValue({
      statusCode: 413,
      error: "credential_blob_too_large",
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useChangeCredentials("env-1"), {
      wrapper,
    });
    await act(() =>
      result.current.mutateAsync({ values: { API_KEY: "x" } }).catch(() => {}),
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("nudges", () => {
  const PATH = "/simulate/api/harness-environments/env-1/nudges/";
  const NUDGE = {
    id: "n1",
    target: "environment",
    state: "pending",
    facts: {
      source: { kind: "github", changed: true, from: "abc", to: "def" },
      changed_files: [
        { path: "tools/a.py", status: "modified", previous_path: null },
      ],
      files_unavailable: null,
      prompt_changed: true,
      variables: { added: ["NEW_KEY"], removed: [], changed: [], unknown: [] },
      settings_changed: ["config.phone_number"],
      agent_default_moved: true,
    },
    trigger_agent_version: { id: "a2", label: "v2" },
    replaced_by: null,
    card_message_id: "m1",
    queued_behind_input: false,
  };
  const SUITE = {
    id: "n2",
    target: "suite",
    state: "pending",
    facts: {
      environment_diff: {
        "tools.get_order": { added: true },
        "sub_goals.refund_policy": { changed: true },
      },
      environment_summary: "1 tool added",
      from_environment_version_id: "e1",
    },
    base_environment_version: { id: "e2", label: "v2" },
  };
  const intervalOf = () => {
    const query = client
      .getQueryCache()
      .find({ queryKey: nudgesKey("env-1"), exact: true });
    return query.options.refetchInterval(query);
  };

  it("maps facts deeply to camelCase, leaving the suite diff's keys as sent", () => {
    const nudge = nudgeFromApi(NUDGE);
    expect(nudge).toMatchObject({
      triggerAgentVersion: { id: "a2", label: "v2" },
      cardMessageId: "m1",
      queuedBehindInput: false,
      facts: {
        changedFiles: [{ path: "tools/a.py", previousPath: null }],
        filesUnavailable: null,
        promptChanged: true,
        settingsChanged: ["config.phone_number"],
        agentDefaultMoved: true,
      },
    });
    const suite = nudgeFromApi(SUITE);
    expect(Object.keys(suite.facts.environmentDiff)).toEqual([
      "tools.get_order",
      "sub_goals.refund_policy",
    ]);
    expect(suite.facts.environmentSummary).toBe("1 tool added");
    expect(suite.facts.fromEnvironmentVersionId).toBe("e1");
  });

  it("lists an environment's nudges, by id and the pending one per target", async () => {
    axios.get.mockResolvedValue({ data: [NUDGE, SUITE] });
    const { result } = renderHook(() => useNudges("env-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(PATH);
    expect(result.current.data.list.map((one) => one.id)).toEqual(["n1", "n2"]);
    expect(result.current.data.byId.n2.target).toBe("suite");
    expect(result.current.data.pendingEnvironment.id).toBe("n1");
    expect(result.current.data.pendingSuite.id).toBe("n2");
  });

  it("asks nothing while versioning is off", () => {
    renderHook(() => useNudges("env-1", { enabled: false }), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("polls every 30 s, and every 5 s while a rebuild is queued or running", async () => {
    axios.get.mockResolvedValue({ data: [NUDGE] });
    const { result } = renderHook(() => useNudges("env-1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(intervalOf()).toBe(30000);
    client.setQueryData(nudgesKey("env-1"), [{ ...NUDGE, state: "running" }]);
    expect(intervalOf()).toBe(5000);
    client.setQueryData(nudgesKey("env-1"), [{ ...NUDGE, state: "queued" }]);
    expect(intervalOf()).toBe(5000);
  });

  it("stops polling once the list is gone", async () => {
    client.setQueryData(nudgesKey("env-1"), [{ ...NUDGE, state: "running" }]);
    axios.get.mockRejectedValue({
      statusCode: 404,
      error: "versioning_disabled",
    });
    const { result } = renderHook(() => useNudges("env-1"), { wrapper });
    const { status } = await act(() => result.current.refetch());
    expect(status).toBe("error");
    expect(intervalOf()).toBe(false);
  });

  it("skips a nudge, updates its row and refreshes what follows it", async () => {
    client.setQueryData(nudgesKey("env-1"), [NUDGE, SUITE]);
    axios.post.mockResolvedValue({ data: { ...NUDGE, state: "answered_no" } });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useAnswerNudge("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync({ nudgeId: "n1", answer: "no" }),
    );
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/nudges/n1/answer/",
      { answer: "no" },
    );
    expect(
      client.getQueryData(nudgesKey("env-1")).map((one) => one.state),
    ).toEqual(["answered_no", "pending"]);
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: nudgesKey("env-1") },
      { queryKey: [...environmentVersionsKey("env-1"), "run-plan"] },
      { queryKey: harnessJobQuery("env-1").queryKey },
    ]);
  });

  it("refreshes the list when the nudge was closed meanwhile", async () => {
    axios.post.mockRejectedValue({
      statusCode: 409,
      error: "nudge_closed",
      state: "replaced",
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useAnswerNudge("env-1"), { wrapper });
    await act(() =>
      result.current
        .mutateAsync({ nudgeId: "n1", answer: "no" })
        .catch(() => {}),
    );
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: nudgesKey("env-1") },
    ]);
  });

  it("keeps a landed rebuild's diffs as sent, and names the active rebuild", () => {
    const landed = {
      ...NUDGE,
      state: "applied",
      summary: {
        set_aside: { started_from_label: "v1" },
        update: {
          sub_goals: { added: ["verify_identity"] },
          changed: { angry_caller_v2: ["persona"] },
        },
        draft_edits: { contract: { use_cases: {} }, edited_by: "Asha" },
      },
    };
    const nudge = nudgeFromApi(landed);
    expect(nudge.summary.setAside.startedFromLabel).toBe("v1");
    expect(Object.keys(nudge.summary.update)).toEqual(["sub_goals", "changed"]);
    expect(Object.keys(nudge.summary.update.changed)).toEqual([
      "angry_caller_v2",
    ]);
    expect(nudge.summary.draftEdits).toEqual({
      contract: { use_cases: {} },
      edited_by: "Asha",
    });
    expect(toNudges([landed]).active).toBeNull();
    const running = { ...SUITE, state: "running" };
    expect(toNudges([landed, running]).active.id).toBe("n2");
  });

  it("stops a rebuild, updates its row and refreshes what follows it", async () => {
    client.setQueryData(nudgesKey("env-1"), [
      { ...NUDGE, state: "running" },
      SUITE,
    ]);
    axios.post.mockResolvedValue({
      data: { ...NUDGE, state: "failed", error: { code: "stopped_by_user" } },
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useStopNudge("env-1"), { wrapper });
    await act(() => result.current.mutateAsync({ nudgeId: "n1" }));
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/nudges/n1/stop/",
      {},
    );
    expect(
      client.getQueryData(nudgesKey("env-1")).map((one) => one.state),
    ).toEqual(["failed", "pending"]);
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: nudgesKey("env-1") },
      { queryKey: [...environmentVersionsKey("env-1"), "run-plan"] },
      { queryKey: harnessJobQuery("env-1").queryKey },
    ]);
  });

  it("refreshes the list when the rebuild being stopped closed meanwhile", async () => {
    axios.post.mockRejectedValue({
      statusCode: 409,
      error: "nudge_closed",
      state: "applied",
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useStopNudge("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync({ nudgeId: "n1" }).catch(() => {}),
    );
    expect(spy.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: nudgesKey("env-1") },
    ]);
  });

  it.each(["nudges_disabled", "rebuild_running", "environment_building"])(
    "refreshes nothing when a rebuild is refused (%s)",
    async (code) => {
      axios.post.mockRejectedValue({ statusCode: 409, error: code });
      const spy = vi.spyOn(client, "invalidateQueries");
      const { result } = renderHook(() => useAnswerNudge("env-1"), {
        wrapper,
      });
      await act(() =>
        result.current
          .mutateAsync({ nudgeId: "n1", answer: "yes" })
          .catch(() => {}),
      );
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(spy).not.toHaveBeenCalled();
    },
  );
});

describe("drafts", () => {
  const DRAFTS = {
    environment: {
      id: "ed",
      label: null,
      status: "draft",
      origin: "chat",
      parent_id: "e2",
      stale: false,
      revision: 3,
      last_edited_by: { id: "u1", name: "Asha" },
      updated_at: "2026-10-16T10:00:00Z",
      parent_label: "v2",
      default_label: "v2",
      changes: { tools: 1, rules: 2, sub_goals: 0, world_files: 0 },
    },
    suite: null,
    set_aside: [],
  };
  const PIECES = [
    { piece: "environment", parentId: "e2", revision: 3 },
    { piece: "suite", parentId: "s3", revision: 1 },
  ];
  const BODY = [
    { piece: "environment", expected_parent_id: "e2", expected_revision: 3 },
    { piece: "suite", expected_parent_id: "s3", expected_revision: 1 },
  ];
  const SAVE_PATH = "/simulate/api/harness-environments/env-1/drafts/save/";
  const invalidated = (spy) => spy.mock.calls.map(([filters]) => filters);

  it("nests the drafts under the versions, so a versions refresh reaches them", () => {
    expect(draftsKey("env-1")).toEqual([
      ...environmentVersionsKey("env-1"),
      "drafts",
    ]);
  });

  it("maps each open piece and tells whether any is open or stale", () => {
    const drafts = toDrafts({
      ...DRAFTS,
      suite: { ...DRAFTS.environment, id: "sd", stale: true, changes: {} },
    });
    expect(drafts.environment).toMatchObject({
      id: "ed",
      status: "draft",
      parentId: "e2",
      stale: false,
      revision: 3,
      lastEditedBy: "Asha",
      updatedAt: "2026-10-16T10:00:00Z",
      parentLabel: "v2",
      defaultLabel: "v2",
      changes: { tools: 1, rules: 2, sub_goals: 0, world_files: 0 },
    });
    expect(drafts.pieces).toEqual(["environment", "suite"]);
    expect(drafts.any).toBe(true);
    expect(drafts.stale).toBe(true);
  });

  it("reads no drafts as nothing open", () => {
    expect(
      toDrafts({ environment: null, suite: null, set_aside: [] }),
    ).toMatchObject({
      environment: null,
      suite: null,
      setAside: [],
      pieces: [],
      any: false,
      stale: false,
    });
  });

  it("reads the drafts under their key, and not while versioning is off", async () => {
    axios.get.mockResolvedValue({ data: DRAFTS });
    const { result } = renderHook(() => useDrafts("env-1"), { wrapper });
    await waitFor(() => expect(result.current.data?.any).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/drafts/",
    );
    expect(client.getQueryData(draftsKey("env-1"))).toEqual(DRAFTS);

    axios.get.mockClear();
    renderHook(() => useDrafts("env-2", { enabled: false }), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("saves every piece with its parent, revision and a key, then refreshes what follows", async () => {
    axios.post.mockResolvedValue({
      data: { saved: {}, versions: RESPONSE },
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useSaveDrafts("env-1"), { wrapper });

    await act(() => result.current.mutateAsync(PIECES));

    expect(axios.post).toHaveBeenCalledWith(SAVE_PATH, {
      drafts: BODY,
      idempotency_key: "key-1",
    });
    expect(client.getQueryData(environmentVersionsKey("env-1"))).toEqual(
      RESPONSE,
    );
    // The versions prefix covers the drafts, the run plan and the nudges.
    expect(invalidated(spy)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
      { queryKey: harnessEnvironmentKey("env-1") },
      { queryKey: harnessJobQuery("env-1").queryKey },
      { queryKey: harnessScenariosListKey("env-1") },
    ]);
  });

  it("leaves out a revision it doesn't know", async () => {
    axios.post.mockResolvedValue({ data: {} });
    const { result } = renderHook(() => useSaveDrafts("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync([{ piece: "suite", parentId: null }]),
    );
    expect(axios.post.mock.calls[0][1].drafts).toEqual([
      { piece: "suite", expected_parent_id: null },
    ]);
  });

  it("keeps the key for a retry of the same drafts after a failure the server may not have seen", async () => {
    axios.post.mockRejectedValueOnce({ message: "Network Error" });
    axios.post.mockResolvedValueOnce({ data: {} });
    const { result } = renderHook(() => useSaveDrafts("env-1"), { wrapper });

    await act(() => result.current.mutateAsync(PIECES).catch(() => {}));
    await act(() => result.current.mutateAsync(PIECES));

    const keys = axios.post.mock.calls.map(([, body]) => body.idempotency_key);
    expect(keys).toEqual(["key-1", "key-1"]);
  });

  it("uses a new key after a refusal, and for other drafts", async () => {
    axios.post.mockRejectedValueOnce({
      statusCode: 409,
      error: "chat_working",
    });
    axios.post.mockRejectedValueOnce({ message: "Network Error" });
    axios.post.mockResolvedValue({ data: {} });
    const { result } = renderHook(() => useSaveDrafts("env-1"), { wrapper });

    await act(() => result.current.mutateAsync(PIECES).catch(() => {}));
    await act(() => result.current.mutateAsync(PIECES).catch(() => {}));
    await act(() => result.current.mutateAsync(PIECES.slice(0, 1)));

    const keys = axios.post.mock.calls.map(([, body]) => body.idempotency_key);
    expect(keys).toEqual(["key-1", "key-2", "key-3"]);
  });

  it.each(["draft_stale", "draft_changed", "draft_not_found"])(
    "asks for the drafts again when the save is refused with %s",
    async (code) => {
      axios.post.mockRejectedValue({ statusCode: 409, error: code });
      const spy = vi.spyOn(client, "invalidateQueries");
      const { result } = renderHook(() => useSaveDrafts("env-1"), {
        wrapper,
      });
      await act(() => result.current.mutateAsync(PIECES).catch(() => {}));
      expect(invalidated(spy)).toEqual([{ queryKey: draftsKey("env-1") }]);
    },
  );

  it("asks for the versions again after a version conflict, and for nothing on other refusals", async () => {
    axios.post.mockRejectedValueOnce({
      statusCode: 409,
      error: "version_conflict",
    });
    axios.post.mockRejectedValueOnce({
      statusCode: 409,
      error: "rebuild_running",
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useSaveDrafts("env-1"), { wrapper });
    await act(() => result.current.mutateAsync(PIECES).catch(() => {}));
    expect(invalidated(spy)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
    ]);
    spy.mockClear();
    await act(() => result.current.mutateAsync(PIECES).catch(() => {}));
    expect(spy).not.toHaveBeenCalled();
  });

  it("discards each piece with its own request, then refreshes what it overlaid", async () => {
    axios.delete.mockResolvedValue({ data: "" });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useDiscardDraft("env-1"), {
      wrapper,
    });

    await act(() => result.current.mutateAsync(["environment", "suite"]));

    expect(axios.delete.mock.calls.map(([path]) => path)).toEqual([
      "/simulate/api/harness-environments/env-1/drafts/environment/",
      "/simulate/api/harness-environments/env-1/drafts/suite/",
    ]);
    // The versions prefix covers the drafts and the discarded draft's views.
    expect(invalidated(spy)).toEqual([
      { queryKey: environmentVersionsKey("env-1") },
      { queryKey: harnessEnvironmentKey("env-1") },
      { queryKey: harnessJobQuery("env-1").queryKey },
      { queryKey: harnessScenariosListKey("env-1") },
    ]);
  });

  it("refreshes after a refused discard too, since earlier pieces may be gone", async () => {
    axios.delete.mockResolvedValueOnce({ data: "" });
    axios.delete.mockRejectedValueOnce({
      statusCode: 409,
      error: "chat_working",
    });
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useDiscardDraft("env-1"), {
      wrapper,
    });
    await act(() =>
      result.current.mutateAsync(["environment", "suite"]).catch(() => {}),
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(axios.delete).toHaveBeenCalledTimes(2);
    expect(invalidated(spy)).toContainEqual({
      queryKey: environmentVersionsKey("env-1"),
    });
  });
});
