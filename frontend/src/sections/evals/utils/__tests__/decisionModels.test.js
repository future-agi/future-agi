import { describe, expect, it } from "vitest";
import {
  chooseDecisionConnection,
  decisionCompatibilityError,
  decisionModelLabel,
  filterDecisionGroups,
  isDecisionModel,
  isJevDecisionModel,
  isNativeJevModel,
} from "../decisionModels";
import { buildJevMapping, getJevValidationErrors } from "../jevMapping";

const group = {
  model_name: "openai/gpt-6-luna-decisions",
  label: "GPT-6 Luna Decisions",
  author: "OpenAI",
  version: "versioned",
  question_types: ["noul", "choice", "score"],
  min_choices: 1,
  max_choices: 255,
  connections: [
    {
      model_name: "decision/openai/gpt-6-luna",
      provider: "openai",
      label: "OpenAI",
      is_available: true,
    },
    {
      model_name: "decision/openrouter/openai/gpt-6-luna-decisions",
      provider: "openrouter",
      label: "OpenRouter",
      is_available: false,
    },
  ],
};

describe("Decision model identity and connection selection", () => {
  it("blocks a withdrawn provider endpoint even when its key is configured", () => {
    const unavailable = {
      ...group,
      unavailable_reason:
        "No active OpenRouter endpoint. Choose another decision model.",
    };
    expect(decisionCompatibilityError(unavailable, "pass_fail", 0)).toBe(
      unavailable.unavailable_reason,
    );
    expect(decisionCompatibilityError(unavailable, undefined, undefined)).toBe(
      unavailable.unavailable_reason,
    );
  });
  it.each(["jev-1.13.0", "jev-1.14.0", "jev-latest"])(
    "preserves native Jev identity for %s",
    (model) => {
      expect(isNativeJevModel(model)).toBe(true);
      expect(isDecisionModel(model)).toBe(true);
    },
  );
  it("keeps the Jev entitlement on OpenRouter routes without gating other decision models", () => {
    expect(isJevDecisionModel("decision/openrouter/typesafe/jev-1.14")).toBe(
      true,
    );
    expect(isJevDecisionModel("decision/openrouter/~typesafe/jev-latest")).toBe(
      true,
    );
    expect(isJevDecisionModel(group.connections[1].model_name)).toBe(false);
  });
  it("keeps ordinary chat IDs separate from dedicated decision IDs", () => {
    expect(isDecisionModel("openai/gpt-6-luna")).toBe(false);
    expect(isDecisionModel("openrouter/typesafe/jev-1.14")).toBe(false);
    expect(isDecisionModel(group.connections[0].model_name)).toBe(true);
    expect(isNativeJevModel(group.connections[1].model_name)).toBe(false);
    // Unknown names still take the typed path, where server catalog validation refuses them.
    expect(isDecisionModel("decision/openrouter/unknown")).toBe(true);
  });
  it("keeps saved/explicit connections even without a key", () => {
    const [native, router] = group.connections;
    expect(chooseDecisionConnection(group, router.model_name)).toEqual(router);
    expect(
      chooseDecisionConnection(group, native.model_name, router.model_name),
    ).toEqual(router);
    expect(chooseDecisionConnection(group, "")).toEqual(native);
  });
  it("offers the connection a search names, without overriding a saved route", () => {
    const [native, router] = group.connections;
    // "openrouter" names a connection, so the row offers OpenRouter even
    // though only the native key is configured.
    expect(
      chooseDecisionConnection(group, "", undefined, "openrouter"),
    ).toEqual(router);
    expect(chooseDecisionConnection(group, "", undefined, " Router ")).toEqual(
      router,
    );
    // A search for the model or its author keeps the default choice.
    expect(chooseDecisionConnection(group, "", undefined, "OpenAI")).toEqual(
      native,
    );
    expect(chooseDecisionConnection(group, "", undefined, "luna")).toEqual(
      native,
    );
    // The current or saved route still wins over the search.
    expect(
      chooseDecisionConnection(
        group,
        native.model_name,
        undefined,
        "openrouter",
      ),
    ).toEqual(native);
    expect(
      chooseDecisionConnection(group, "", native.model_name, "openrouter"),
    ).toEqual(native);
  });
  it.each(["OpenRouter", "Luna", "OpenAI", "decision/openai"])(
    "searches model, author and route: %s",
    (search) => {
      expect(filterDecisionGroups([group], search)).toEqual([group]);
    },
  );
  it("uses a friendly model label without changing persisted identity", () => {
    expect(decisionModelLabel(group.connections[1].model_name, [group])).toBe(
      group.label,
    );
    expect(decisionModelLabel("jev-1.13.0", [])).toBe("Jev 1.13 (pinned)");
  });
});

describe("Decision model output compatibility", () => {
  it("blocks unsupported outputs and option counts", () => {
    expect(
      decisionCompatibilityError(
        { ...group, label: "Span-01", question_types: ["noul"] },
        "percentage",
      ),
    ).toBe(
      "Span-01 does not support Score. Choose another model or output type.",
    );
    expect(
      decisionCompatibilityError(
        { ...group, min_choices: 2, max_choices: 24 },
        "deterministic",
        25,
      ),
    ).toBe("GPT-6 Luna Decisions requires 2 to 24 choices.");
    expect(decisionCompatibilityError(group, "pass_fail")).toBe("");
  });
  it("preserves the existing typed mapping contract for new connections", () => {
    const mapping = buildJevMapping(
      "deterministic",
      { Yes: 1, No: 0 },
      { choice: { descriptions: { Yes: "Explicitly confirmed" } } },
    );
    expect(mapping).toEqual({
      revision: "jev-map-v1",
      question_type: "choice",
      pass: null,
      score: null,
      choice: {
        labels: ["Yes", "No"],
        descriptions: { Yes: "Explicitly confirmed", No: "" },
      },
      include_messages: false,
    });
    expect(
      getJevValidationErrors({
        mapping,
        config: {},
        choiceScores: { Yes: 1, No: 0 },
        modelLabel: group.label,
        modelGroup: group,
      }),
    ).toEqual([]);
  });
  it("names the selected model in validation and never silently drops unsupported settings", () => {
    const mapping = buildJevMapping("pass_fail", {});
    const config = { check_internet: true };
    expect(
      getJevValidationErrors({
        mapping,
        config,
        modelLabel: group.label,
        modelGroup: group,
      }),
    ).toEqual([
      "Internet check is not supported with GPT-6 Luna Decisions. Nothing was changed or removed from your template.",
    ]);
    expect(config).toEqual({ check_internet: true });
  });
});
