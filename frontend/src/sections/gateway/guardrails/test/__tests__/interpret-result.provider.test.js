import { describe, expect, it } from "vitest";

import { interpretTestResult } from "../interpret-result";

// A managed call's
// provider reads "Future AGI" on customer screens, from the billing
// mode the gateway answers with, never from the provider's name. The Test
// tab's result card shows the "Provider" fact read here.
const answer = (headers) => ({
  status_code: 200,
  body: JSON.stringify({ choices: [{ message: { content: "ok" } }] }),
  guardrail_headers: headers,
});

describe("Guardrails Test result: the provider it names", () => {
  it("names a managed call's provider Future AGI", () => {
    const result = interpretTestResult(
      answer({
        "x-agentcc-provider": "openrouter",
        "x-agentcc-billing-mode": "managed",
      }),
    );
    expect(result.provider).toBe("Future AGI");
  });

  it("reads the headers in any case, as the gateway's answer spells them", () => {
    const result = interpretTestResult(
      answer({
        "X-Agentcc-Provider": "openrouter",
        "X-Agentcc-Billing-Mode": "Managed",
      }),
    );
    expect(result.provider).toBe("Future AGI");
  });

  it("keeps the name of the org's own OpenRouter key (billing mode byok)", () => {
    const result = interpretTestResult(
      answer({
        "x-agentcc-provider": "openrouter",
        "x-agentcc-billing-mode": "byok",
      }),
    );
    expect(result.provider).toBe("openrouter");
  });

  it("keeps the provider when the answer names no billing mode", () => {
    const result = interpretTestResult(
      answer({ "x-agentcc-provider": "anthropic" }),
    );
    expect(result.provider).toBe("anthropic");
  });

  it("names no provider when the answer has none", () => {
    expect(interpretTestResult(answer({})).provider).toBeNull();
  });
});
