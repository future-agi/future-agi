import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

const { default: useBuildHandoff, redactSource } = await import("../hooks/useBuildHandoff");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
);
const { BUILD_HANDOFF_COPY } = await import("../environmentOptions");

const makeWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return { Wrapper, client };
};

describe("redactSource", () => {
  it("drops apiKey and envText but keeps everything else including secretFiles", () => {
    const out = redactSource({
      kind: "repo",
      apiKey: "sk-secret",
      envText: "OPENAI_API_KEY=xyz",
      secretFiles: [{ name: "c.json", size: 3, secret_ref: "sref-1" }],
    });
    expect(out).toEqual({
      kind: "repo",
      secretFiles: [{ name: "c.json", size: 3, secret_ref: "sref-1" }],
    });
  });

  it("is null-safe", () => {
    expect(redactSource(undefined)).toEqual({});
  });
});

describe("useBuildHandoff", () => {
  beforeEach(() => {
    resetEnvironmentsStore();
    enqueueSnackbar.mockReset();
  });

  it("stores a redacted draft (no raw secrets) and fires the snackbar", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useBuildHandoff(), {
      wrapper: Wrapper,
    });

    act(() => {
      result.current({
        kind: "platform",
        apiKey: "sk-secret",
        envText: "TOKEN=leak",
        agentId: "agent-1",
      });
    });

    await waitFor(() =>
      expect(useEnvironmentsStore.getState().draft).not.toBeNull(),
    );

    const draft = useEnvironmentsStore.getState().draft;
    expect(draft).not.toHaveProperty("apiKey");
    expect(draft).not.toHaveProperty("envText");
    expect(draft.agentId).toBe("agent-1");
    expect(enqueueSnackbar).toHaveBeenCalledWith(BUILD_HANDOFF_COPY, {
      variant: "info",
    });
  });

  it("hands react-query a redacted source, so no raw secrets linger as mutation variables", async () => {
    const { Wrapper, client } = makeWrapper();
    const { result } = renderHook(() => useBuildHandoff(), {
      wrapper: Wrapper,
    });

    act(() => {
      result.current({
        kind: "platform",
        apiKey: "sk-secret",
        envText: "TOKEN=leak",
        agentId: "agent-1",
      });
    });

    await waitFor(() =>
      expect(useEnvironmentsStore.getState().draft).not.toBeNull(),
    );

    const [mutation] = client.getMutationCache().getAll();
    expect(mutation.state.variables).not.toHaveProperty("apiKey");
    expect(mutation.state.variables).not.toHaveProperty("envText");
    expect(mutation.state.variables.agentId).toBe("agent-1");
  });
});
