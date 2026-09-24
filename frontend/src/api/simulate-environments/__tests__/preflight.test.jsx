import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/harness/harness", () => ({ preflightHarnessJob: vi.fn() }));

const { preflightHarnessJob } = await import("src/api/harness/harness");
const { usePreflight } = await import("../preflight");
const { draftToPreflightPayload } = await import("../preflightPayload");

const repoDraft = { kind: "repo", value: "acme/support-bot", ref: "main" };
const uploadDraft = { kind: "upload", entry: "src/agent.py", files: [] };

// The happy backend payload — mirrors the T6 read-audit fixtures.
const happyResponse = () => ({
  ready_to_submit: true,
  credentials: {
    scanned_files: 42,
    detected_connectors: ["vapi"],
    requirements: [],
    credential_choices: [],
    probe: [{ provider: "vapi", ok: true }],
  },
  packaging: { notes: [], candidates: [], selected_path: "src/agent.py" },
});

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
  preflightHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue(happyResponse());
});

describe("usePreflight", () => {
  it("calls preflightHarnessJob once with the T5 payload and derives the audit", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => usePreflight(repoDraft), {
      wrapper: Wrapper,
    });

    expect(result.current.isFetching).toBe(true);
    expect(result.current.audit).toBeNull();

    await waitFor(() => expect(result.current.isFetching).toBe(false));

    expect(preflightHarnessJob).toHaveBeenCalledTimes(1);
    expect(preflightHarnessJob).toHaveBeenCalledWith(
      draftToPreflightPayload(repoDraft).payload
    );
    // A clean backend response is a clean audit — no invented gaps or facts.
    expect(result.current.audit.status).toBe("healthy");
    expect(result.current.audit.sectionIssues).toEqual({});
    expect(result.current.audit.reading.tools).toEqual([]);
    expect(result.current.audit.questions).toEqual([]);
    expect(result.current.audit.stats.scannedFiles).toBe(42);
  });

  it("a failing backend check → warning audit carrying that check", async () => {
    preflightHarnessJob.mockResolvedValue({
      ...happyResponse(),
      checks: [
        {
          id: "credentials_present",
          label: "Credentials present",
          status: "failed",
          detail: "No value supplied",
          missing: ["VAPI_API_KEY"],
          fix: "Add the key",
        },
      ],
    });
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => usePreflight(repoDraft), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.isFetching).toBe(false));

    expect(result.current.audit.status).toBe("warning");
    expect(result.current.audit.checks).toHaveLength(1);
    expect(result.current.audit.checks[0].fix).toBe("Add the key");
  });

  it("a skipped draft resolves synchronously — no call", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => usePreflight(uploadDraft), {
      wrapper: Wrapper,
    });

    expect(preflightHarnessJob).not.toHaveBeenCalled();
    expect(result.current.isFetching).toBe(false);
    expect(result.current.audit).not.toBeNull();
    expect(result.current.audit.sectionIssues.tools.message).toBe(
      "Preflight skipped"
    );
    // No payload → no refetch handed out, so "Retry read" can't POST an empty body.
    expect(result.current.refetch).toBeUndefined();
  });

  it("a rejected preflight → hardfail audit, no retry", async () => {
    preflightHarnessJob.mockRejectedValue({ response: { data: { detail: "boom" } } });
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => usePreflight(repoDraft), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.audit).not.toBeNull());

    expect(result.current.audit.status).toBe("hardfail");
    expect(result.current.audit.hardfailReason).toBe("boom");
    expect(preflightHarnessJob).toHaveBeenCalledTimes(1);
  });

  it("draft=null → no call, audit null", () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => usePreflight(null), {
      wrapper: Wrapper,
    });

    expect(preflightHarnessJob).not.toHaveBeenCalled();
    expect(result.current.audit).toBeNull();
  });
});
