import { describe, expect, it } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import {
  detailPollInterval,
  healthPollInterval,
  listPollInterval,
  rateLimitDelay,
  retryRead,
} from "../api/queries";

const query = ({ error = null, data = [], fetchFailureCount = 0 } = {}) => ({
  state: { error, data, fetchFailureCount },
});

describe("deployment polling", () => {
  it("backs off exponentially while a limit keeps holding", () => {
    const error = { status: 429 };
    expect(rateLimitDelay(error, 1)).toBe(5_000);
    expect(rateLimitDelay(error, 2)).toBe(10_000);
    expect(rateLimitDelay(error, 5)).toBe(60_000);
  });

  it("honours Retry-After without polling more than once a minute", () => {
    expect(rateLimitDelay({ status: 429, retryAfter: 12 }, 1)).toBe(12_000);
    expect(
      rateLimitDelay({ code: "omega_rate_limited", retryAfter: 0 }, 1),
    ).toBe(5_000);
    expect(rateLimitDelay({ status: 429, retryAfter: 1 }, 1, 10_000)).toBe(
      10_000,
    );
    expect(rateLimitDelay({ status: 429, retryAfter: 120 }, 1)).toBe(60_000);
  });

  it("keeps normal and busy intervals unchanged when there is no 429", () => {
    expect(listPollInterval(query())).toBe(15_000);
    expect(
      listPollInterval(
        query({ data: [{ status: "building" }], fetchFailureCount: 0 }),
      ),
    ).toBe(3_000);
    expect(detailPollInterval(query())).toBe(10_000);
    expect(detailPollInterval(query({ data: { status: "deploying" } }))).toBe(
      1_500,
    );
    expect(healthPollInterval(query())).toBe(30_000);
  });

  it("never polls faster than a surface's normal interval after a 429", () => {
    const limited = query({
      error: { status: 429 },
      data: [],
      fetchFailureCount: 2,
    });
    expect(listPollInterval(limited)).toBe(15_000);
    expect(detailPollInterval(limited)).toBe(10_000);
    expect(healthPollInterval(limited)).toBe(30_000);
  });

  it("treats missing, null, zero and negative Retry-After as absent", () => {
    for (const retryAfter of [undefined, null, 0, -1, ""]) {
      expect(rateLimitDelay({ status: 429, retryAfter }, 1, 30_000)).toBe(
        30_000,
      );
    }
  });
});

describe("deployment read retries", () => {
  const throttled = new OmegaError({
    state: "unavailable",
    status: null,
    code: "session_exchange_throttled",
    message: "Your account opened Omega too many times in the last minute.",
  });
  const unavailable = new OmegaError({
    state: "unavailable",
    status: 503,
    code: "omega_remote_unreachable",
  });

  it("retries a transient outage twice", () => {
    expect(retryRead(0, unavailable)).toBe(true);
    expect(retryRead(1, unavailable)).toBe(true);
    expect(retryRead(2, unavailable)).toBe(false);
  });

  it("never retries a rate limit, so the page can say so at once", () => {
    expect(retryRead(0, throttled)).toBe(false);
    expect(
      retryRead(0, { state: "error", status: 429, code: "omega_rate_limited" }),
    ).toBe(false);
  });

  it("never retries an ended session or a refusal", () => {
    expect(retryRead(0, { state: "refused", status: 401 })).toBe(false);
    expect(retryRead(0, { state: "refused", status: 403 })).toBe(false);
  });
});
