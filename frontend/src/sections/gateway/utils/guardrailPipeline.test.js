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
      "Gateway default for the checks in this gateway's own configuration: they run in turn and stop at the first block. For checks that do not rewrite the request, a timeout or recovered panic skips that check and continues with the remaining checks. Remote-service errors returned as failed check results follow the check's threshold and action. Your organization's Mode and Fail Open settings apply to its checks. A check that rewrites the request and does not finish always refuses the request.",
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
      "Gateway default for the checks in this gateway's own configuration: they run in turn and stop at the first block. For checks that do not rewrite the request, a timeout or recovered panic produces a failed result evaluated against the check's threshold and action. Remote-service errors returned as failed check results follow the check's threshold and action. Your organization's Mode and Fail Open settings apply to its checks. A check that rewrites the request and does not finish always refuses the request.",
    );
  });
  it("says the checks run together when the gateway's default is parallel", () => {
    expect(
      nodePipelineSentence({
        enabled: true,
        fail_open: false,
        mode: "parallel",
      }),
    ).toMatch(
      /^Gateway default for the checks in this gateway's own configuration: they run together\. For checks/,
    );
  });
  it("says nothing about order when the gateway reports no mode", () => {
    const sentence = nodePipelineSentence({ enabled: true, fail_open: false });
    expect(sentence).toMatch(
      /^Gateway default for the checks in this gateway's own configuration: For checks/,
    );
    expect(sentence).not.toMatch(/in turn|together/);
    expect(sentence).toContain(
      "Your organization's Mode and Fail Open settings apply to its checks.",
    );
  });
});
