// A model without a key names its provider exactly as the provider's key card
// does, for every provider the server lists, including one with no brand
// entry: "cohere_chat" reads "(Add an API key for Cohere Chat)", as its card
// reads "Cohere Chat", never the raw id.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { SERVER_PROVIDERS } from "src/utils/__tests__/serverProviderList";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));
vi.mock("react-lazy-load-image-component", () => ({
  // eslint-disable-next-line react/prop-types
  LazyLoadImage: ({ alt, src, className, style }) => (
    <img alt={alt} src={src} className={className} style={style} />
  ),
}));
vi.mock("src/components/custom-model-dropdown/EachCustomVoiceModel", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/RunPrompt/common", () => ({
  MODEL_TYPES: { LLM: "llm", STT: "stt", TTS: "tts", IMAGE: "image" },
}));

import ShowModelDropdown from "../ShowModelDropdown";
import { normalizeModelOption } from "../common";
import { normalizeProviderStatus } from "../KeysHelper";
import { configureHint, legacyRow } from "./modelListContractFixtures";

function renderMenu(rows) {
  const anchor = document.createElement("div");
  document.body.appendChild(anchor);
  render(
    <ShowModelDropdown
      ref={{ current: anchor }}
      open
      searchDropdown
      hideCreateLabel
      options={rows.map(normalizeModelOption)}
      value=""
      isLoadingModelList={false}
      isFetchingNextPage={false}
      fetchNextPage={vi.fn()}
      setIsFocus={vi.fn()}
      setSearchQuery={vi.fn()}
      inputRef={{ current: null }}
      onChange={vi.fn()}
      onConfigOpen={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

const modelOf = (provider) => `model-of-${provider}`;

describe("ShowModelDropdown: a provider without a brand entry", () => {
  it.each([
    ["cohere_chat", "Cohere Chat"],
    ["ollama", "Ollama"],
    ["text-completion-codestral", "Text Completion Codestral"],
    ["typesafe", "TypeSafe"],
  ])("a %s model without a key reads (Add an API key for %s)", (id, name) => {
    renderMenu([legacyRow({ model_name: modelOf(id), providers: id })]);

    expect(screen.getByText(configureHint(name))).toBeInTheDocument();
    expect(screen.queryByText(configureHint(id))).toBeNull();
  });
});

describe("ShowModelDropdown: every provider the server lists", () => {
  it("names each provider in a model's missing-key hint as its key card does", () => {
    renderMenu(
      SERVER_PROVIDERS.map(({ provider }) =>
        legacyRow({ model_name: modelOf(provider), providers: provider }),
      ),
    );

    const rows = SERVER_PROVIDERS.map((serverRow) => {
      const card = normalizeProviderStatus(serverRow).display_name;
      const model = screen.getByText(modelOf(serverRow.provider));
      const item = model.closest("li");
      const hint = within(item).getByText(/^\(Add an API key for .*\)$/);
      return { provider: serverRow.provider, card, hint: hint.textContent };
    });
    const mismatches = rows.filter(
      ({ card, hint }) => hint !== configureHint(card),
    );

    expect(rows).toHaveLength(SERVER_PROVIDERS.length);
    expect(mismatches).toEqual([]);
  });
});
