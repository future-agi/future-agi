// R1-02 (one key): Run Prompt create, edit/run and preview used to refuse
// gpt-4o-mini unless the OpenAI provider-status entry had a key. The gate is
// now managed-or-BYOK execution eligibility for whatever model is picked, read
// from the server's model-list verdict (credential_mode, the pinned
// ONEKEY-ASSUMED-CONTRACT-r1). Unknown or still-loading eligibility never
// starts a run. Fixtures are contract-shaped server answers: no provider
// credential, credit amount or authorization result is faked here.
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));

import {
  ELIGIBILITY,
  checkExecutionEligibility,
  resolveExecutionEligibility,
  submittedModelOf,
  useExecutionEligibilityGate,
} from "../executionEligibility";

const entry = (over = {}) => ({
  model_name: "gpt-4o-mini",
  providers: "openai",
  is_available: true,
  type: "chat",
  ...over,
});
// An org with only a Future AGI key: no OpenAI key is saved anywhere; the
// model is available because it is managed-eligible.
const MANAGED = entry({
  credential_mode: "managed",
  payer: "future_agi_credits",
});
const BYOK = entry({ credential_mode: "byok", payer: "own_provider_key" });
const UNAVAILABLE = entry({
  credential_mode: "unavailable",
  payer: null,
  is_available: false,
});
// A server from before the one-key fields.
const LEGACY_WITH_KEY = entry();
const LEGACY_NO_KEY = entry({ is_available: false });

// Bodies as RunPrompt.jsx builds them before it posts.
const runConfig = {
  model: "gpt-4o-mini",
  run_prompt_config: { model_name: "gpt-4o-mini", providers: "openai" },
  messages: [{ role: "user", content: [{ type: "text", text: "Hi" }] }],
  concurrency: 5,
  response_format: "text",
};
const BODIES = {
  create: { dataset_id: "ds-1", name: "answer", config: runConfig },
  "edit/run": { dataset_id: "ds-1", name: "answer", config: runConfig },
  preview: {
    dataset_id: "ds-1",
    name: "answer",
    config: { ...runConfig, output_format: "string" },
    first_n_rows: 5,
  },
};
const PATHS = Object.keys(BODIES);

// A refusal names the same next steps as the model menus; a credits part
// only when the organization's Future AGI credits are on: Try it unless the
// gateway says delegated calls are off, then a gateway API key.
const REFUSED_CREDITS_OFF =
  "gpt-4o-mini isn't available in this workspace. Run this with your own provider key (Settings → AI Providers).";
const REFUSED_TRY_IT =
  "gpt-4o-mini isn't available in this workspace. Run this with your own provider key (Settings → AI Providers), or try models with your Future AGI credits: open a model in AI Gateway → Models and use Try it.";
const REFUSED_API_KEY =
  "gpt-4o-mini isn't available in this workspace. Run this with your own provider key (Settings → AI Providers), or call the gateway with an API key on your Future AGI credits (AI Gateway → API keys).";
const CREDITS_ON = { available: true, enabled: true };
const gateways = (fields = {}) => ({ result: [{ id: "gw-1", ...fields }] });

const gate = (fetchEntry, notify = vi.fn()) => {
  const { result } = renderHook(() =>
    useExecutionEligibilityGate({ fetchEntry, notify }),
  );
  return { run: result.current, notify };
};

