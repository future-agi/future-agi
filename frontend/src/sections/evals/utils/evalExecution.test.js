import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { executeEvalForRow } from "./evalExecution";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { develop: { eval: { evalPlayground: "/model-hub/eval-playground/" } } },
}));

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { status: true, result: { output: 1, reason: "progress" } } });
});

describe("stored function parameters in task previews", () => {
  it("forwards saved binding parameters without explicit editor overrides", async () => {
    const result = await executeEvalForRow({
      evalItem: { template_id: "loop", config: { params: { min_repeats: 4, window_size: 8 } } },
      rowType: "traces", currentRow: { trace_id: "trace-1" }, spanDetail: {}, mapping: {},
    });
    expect(result.ok).toBe(true);
    expect(axios.post).toHaveBeenCalledWith("/model-hub/eval-playground/", expect.objectContaining({
      trace_id: "trace-1", config: { mapping: {}, params: { min_repeats: 4, window_size: 8 } },
    }));
  });

  it("keeps explicit editor parameters authoritative over saved values", async () => {
    await executeEvalForRow({
      evalItem: { template_id: "loop", config: { params: { window_size: 100 } } },
      rowType: "traces", currentRow: { trace_id: "trace-1" }, spanDetail: {}, mapping: {},
      codeParams: { window_size: 8 },
    });
    expect(axios.post).toHaveBeenCalledWith("/model-hub/eval-playground/", expect.objectContaining({
      config: { mapping: {}, params: { window_size: 8 } },
    }));
  });
});
