import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  parseEvents,
  sendGatewayTest,
  streamGatewayTest,
} from "../gateway-test-request";
import {
  readTryResult,
  readStreamResult,
} from "../../models/detail/modelFacts";

import { CONTRACT_CASES } from "../../utils/__tests__/managedRefusalContractCases";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post },
}));

const PAIRS = [
  ...CONTRACT_CASES.map(({ status, code, category }) => [
    status,
    code,
    category,
  ]),
  [500, "unknown", "other"],
];
const resultFor = (status, code) => ({
  status_code: status,
  body: {
    error: {
      code,
      message:
        code === "permission_denied"
          ? "model pricing is not available for Future AGI credits"
          : "Server detail",
      metadata:
        code === "managed_credits_insufficient"
          ? { limit_source: "agentcc_in_flight", remedy_hint: "retry" }
          : {},
    },
  },
  guardrail_headers: { "Retry-After": "2" },
});

beforeEach(() => {
  post.mockReset();
});

describe("request transport carries managed refusal state without replay", () => {
  it.each(PAIRS)(
    "maps one JSON request for %i / %s",
    async (status, code, category) => {
      const original = resultFor(status, code);
      post.mockResolvedValue({ data: { result: original } });
      const result = await sendGatewayTest("gw-1", { prompt: "Hi" });
      expect(result.refusal.category).toBe(category);
      expect(readTryResult(result).refusal).toEqual(result.refusal);
      expect(result.body).toBe(original.body);
      expect(original).not.toHaveProperty("refusal");
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it.each(PAIRS)(
    "maps one streamed request for %i / %s",
    async (status, code, category) => {
      const data = `event: result\ndata: ${JSON.stringify(resultFor(status, code))}\n\n`;
      const onEvents = vi.fn();
      post.mockImplementation(async (_url, _body, options) => {
        options.onDownloadProgress({
          event: { target: { responseText: data } },
        });
        return { data };
      });
      const events = await streamGatewayTest(
        "gw-1",
        { prompt: "Hi" },
        { onEvents },
      );
      expect(events[0].data.refusal.category).toBe(category);
      expect(onEvents.mock.calls[0][0][0].data.refusal.category).toBe(category);
      expect(
        readStreamResult(events, { finished: true }).refusal.category,
      ).toBe(category);
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it.each(PAIRS)(
    "does not replay an outer HTTP refusal for %i / %s",
    async (status, code) => {
      const failure = { statusCode: status, ...resultFor(status, code).body };
      post.mockRejectedValue(failure);
      await expect(sendGatewayTest("gw-1", { prompt: "Hi" })).rejects.toBe(
        failure,
      );
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it.each(PAIRS)(
    "does not replay a streamed HTTP refusal for %i / %s",
    async (status, code) => {
      const failure = { statusCode: status, ...resultFor(status, code).body };
      post.mockRejectedValue(failure);
      await expect(
        streamGatewayTest("gw-1", { prompt: "Hi" }, {}),
      ).rejects.toBe(failure);
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it("preserves successes and non-result SSE events", async () => {
    const result = { status_code: 200, body: { choices: [] } };
    post.mockResolvedValue({ data: { result } });
    await expect(sendGatewayTest("gw-1", {})).resolves.toBe(result);
    expect(parseEvents('event: delta\ndata: {"content":"Hi"}\n\n')).toEqual([
      { event: "delta", data: { content: "Hi" } },
    ]);
  });
});
