import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: useBuildHandoff, redactSource } = await import("../hooks/useBuildHandoff");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
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
