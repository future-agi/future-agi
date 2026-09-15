import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/harness/harness", () => ({ listHarnessJobs: vi.fn() }));

const { listHarnessJobs } = await import("src/api/harness/harness");
const {
  useMyEnvironments,
  useDeleteEnvironment,
  useBuildEnvironment,
  useUploadSecretFile,
  useRunSimulation,
  myEnvironmentsQueryKey,
} = await import("../environments");

// The raw harness-jobs payload the hook maps into table rows.
const HARNESS_JOBS = [
  {
    job: { job_id: "job-voice", metadata: { name: "Customer Support Line" } },
    status: { stage: "completed", updated_at: "2026-09-15T09:00:00Z" },
    credentials: { detected_connectors: ["livekit"] },
  },
  {
    job: { job_id: "job-chat", metadata: { name: "Billing Chat Agent" } },
    status: { stage: "running", updated_at: "2026-09-15T11:00:00Z" },
    credentials: { detected_connectors: ["http"] },
  },
];

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
  listHarnessJobs.mockReset();
  listHarnessJobs.mockResolvedValue(HARNESS_JOBS);
});

describe("useMyEnvironments", () => {
  it("maps the harness-jobs list into flat table rows", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useMyEnvironments(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data[0]).toMatchObject({
      id: "job-voice",
      name: "Customer Support Line",
      status: "completed",
      agentType: "voice",
      updatedAt: "2026-09-15T09:00:00Z",
    });
    expect(result.current.data[1]).toMatchObject({
      id: "job-chat",
      status: "running",
      agentType: "text",
    });
  });

  it("maps a non-array payload to an empty list", async () => {
    listHarnessJobs.mockResolvedValue(null);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useMyEnvironments(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});

describe("useDeleteEnvironment", () => {
  it("removes the matching raw job from the cached list", async () => {
    const { queryClient, Wrapper } = makeWrapper();
    const { result } = renderHook(
      () => ({
        list: useMyEnvironments(),
        del: useDeleteEnvironment(),
      }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await result.current.del.mutateAsync("job-chat");

    // The cache holds the RAW { job, status } items, not the mapped rows.
    const cached = queryClient.getQueryData(myEnvironmentsQueryKey());
    expect(cached).toHaveLength(1);
    expect(cached.some((item) => item?.job?.job_id === "job-chat")).toBe(false);
  });
});

describe("useBuildEnvironment", () => {
  it("resolves an env id without echoing the (possibly secret-bearing) source", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useBuildEnvironment(), {
      wrapper: Wrapper,
    });
    const source = { kind: "platform", apiKey: "sk-secret" };
    const out = await result.current.mutateAsync(source);
    expect(out.envId).toMatch(/^env-/);
    expect(out.source).toBeUndefined();
  });
});

describe("useUploadSecretFile", () => {
  it("returns a secret reference and file metadata, never the contents", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useUploadSecretFile(), {
      wrapper: Wrapper,
    });
    const file = new File(["SECRET=1"], "creds.json", {
      type: "application/json",
    });
    const out = await result.current.mutateAsync({ file });
    expect(out.secret_ref).toMatch(/^sref-/);
    expect(out.name).toBe("creds.json");
    expect(out.size).toBe(file.size);
    expect(out).not.toHaveProperty("contents");
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
