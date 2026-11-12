import { describe, expect, it } from "vitest";

import { ACCESS } from "../catalogRows";
import { callTarget, modelRoutes, readStreamResult, readTryResult } from "./modelFacts";

const own = { id: "openai/gpt-4o", access: ACCESS.OWN_KEY, ownRoutes: [{ sendAs: "gpt-4o" }], managedOn: false };

describe("call targets with unresolved provider access", () => {
  it.each(["pending", "error"])("never authorizes a cached own route while provider access is %s", (providerAccessState) => {
    expect(callTarget({ ...own, providerAccessState })).toBeNull();
    expect(callTarget({ ...own, access: ACCESS.UNKNOWN, providerAccessState })).toBeNull();
    expect(callTarget({ ...own, access: ACCESS.BOTH, providerAccessState, managedOn: true }))
      .toEqual({ model: own.id, managed: true });
    // A known managed route remains representable while off; its caller gates use.
    expect(callTarget({ ...own, access: ACCESS.MANAGED, providerAccessState }))
      .toEqual({ model: own.id, managed: true });
  });

  it.each([undefined, "ready"])("preserves ordinary legacy and ready own routes (%s)", (providerAccessState) => {
    expect(callTarget({ ...own, providerAccessState })).toEqual({ model: "gpt-4o", managed: false });
    expect(callTarget({ ...own, access: ACCESS.BOTH, providerAccessState })).toEqual({ model: "gpt-4o", managed: false });
  });
});

describe("explicitly unpushed call targets", () => {
  const stopped = { providerId: "one", providerName: "One", sendAs: "one/chat", pushed: false, statusReason: "not pushed: credential unavailable" };
  it("rejects an unpushed route even if a legacy row still marks own access", () => {
    expect(callTarget({ ...own, ownRoutes: [stopped] })).toBeNull();
    expect(modelRoutes({ ...own, ownRoutes: [stopped] })[0]).toMatchObject({
      on: false, statusReason: stopped.statusReason,
    });
  });
  it("selects a later usable route and preserves independent managed eligibility", () => {
    expect(callTarget({ ...own, ownRoutes: [stopped, { sendAs: "two/chat", pushed: true }] }))
      .toEqual({ model: "two/chat", managed: false });
    expect(callTarget({ ...own, access: ACCESS.BOTH, ownRoutes: [stopped] }))
      .toEqual({ model: own.id, managed: true });
  });
});

describe("test result diagnostics preserve existing fields", () => {
  it("adds only received diagnostics to JSON and SSE refusal results", () => {
    const wire = { status_code: 429, body: { error: { code: "upstream_4xx", message: "Gateway request failed." } }, guardrail_headers: {} };
    const result = readTryResult(wire);
    expect(result).toMatchObject({ ok: false, status: 429, error: "Gateway request failed.", answer: "", requestId: "" });
    expect(result.diagnostics).toEqual({ outcome: "refused", status: 429, message: "Gateway request failed.", code: "upstream_4xx" });
    expect(readStreamResult([{ event: "result", data: wire }])).toEqual({ ...result, streaming: false });
  });

  it.each([true, false])("retains partial output and unknown completion when a stream ends without done (error event: %s)", (withError) => {
    const events = [
      { event: "meta", data: { status_code: 200 } },
      { event: "delta", data: { content: "Keep these words" } },
      ...(withError ? [{ event: "error", data: { message: "Stream interrupted" } }] : []),
    ];
    expect(readStreamResult(events, { finished: true })).toMatchObject({
      ok: false, streaming: false, answer: "Keep these words", diagnostics: { outcome: "unknown", status: 200 },
    });
  });
});
