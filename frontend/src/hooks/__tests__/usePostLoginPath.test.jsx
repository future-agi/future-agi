import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// TH-8084 review C9 (TH-8005): the first landing on a self-hosted install is
// an included product, never a gated surface.

const h = vi.hoisted(() => ({ mode: "oss", isSuccess: true }));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useQuery: () => ({
      data: h.mode,
      isLoading: !h.isSuccess,
      isSuccess: h.isSuccess,
    }),
  };
});
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { settings: { v2: { deploymentInfo: "/api/deployment-info/" } } },
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITIES_QUERY_KEY: ["capabilities"],
}));
vi.mock("src/routes/paths", () => ({
  paths: {
    dashboard: {
      getstarted: "/dashboard/get-started",
      falconAI: "/dashboard/falcon-ai",
    },
  },
}));

const { usePostLoginPath } = await import("../useDeploymentMode");

function renderPath({ falconAllowed } = {}) {
  const queryClient = new QueryClient();
  if (falconAllowed !== undefined) {
    queryClient.setQueryData(["capabilities"], {
      data: { features: { falcon_ai: { allowed: falconAllowed } } },
    });
  }
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return renderHook(() => usePostLoginPath(), { wrapper: Wrapper });
}

beforeEach(() => {
  localStorage.clear();
  h.mode = "oss";
  h.isSuccess = true;
});

describe("usePostLoginPath (AC-01)", () => {
  it("lands a fresh self-hosted install on Get started", () => {
    expect(renderPath().result.current).toBe("/dashboard/get-started");
  });

  it("lands an EE-keyed install without a usable licence on Get started", () => {
    h.mode = "ee";
    expect(renderPath({ falconAllowed: false }).result.current).toBe(
      "/dashboard/get-started",
    );
  });

  it("ignores a stored redirect to Falcon AI without a usable licence", () => {
    h.mode = "ee";
    localStorage.setItem("redirectUrl", "/dashboard/falcon-ai/chat");
    expect(renderPath({ falconAllowed: false }).result.current).toBe(
      "/dashboard/get-started",
    );
  });

  it("keeps a stored redirect to an included product", () => {
    localStorage.setItem("redirectUrl", "/dashboard/observe");
    expect(renderPath().result.current).toBe("/dashboard/observe");
  });

  it("honours a Falcon AI redirect when the licence allows it", () => {
    h.mode = "ee";
    localStorage.setItem("redirectUrl", "/dashboard/falcon-ai");
    expect(renderPath({ falconAllowed: true }).result.current).toBe(
      "/dashboard/falcon-ai",
    );
  });

  it("reads the stored redirect once per mount", () => {
    localStorage.setItem("redirectUrl", "/dashboard/observe");
    const { result, rerender } = renderPath();
    localStorage.removeItem("redirectUrl");
    rerender();
    expect(result.current).toBe("/dashboard/observe");
  });

  it("is unchanged on Cloud (AC-13)", () => {
    h.mode = "cloud";
    expect(renderPath().result.current).toBe("/dashboard/falcon-ai");
    localStorage.setItem("redirectUrl", "/dashboard/falcon-ai/chat");
    expect(renderPath().result.current).toBe("/dashboard/falcon-ai/chat");
  });
});
