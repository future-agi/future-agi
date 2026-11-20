import { describe, it, expect, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/harness/harness", () => ({ getHarnessJob: vi.fn() }));
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  getHarnessEnvironment: vi.fn(async () => null),
}));

const { getHarnessJob } = await import("src/api/harness/harness");
const { useEnvironment } = await import("../environment");

const job = (stage) => ({
  job: { job_id: "job-x", metadata: { name: "Extended" }, scenario_count: 5 },
  status: { stage, updated_at: "2026-11-20T09:00:00Z" },
  credentials: { detected_connectors: [] },
  stage_outputs: [],
});

describe("useEnvironment after an extend", () => {
  it("reads the unsaved changes again once the extend finishes, as it does after a chat turn", async () => {
    getHarnessJob.mockResolvedValue(job("running"));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper = ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.propTypes = { children: PropTypes.node };
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const draftsCalls = () =>
      spy.mock.calls.filter(
        ([arg]) =>
          JSON.stringify(arg?.queryKey) ===
          JSON.stringify(["harness-env-versions", "job-x", "drafts"]),
      ).length;
    renderHook(() => useEnvironment("job-x"), { wrapper: Wrapper });
    await waitFor(() => expect(getHarnessJob).toHaveBeenCalled());
    expect(draftsCalls()).toBe(0);

    getHarnessJob.mockResolvedValue(job("completed"));
    await queryClient.refetchQueries({ queryKey: ["harness-job", "job-x"] });

    await waitFor(() => expect(draftsCalls()).toBe(1));
  });
});
