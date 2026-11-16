import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  AUTOMATION_RULE_LIST_TIMEOUT_MS,
  readAutomationRulePage,
} from "./automation-rule-list-read";

const pageResponse = (overrides = {}) => ({
  data: {
    results: [{ id: "rule-1", name: "Rule One" }],
    count: 26,
    current_page: 1,
    total_pages: 2,
    ...overrides,
  },
});

describe("readAutomationRulePage", () => {
  it("returns a validated bounded page and forwards cancellation options", async () => {
    const requestPage = vi.fn().mockResolvedValue(pageResponse());

    await expect(readAutomationRulePage(requestPage)).resolves.toEqual({
      results: [{ id: "rule-1", name: "Rule One" }],
      count: 26,
      currentPage: 1,
      totalPages: 2,
    });
    expect(requestPage).toHaveBeenCalledWith({
      signal: expect.any(AbortSignal),
      timeout: AUTOMATION_RULE_LIST_TIMEOUT_MS,
    });
  });

  it.each([
    {},
    { results: [], count: 0, current_page: 1, total_pages: 0 },
    { results: [{}], count: 1, current_page: 1, total_pages: 1 },
    {
      results: [{ id: "rule-1" }],
      count: 1,
      current_page: 2,
      total_pages: 1,
    },
  ])(
    "rejects malformed pagination instead of publishing an empty list",
    async (data) => {
      await expect(
        readAutomationRulePage(() => Promise.resolve({ data })),
      ).rejects.toMatchObject({ code: "automation_rule_list_invalid_page" });
    },
  );

  it("aborts a stalled page at the configured interactive wall", async () => {
    vi.useFakeTimers();
    let signal;
    const pending = readAutomationRulePage((options) => {
      signal = options.signal;
      return new Promise(() => {});
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(AUTOMATION_RULE_LIST_TIMEOUT_MS);

    await rejection;
    expect(signal.aborted).toBe(true);
    expect(AUTOMATION_RULE_LIST_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
    vi.useRealTimers();
  });
});
