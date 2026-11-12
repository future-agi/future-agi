import { describe, it, expect } from "vitest";
import { nodePipelineSentence } from "./guardrailPipeline";

describe("gateway organization-check copy", () => {
  it("states the exact fail_open=true behavior", () => {
    expect(
      nodePipelineSentence({
        enabled: true,
        fail_open: true,
        mode: "sequential",
      }),
    ).toBe(
      "Set by this gateway, read-only: enabled organization checks run one at a time and stop at the first block. For checks that do not rewrite the request, a timeout or recovered panic skips that check and continues with the remaining checks. Remote-service errors returned as failed check results follow the check's threshold and action. A request-rewriting check that times out or panics blocks the request. The gateway sets this behavior for every organization.",
    );
  });
  it("states the exact fail_open=false behavior", () => {
    expect(
      nodePipelineSentence({
        enabled: true,
        fail_open: false,
        mode: "sequential",
      }),
    ).toBe(
      "Set by this gateway, read-only: enabled organization checks run one at a time and stop at the first block. For checks that do not rewrite the request, a timeout or recovered panic produces a failed result evaluated against the check's threshold and action. Remote-service errors returned as failed check results follow the check's threshold and action. A request-rewriting check that times out or panics blocks the request. The gateway sets this behavior for every organization.",
    );
  });
});
