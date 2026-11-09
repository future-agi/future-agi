import { beforeEach, describe, expect, it, vi } from "vitest";

import modelTryIt from "../../models/detail/ModelTryIt.jsx?raw";
import guardrailTestTab from "../../guardrails/test/GuardrailTestTab.jsx?raw";
import { gatewayTestBody, sendGatewayTest } from "../gateway-test-request";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post } };
});

// The model page's Try it and the Guardrails Test tab send the same request
// to the gateway's test endpoint (Django creates a temporary key, sends one
// prompt and returns the gateway's status, headers and body). It is built and
// sent in one place; each page reads the answer its own way.
describe("the gateway test request", () => {
  beforeEach(() => post.mockReset());

  it("sends the trimmed prompt, and the model and system prompt only when given", () => {
    expect(
      gatewayTestBody({
        prompt: "  Hello ",
        model: " gpt-4o-mini ",
        systemPrompt: " Be brief. ",
      }),
    ).toEqual({
      prompt: "Hello",
      model: "gpt-4o-mini",
      system_prompt: "Be brief.",
    });
    expect(
      gatewayTestBody({ prompt: "Hello", model: "  ", systemPrompt: "" }),
    ).toEqual({ prompt: "Hello" });
  });

  it("posts once to this gateway's test endpoint and returns its result", async () => {
    const result = { status_code: 200, body: {} };
    post.mockResolvedValue({ data: { result } });
    await expect(sendGatewayTest("gw-1", { prompt: "Hi" })).resolves.toBe(
      result,
    );
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/test-playground/",
    );
    expect(post.mock.calls[0][1]).toEqual({ prompt: "Hi" });
    // A model's first answer can be slow; the app-wide default is shorter.
    expect(post.mock.calls[0][2]).toMatchObject({ timeout: 90000 });
  });

  it("reads an answer that is not wrapped in result", async () => {
    post.mockResolvedValue({ data: { status_code: 403 } });
    await expect(sendGatewayTest("gw-1", { prompt: "Hi" })).resolves.toEqual({
      status_code: 403,
    });
  });

  it.each([
    ["Try it on the model page", modelTryIt],
    ["the Guardrails Test tab", guardrailTestTab],
  ])("%s sends it through the shared request", (_name, source) => {
    expect(source).toMatch(/gateway-test-request/);
    expect(source).not.toMatch(/testPlayground\(/);
  });
});
