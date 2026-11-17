// A provider with a brand entry is named by it ("openai" reads "OpenAI"); a
// provider without one is named the way the server's provider list names it
// (LiteLlmModelProvider.get_choices: "cohere_chat" reads "Cohere Chat"), so a
// provider's key card and a model's missing-key hint read the same.
import { describe, expect, it } from "vitest";
import {
  PROVIDER_DISPLAY_NAMES,
  providerDisplayName,
} from "src/utils/providerDisplayName";
import { SERVER_PROVIDERS } from "./serverProviderList";

describe("providerDisplayName", () => {
  it.each(SERVER_PROVIDERS)(
    "names $provider by its brand entry, else as the server lists it",
    ({ provider, display_name: serverName }) => {
      const expected = Object.hasOwn(PROVIDER_DISPLAY_NAMES, provider)
        ? PROVIDER_DISPLAY_NAMES[provider]
        : serverName;
      expect(providerDisplayName(provider)).toBe(expected);
    },
  );

  it("spells typesafe as the server does", () => {
    expect(providerDisplayName("typesafe")).toBe("TypeSafe");
  });

  // get_choices: member name, "_" as a space, Python str.title().
  it.each([
    ["cohere_chat", "Cohere Chat"],
    ["text-completion-codestral", "Text Completion Codestral"],
    ["aleph_alpha", "Aleph Alpha"],
    ["ollama", "Ollama"],
    ["new_provider-v2", "New Provider V2"],
    ["model2go", "Model2Go"],
  ])("title-cases %s as %s", (id, name) => {
    expect(providerDisplayName(id)).toBe(name);
  });

  it("reads the first entry of a provider list", () => {
    expect(providerDisplayName(["cohere_chat", "openai"])).toBe("Cohere Chat");
    expect(providerDisplayName(["openai"])).toBe("OpenAI");
  });

  it("gives an empty name for no provider", () => {
    expect(providerDisplayName(undefined)).toBe("");
    expect(providerDisplayName(null)).toBe("");
    expect(providerDisplayName([])).toBe("");
  });
});