describe("resolveExecutionEligibility", () => {
  it("a managed model is eligible without any provider key", () => {
    expect(
      resolveExecutionEligibility({ modelName: "gpt-4o-mini", entry: MANAGED }),
    ).toEqual({
      status: ELIGIBILITY.ELIGIBLE,
      mode: "managed",
      payer: "future_agi_credits",
      message: null,
    });
  });

  it("a BYOK model is eligible", () => {
    expect(
      resolveExecutionEligibility({ modelName: "gpt-4o-mini", entry: BYOK }),
    ).toMatchObject({ status: ELIGIBILITY.ELIGIBLE, mode: "byok" });
  });

  it("an unavailable model is refused with what to do next", () => {
    const verdict = resolveExecutionEligibility({
      modelName: "gpt-4o-mini",
      entry: UNAVAILABLE,
    });
    expect(verdict.status).toBe(ELIGIBILITY.INELIGIBLE);
    expect(verdict.message).toBe(REFUSED_CREDITS_OFF);
  });

  it.each([
    ["try_it", REFUSED_TRY_IT],
    ["api_key", REFUSED_API_KEY],
  ])("names the %s credits path when it is given", (creditsRoute, message) => {
    expect(
      resolveExecutionEligibility({
        modelName: "gpt-4o-mini",
        entry: UNAVAILABLE,
        creditsRoute,
      }).message,
    ).toBe(message);
  });

  it("is not tied to one model or provider", () => {
    const verdict = resolveExecutionEligibility({
      modelName: "claude-sonnet-4",
      entry: entry({
        model_name: "claude-sonnet-4",
        providers: "anthropic",
        credential_mode: "unavailable",
        is_available: false,
      }),
    });
    expect(verdict.status).toBe(ELIGIBILITY.INELIGIBLE);
    expect(verdict.message).toMatch(
      /^claude-sonnet-4 isn't available in this workspace\. /,
    );
    expect(
      resolveExecutionEligibility({
        modelName: "claude-sonnet-4",
        entry: entry({
          model_name: "claude-sonnet-4",
          providers: "anthropic",
          credential_mode: "managed",
          payer: "future_agi_credits",
        }),
      }).status,
    ).toBe(ELIGIBILITY.ELIGIBLE);
  });

  it("a server without the one-key fields keeps the provider-key rule", () => {
    expect(
      resolveExecutionEligibility({
        modelName: "gpt-4o-mini",
        entry: LEGACY_WITH_KEY,
      }),
    ).toMatchObject({ status: ELIGIBILITY.ELIGIBLE, mode: "byok" });
    expect(
      resolveExecutionEligibility({
        modelName: "gpt-4o-mini",
        entry: LEGACY_NO_KEY,
      }),
    ).toMatchObject({
      status: ELIGIBILITY.INELIGIBLE,
      message: "Please configure OpenAI API key",
    });
  });

  it.each([
    ["the lookup failed", { entry: undefined, lookupFailed: true }],
    ["the server lists no such model", { entry: null }],
    ["credential_mode is not a contract value", { entry: entry({ credential_mode: "trial" }) }],
    ["the item carries no verdict at all", { entry: { model_name: "gpt-4o-mini" } }],
  ])("is unknown, never eligible, when %s", (_why, input) => {
    const verdict = resolveExecutionEligibility({
      modelName: "gpt-4o-mini",
      ...input,
    });
    expect(verdict.status).toBe(ELIGIBILITY.UNKNOWN);
    expect(verdict.message).toMatch(/^Couldn't confirm access to gpt-4o-mini/);
  });

  it("asks for a model when none is set", () => {
    expect(resolveExecutionEligibility({ modelName: "  " })).toMatchObject({
      status: ELIGIBILITY.UNKNOWN,
      message: "Select a model before running this prompt.",
    });
  });
});

describe("checkExecutionEligibility", () => {
  it("looks the picked model up and resolves its verdict", async () => {
    const fetchEntry = vi.fn().mockResolvedValue(MANAGED);
    await expect(
      checkExecutionEligibility({ modelName: " gpt-4o-mini ", fetchEntry }),
    ).resolves.toMatchObject({ status: ELIGIBILITY.ELIGIBLE, mode: "managed" });
    expect(fetchEntry).toHaveBeenCalledWith("gpt-4o-mini");
  });

  it("a failed lookup is unknown and never shows the server's error", async () => {
    const fetchEntry = vi
      .fn()
      .mockRejectedValue(new Error("upstream said: invalid_api_key"));
    const verdict = await checkExecutionEligibility({
      modelName: "gpt-4o-mini",
      fetchEntry,
    });
    expect(verdict.status).toBe(ELIGIBILITY.UNKNOWN);
    expect(verdict.message).not.toMatch(/upstream|invalid_api_key/);
  });

  it("does not look anything up without a model", async () => {
    const fetchEntry = vi.fn();
    await expect(
      checkExecutionEligibility({ modelName: "", fetchEntry }),
    ).resolves.toMatchObject({ status: ELIGIBILITY.UNKNOWN });
    expect(fetchEntry).not.toHaveBeenCalled();
  });
});

describe("submittedModelOf", () => {
  it.each(PATHS)("%s: reads the model and provider Run Prompt sends", (path) => {
    expect(submittedModelOf(BODIES[path])).toEqual({
      modelName: "gpt-4o-mini",
      provider: "openai",
    });
  });
});

describe("Run Prompt gate with only Future AGI managed access", () => {
  it.each(PATHS)("%s runs without an OpenAI key", async (path) => {
    const { run, notify } = gate(vi.fn().mockResolvedValue(MANAGED));
    await expect(run(BODIES[path])).resolves.toBe(true);
    expect(notify).not.toHaveBeenCalled();
  });

  it.each(PATHS)("%s is refused, with a reason, when unavailable", async (path) => {
    const { run, notify } = gate(vi.fn().mockResolvedValue(UNAVAILABLE));
    await expect(run(BODIES[path])).resolves.toBe(false);
    expect(notify).toHaveBeenCalledWith(REFUSED_CREDITS_OFF, {
      variant: "error",
    });
  });
});

describe("Run Prompt refusal names Future AGI credits only when they are on", () => {
  const gateWithCredits = (
    fetchCreditsStatus,
    fetchGatewayList = vi.fn().mockResolvedValue(gateways()),
  ) => {
    const notify = vi.fn();
    const { result } = renderHook(() =>
      useExecutionEligibilityGate({
        fetchEntry: vi.fn().mockResolvedValue(UNAVAILABLE),
        fetchCreditsStatus,
        fetchGatewayList,
        notify,
      }),
    );
    return { run: result.current, notify };
  };

  it.each([
    ["the gateway does not send the field yet", gateways(), REFUSED_TRY_IT],
    [
      "the gateway serves delegated calls",
      gateways({ delegated_playground_available: true }),
      REFUSED_TRY_IT,
    ],
    [
      "the gateway says delegated calls are off",
      gateways({ delegated_playground_available: false }),
      REFUSED_API_KEY,
    ],
  ])("credits on and %s", async (_why, list, message) => {
    const fetchCreditsStatus = vi.fn().mockResolvedValue(CREDITS_ON);
    const { run, notify } = gateWithCredits(
      fetchCreditsStatus,
      vi.fn().mockResolvedValue(list),
    );
    await expect(run(BODIES.create)).resolves.toBe(false);
    expect(fetchCreditsStatus).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith(message, { variant: "error" });
  });

  it.each([
    [
      "credits are off",
      () => Promise.resolve({ available: true, enabled: false }),
    ],
    ["the status cannot be read", () => Promise.reject(new Error("503"))],
  ])("%s: the provider-key sentence alone", async (_why, status) => {
    const { run, notify } = gateWithCredits(vi.fn(status));
    await expect(run(BODIES.preview)).resolves.toBe(false);
    expect(notify).toHaveBeenCalledWith(REFUSED_CREDITS_OFF, {
      variant: "error",
    });
  });

  it("an eligible model never reads the credits status", async () => {
    const fetchCreditsStatus = vi.fn();
    const notify = vi.fn();
    const { result } = renderHook(() =>
      useExecutionEligibilityGate({
        fetchEntry: vi.fn().mockResolvedValue(MANAGED),
        fetchCreditsStatus,
        notify,
      }),
    );
    await expect(result.current(BODIES.create)).resolves.toBe(true);
    expect(fetchCreditsStatus).not.toHaveBeenCalled();
  });
});

describe("BYOK regression: own provider keys behave as before", () => {
  it.each(
    PATHS.flatMap((path) => [
      [path, "credential_mode byok", BYOK],
      [path, "a server without the one-key fields", LEGACY_WITH_KEY],
    ]),
  )("%s with %s runs and leaves the body untouched", async (path, _how, answer) => {
    const body = BODIES[path];
    const before = JSON.stringify(body);
    const { run, notify } = gate(vi.fn().mockResolvedValue(answer));

    await expect(run(body)).resolves.toBe(true);
    expect(JSON.stringify(body)).toBe(before);
    expect(notify).not.toHaveBeenCalled();
  });

  it("a missing OpenAI key still shows the same message as before", async () => {
    const { run, notify } = gate(vi.fn().mockResolvedValue(LEGACY_NO_KEY));
    await expect(run(BODIES["edit/run"])).resolves.toBe(false);
    expect(notify).toHaveBeenCalledWith("Please configure OpenAI API key", {
      variant: "error",
    });
  });
});

describe("loading or unknown eligibility fails safe", () => {
  it("a failed lookup refuses and asks to retry", async () => {
    const { run, notify } = gate(vi.fn().mockRejectedValue(new Error("x")));
    await expect(run(BODIES.preview)).resolves.toBe(false);
    expect(notify).toHaveBeenCalledWith(
      expect.stringMatching(/^Couldn't confirm access to gpt-4o-mini/),
      { variant: "warning" },
    );
  });

  it("a second submit while the check is loading does not start a run", async () => {
    let answer;
    const fetchEntry = vi.fn(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const { run, notify } = gate(fetchEntry);

    const first = run(BODIES.create);
    await expect(run(BODIES.create)).resolves.toBe(false);
    expect(fetchEntry).toHaveBeenCalledTimes(1);

    answer(MANAGED);
    await expect(first).resolves.toBe(true);
    expect(notify).toHaveBeenCalledWith("Checking model access. Please wait.", { variant: "info" });

    // Settled: the next submit is checked again.
    fetchEntry.mockResolvedValueOnce(UNAVAILABLE);
    await expect(run(BODIES.create)).resolves.toBe(false);
    expect(fetchEntry).toHaveBeenCalledTimes(2);
  });
});
