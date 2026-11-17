import { describe, expect, it } from "vitest";
import {
  defaultPromptName,
  firstPromptBody,
  firstPromptIssues,
  insertToken,
  savedPromptOf,
  starterMessage,
  unknownVariables,
  unknownVariablesLine,
  variableToken,
} from "../firstPromptModel";

// JRN-EXP-NO-PROMPT-DEADEND: a brand-new user has no saved prompt, so the
// wizard lets them write one. These are the pure parts: the starting values,
// what blocks saving, and the create-draft body (a saved v1, not a draft).
const model = {
  model_name: "gpt-4o-mini",
  providers: "openai",
  logoUrl: "https://logos.example/openai.svg",
  isAvailable: true,
  type: "chat",
};
const columns = [
  { id: "c1", name: "question" },
  { id: "c2", name: "customer tier" },
];

describe("first prompt model (JRN-EXP-NO-PROMPT-DEADEND)", () => {
  it("FED-M1: names the prompt after the dataset and starts from its input column", () => {
    expect(defaultPromptName("support-faq")).toBe("support-faq prompt");
    expect(defaultPromptName("  ")).toBe("First prompt");
    expect(defaultPromptName(null)).toBe("First prompt");
    expect(starterMessage("question")).toBe("{{question}}");
    expect(starterMessage(null)).toBe("");
    expect(variableToken("customer tier")).toBe("{{customer tier}}");
  });

  it("FED-M2: inserts a column at the caret, with a space before it when needed", () => {
    expect(insertToken("Answer:", "{{question}}", 7, 7)).toEqual({
      text: "Answer: {{question}}",
      caret: 20,
    });
    expect(insertToken("Hi  there", "{{q}}", 3, 3)).toEqual({
      text: "Hi {{q}} there",
      caret: 8,
    });
    expect(insertToken("", "{{q}}")).toEqual({ text: "{{q}}", caret: 5 });
    // No caret: at the end.
    expect(insertToken("Answer", "{{q}}")).toEqual({
      text: "Answer {{q}}",
      caret: 12,
    });
    // A selection is replaced.
    expect(insertToken("Say NAME now", "{{q}}", 4, 8)).toEqual({
      text: "Say {{q}} now",
      caret: 9,
    });
  });

  it("FED-M3: says what blocks saving, and names variables that are not columns", () => {
    expect(firstPromptIssues({ name: "", model: null, message: " " })).toEqual([
      "Give the prompt a name",
      "Pick a model",
      "Write the message to send",
    ]);
    expect(
      firstPromptIssues({ name: "p", model, message: "{{question}}" }),
    ).toEqual([]);
    expect(
      unknownVariables(
        ["Use {{question}}", "Tier {{tier}}, {{ question }} and {{tier}}"],
        columns,
      ),
    ).toEqual(["tier"]);
    expect(unknownVariables(["{{customer tier}}"], columns)).toEqual([]);
    expect(unknownVariablesLine(["tier"])).toBe(
      "{{tier}} isn't a column in this dataset. Pick one from the list below.",
    );
    expect(unknownVariablesLine(["a", "b"])).toBe(
      "{{a}}, {{b}} aren't columns in this dataset. Pick one from the list below.",
    );
    expect(unknownVariablesLine([])).toBe("");
  });

  it("FED-M4: builds a saved prompt (is_draft false) with the picked model", () => {
    const body = firstPromptBody({
      name: " Support prompt ",
      model,
      instructions: " Be brief. ",
      message: "Answer {{question}} for a {{customer tier}} customer.\n",
    });
    expect(body).toEqual({
      name: "Support prompt",
      is_draft: false,
      prompt_config: [
        {
          messages: [
            { role: "system", content: [{ type: "text", text: "Be brief." }] },
            {
              role: "user",
              content: [
                {
                  type: "text",
                  text: "Answer {{question}} for a {{customer tier}} customer.",
                },
              ],
            },
          ],
          configuration: {
            model: "gpt-4o-mini",
            model_detail: {
              model_name: "gpt-4o-mini",
              providers: "openai",
              logo_url: "https://logos.example/openai.svg",
              is_available: true,
              type: "chat",
            },
          },
        },
      ],
    });
    // No instructions: the user message only.
    expect(
      firstPromptBody({
        name: "p",
        model,
        instructions: "",
        message: "{{question}}",
      }).prompt_config[0].messages,
    ).toEqual([
      { role: "user", content: [{ type: "text", text: "{{question}}" }] },
    ]);
    // A model with no type stays without one: the Prompt picker lists a
    // prompt with no type as chat, and one with "" in no list at all.
    const { type: _type, ...untyped } = model;
    expect(
      firstPromptBody({ name: "p", model: untyped, message: "{{question}}" })
        .prompt_config[0].configuration.model_detail,
    ).not.toHaveProperty("type");
  });

  it("FED-M5: reads the new prompt from the create answer for the run card", () => {
    expect(
      savedPromptOf(
        { id: "p-1", name: "Support prompt", root_template: "p-1" },
        model,
        "Typed name",
      ),
    ).toEqual({
      id: "p-1",
      name: "Support prompt",
      model: "gpt-4o-mini",
      model_detail: {
        logo_url: "https://logos.example/openai.svg",
        providers: "openai",
      },
    });
    expect(
      savedPromptOf({ root_template: "p-2" }, model, "Typed name"),
    ).toMatchObject({
      id: "p-2",
      name: "Typed name",
    });
    expect(savedPromptOf({}, model, "x")).toBeNull();
    expect(savedPromptOf(undefined, model, "x")).toBeNull();
  });
});
