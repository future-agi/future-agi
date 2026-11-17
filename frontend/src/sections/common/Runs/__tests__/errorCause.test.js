// W1-7: the one error classifier (errorCause) and its grouping.
import { describe, expect, it } from "vitest";
import {
  backoffLine,
  causesText,
  errorCauseOf,
  errorsByCause,
  isRateLimitCause,
  retryAfterSecondsOf,
} from "../errorCause";

const err = (error, extra = {}) => ({ status: "error", error, ...extra });

describe("errorCauseOf", () => {
  it("reads the cause from the error text, never from an id", () => {
    expect(
      errorCauseOf(
        err(
          "RateLimitError: Rate limit reached for gpt-4o-mini in organization org-acme on tokens per min (TPM). Please try again in 2.1s.",
        ),
      ),
    ).toBe("RateLimitError");
    expect(errorCauseOf(err("Stopped · cost cap $0.80 reached"))).toBe(
      "stopped at cost cap",
    );
    expect(errorCauseOf(err("Stopped at max turns (12)"))).toBe("max turns");
    expect(errorCauseOf(err("Failed at step 4 · lookup_customer 404"))).toBe(
      "tool error · lookup_customer",
    );
    expect(errorCauseOf(err("lookup_invoice timeout after 30s"))).toBe(
      "tool timeout · lookup_invoice",
    );
    expect(errorCauseOf(err("Timed out at step 19 · 600 s"))).toBe("timeout");
    expect(errorCauseOf(err("HTTP 502 from api.acme.ai (1 retry)"))).toBe(
      "HTTP 502",
    );
    expect(errorCauseOf(err("Something odd"))).toBe("other error");
    expect(errorCauseOf({ status: "completed", error: null })).toBeNull();
  });

  it("names a failed tool call the try recorded", () => {
    expect(
      errorCauseOf(
        err("The agent stopped", {
          tool_calls: [
            { name: "lookup_order" },
            { name: "issue_refund", error: { message: "timed out after 30s" } },
          ],
        }),
      ),
    ).toBe("tool timeout · issue_refund");
    expect(
      errorCauseOf(
        err("The agent stopped", {
          tool_calls: [{ name: "cancel_item", status: "error", error: "409" }],
        }),
      ),
    ).toBe("tool error · cancel_item");
  });
});

describe("retryAfterSecondsOf and rate limits", () => {
  it("reads the wait the error asks for", () => {
    expect(retryAfterSecondsOf("Please try again in 1.4s.")).toBe(1.4);
    expect(retryAfterSecondsOf("please try again in 850ms")).toBeCloseTo(0.85);
    expect(retryAfterSecondsOf("retry after 2 seconds")).toBe(2);
    expect(retryAfterSecondsOf("Rate limit reached")).toBeNull();
    expect(isRateLimitCause("RateLimitError")).toBe(true);
    expect(isRateLimitCause("HTTP 429")).toBe(true);
    expect(isRateLimitCause("HTTP 502")).toBe(false);
  });
});

describe("errorsByCause", () => {
  it("counts rows whose every try errored, by the first errored try's cause", () => {
    const recs = [
      err("RateLimitError: … Please try again in 1.2s.", {
        example_id: "r1",
        trial: 1,
      }),
      err("RateLimitError: … Please try again in 2.1s.", {
        example_id: "r1",
        trial: 2,
      }),
      err("RateLimitError: … Please try again in 0.5s.", {
        example_id: "r2",
        trial: 1,
      }),
      // r3 errored once and passed once: not an errored row.
      err("HTTP 502 from api", { example_id: "r3", trial: 1 }),
      { status: "completed", example_id: "r3", trial: 2 },
      err("lookup_invoice timeout after 30s", { example_id: "r4", trial: 1 }),
    ];
    const by = errorsByCause(recs);
    expect(by).toEqual([
      { cause: "RateLimitError", rows: 2, retry_after_s: 1.2 },
      { cause: "tool timeout · lookup_invoice", rows: 1 },
    ]);
    expect(causesText(by)).toBe(
      "RateLimitError 2 · tool timeout · lookup_invoice 1",
    );
  });

  it("words the backoff line of a Rerun failed rows dialog", () => {
    expect(
      backoffLine([
        {
          errors_by_cause: [
            { cause: "RateLimitError", rows: 2, retry_after_s: 2.1 },
          ],
        },
        { errors_by_cause: [{ cause: "HTTP 502", rows: 1 }] },
      ]),
    ).toBe(
      "2 rate-limited rows run again after the other rows, once the 2.1 s wait their errors name is over.",
    );
    expect(
      backoffLine([
        { errors_by_cause: [{ cause: "RateLimitError", rows: 1 }] },
      ]),
    ).toBe(
      "1 rate-limited row runs again after the other rows (the error names no wait).",
    );
    expect(backoffLine([{ errors_by_cause: [] }])).toBe("");
  });
});
