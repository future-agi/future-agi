import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const enqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

vi.mock("src/api/harness/harness", () => ({ storeHarnessSecretValues: vi.fn() }));

const { storeHarnessSecretValues } = await import("src/api/harness/harness");
const { default: useBuildHandoff, redactSource } = await import("../hooks/useBuildHandoff");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
);
const { getPendingCredentialValues } = await import(
  "src/api/simulate-environments/credentialValues"
);

const makeWrapper = () => {
  const Wrapper = ({ children }) => <MemoryRouter>{children}</MemoryRouter>;
  Wrapper.propTypes = { children: PropTypes.node };
  return { Wrapper };
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
    navigate.mockReset();
    enqueueSnackbar.mockReset();
    storeHarnessSecretValues.mockReset();
    storeHarnessSecretValues.mockResolvedValue({ secret_refs: {} });
  });

  const handoff = async (source) => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useBuildHandoff(), { wrapper: Wrapper });
    await act(async () => {
      await result.current(source);
    });
  };

  it("exchanges a hosted API key for an opaque secret ref the preflight can read", async () => {
    // The endpoint returns SecretReference objects, not bare strings.
    const ref = { manager: "platform-vault", key: "vapi-1", purpose: "target_provider" };
    storeHarnessSecretValues.mockResolvedValue({ secret_refs: { VAPI_API_KEY: ref } });

    await handoff({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_1",
      apiKey: "sk-secret",
    });

    expect(storeHarnessSecretValues).toHaveBeenCalledWith({ VAPI_API_KEY: "sk-secret" });
    const draft = useEnvironmentsStore.getState().draft;
    expect(draft.secret_refs).toEqual({ VAPI_API_KEY: ref });
    expect(draft).not.toHaveProperty("apiKey");
    expect(navigate).toHaveBeenCalled();
  });

  it("uses the Retell alias for a Retell draft", async () => {
    await handoff({ kind: "platform", provider: "retell", agentId: "a", apiKey: "sk-r" });
    expect(storeHarnessSecretValues).toHaveBeenCalledWith({ RETELL_API_KEY: "sk-r" });
  });

  it("exchanges pasted .env values one alias at a time", async () => {
    await handoff({ kind: "repo", value: "acme/bot", envText: "A=1\nB=2" });
    expect(storeHarnessSecretValues).toHaveBeenCalledWith({ A: "1", B: "2" });
  });

  it("keeps the plaintext values out of the draft but hands them to the probe", async () => {
    await handoff({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_1",
      apiKey: "sk-secret",
    });

    // The live credentials_valid / provider_target probes need the raw value…
    expect(getPendingCredentialValues()).toEqual({ VAPI_API_KEY: "sk-secret" });
    // …but it must never be persisted in the store.
    expect(JSON.stringify(useEnvironmentsStore.getState().draft)).not.toContain("sk-secret");
  });

  it("does not call the exchange when there is nothing to exchange", async () => {
    await handoff({ kind: "repo", value: "acme/bot" });
    expect(storeHarnessSecretValues).not.toHaveBeenCalled();
    expect(useEnvironmentsStore.getState().draft.secret_refs).toBeUndefined();
    expect(navigate).toHaveBeenCalled();
  });

  it("surfaces a failed exchange and stays on the panel", async () => {
    storeHarnessSecretValues.mockRejectedValue(new Error("vault down"));

    await handoff({ kind: "platform", provider: "vapi", agentId: "a", apiKey: "sk-x" });

    expect(navigate).not.toHaveBeenCalled();
    expect(useEnvironmentsStore.getState().draft).toBeNull();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.stringContaining("vault down"),
      { variant: "error" },
    );
  });

  it("stores a redacted draft (no raw secrets) and navigates to the build page", async () => {
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
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/build",
    );
  });
});
