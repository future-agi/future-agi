import { describe, it, expect } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useMyEnvironments,
  useDeleteEnvironment,
  useBuildEnvironment,
  useUploadSecretFile,
  useRunSimulation,
  myEnvironmentsQueryKey,
} from "../environments";
import { MY_ENVIRONMENTS_FIXTURE } from "../_fixtures/myEnvironments";

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

describe("useMyEnvironments", () => {
  it("resolves the six fixture rows as a cloned array", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useMyEnvironments(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(6);
    expect(result.current.data[0].id).toBe("env-support-line");
    expect(result.current.data).not.toBe(MY_ENVIRONMENTS_FIXTURE);
  });
});

describe("useDeleteEnvironment", () => {
  it("removes a row from the cached list without mutating the fixture", async () => {
    const { queryClient, Wrapper } = makeWrapper();
    const { result } = renderHook(
      () => ({
        list: useMyEnvironments(),
        del: useDeleteEnvironment(),
      }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await result.current.del.mutateAsync("env-billing-chat");

    const cached = queryClient.getQueryData(myEnvironmentsQueryKey());
    expect(cached).toHaveLength(5);
    expect(cached.some((r) => r.id === "env-billing-chat")).toBe(false);
    expect(MY_ENVIRONMENTS_FIXTURE).toHaveLength(6);
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
