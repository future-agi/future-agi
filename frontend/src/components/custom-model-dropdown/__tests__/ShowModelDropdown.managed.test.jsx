// OK-B (R1-01, R1-13, R2-13): the shared model menu decides "select" versus
// "send to provider-key setup" per row. RED on base 651ab5bb because the gate
// reads only `isAvailable` (ShowModelDropdown.jsx:135,577), so a managed
// Future AGI model is sent to provider-key setup, no payer is shown, an
// "unavailable" contract row can still be picked, and contract fields leak
// onto the DOM through the restOption spread. The BYOK / pre-contract cases
// are regression guards and pass on base and after the change.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

// Keep the real eligibility adapter without importing the application's HTTP
// client (and its unrelated generated-contract initialization).
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));

// Provider-logo loading is not part of eligibility. jsdom does not supply
// the full IntersectionObserver/IntersectionObserverEntry browser API.
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
import {
  BYOK_PAYER_LABEL,
  MANAGED_PAYER_LABEL,
  byokRow,
  configureHint,
  legacyRow,
  managedRow,
  unavailableRow,
} from "./modelListContractFixtures";

function renderMenu({ rows, ...props }) {
  const anchor = document.createElement("div");
  document.body.appendChild(anchor);
  const handlers = {
    onChange: vi.fn(),
    onConfigOpen: vi.fn(),
    onClose: vi.fn(),
  };
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
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

const clickRow = (modelName) => fireEvent.click(screen.getByText(modelName));

describe("ShowModelDropdown: managed-eligible models", () => {
  it("selects a managed model without opening provider-key setup, even if is_available is still false", () => {
    const h = renderMenu({ rows: [managedRow()] });

    clickRow("gpt-5.5-mini");

    expect(h.onConfigOpen).not.toHaveBeenCalled();
    expect(h.onChange).toHaveBeenCalledTimes(1);
    const { target } = h.onChange.mock.calls[0][0];
    expect(target.value).toBe("gpt-5.5-mini");
    expect(target.option.model_name).toBe("gpt-5.5-mini");
    expect(h.onClose).toHaveBeenCalled();
  });

  it("shows that Future AGI credits pay, and no provider-key hint", () => {
    renderMenu({ rows: [managedRow()] });

    expect(screen.getByText(MANAGED_PAYER_LABEL)).toBeInTheDocument();
    expect(screen.queryByText(/an api key for/i)).toBeNull();
  });

  it("adds a managed model to a multi-select variant list (experiment variants) with the unchanged entry shape", () => {
    const h = renderMenu({
      rows: [managedRow(), byokRow()],
      multiple: true,
      showButtons: true,
      value: [],
    });

    clickRow("gpt-5.5-mini");
    expect(h.onConfigOpen).not.toHaveBeenCalled();
    expect(h.onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Add"));
    expect(h.onChange).toHaveBeenCalledTimes(1);
    const picked = h.onChange.mock.calls[0][0].target.value;
    expect(picked).toHaveLength(1);
    expect(Object.keys(picked[0]).sort()).toEqual([
      "id",
      "logoUrl",
      "providers",
      "value",
    ]);
    expect(picked[0]).toMatchObject({
      value: "gpt-5.5-mini",
      providers: "openai",
    });
  });

  it("does not forward the contract fields onto the DOM", () => {
    renderMenu({ rows: [managedRow(), byokRow()] });

    expect(document.querySelector("[credential_mode]")).toBeNull();
    expect(document.querySelector("[payer]")).toBeNull();
  });
});

describe("ShowModelDropdown: unavailable contract rows fail closed", () => {
  it("routes an unavailable row to provider-key setup even if the legacy flag says available", () => {
    const h = renderMenu({ rows: [unavailableRow({ is_available: true })] });

    expect(screen.getByText(configureHint("Mistral"))).toBeInTheDocument();
    clickRow("mistral-large-3");

    expect(h.onConfigOpen).toHaveBeenCalledTimes(1);
    expect(h.onConfigOpen.mock.calls[0][0].model_name).toBe("mistral-large-3");
    expect(h.onChange).not.toHaveBeenCalled();
  });
});

describe("ShowModelDropdown: BYOK and pre-contract rows are unchanged", () => {
  it("opens provider-key setup for an unavailable pre-contract row and shows the same hint", () => {
    const h = renderMenu({ rows: [legacyRow({ is_available: false })] });

    expect(screen.getByText(configureHint("OpenAI"))).toBeInTheDocument();
    clickRow("gpt-4o");

    expect(h.onConfigOpen).toHaveBeenCalledTimes(1);
    expect(h.onConfigOpen.mock.calls[0][0].model_name).toBe("gpt-4o");
    expect(h.onChange).not.toHaveBeenCalled();
    expect(screen.queryByText(MANAGED_PAYER_LABEL)).toBeNull();
    expect(screen.queryByText(BYOK_PAYER_LABEL)).toBeNull();
  });

  it("selects an available pre-contract row exactly as before", () => {
    const h = renderMenu({ rows: [legacyRow({ is_available: true })] });

    expect(screen.queryByText(/an api key for/i)).toBeNull();
    clickRow("gpt-4o");

    expect(h.onConfigOpen).not.toHaveBeenCalled();
    expect(h.onChange).toHaveBeenCalledTimes(1);
    expect(h.onChange.mock.calls[0][0].target.value).toBe("gpt-4o");
    expect(h.onClose).toHaveBeenCalled();
  });

  it("keeps the provider-key gate for byok rows and names the user's key as payer", () => {
    const h = renderMenu({
      rows: [
        byokRow(),
        byokRow({ model_name: "claude-opus-5", is_available: false }),
      ],
    });

    expect(screen.getAllByText(BYOK_PAYER_LABEL)).toHaveLength(1);
    expect(screen.getByText(configureHint("Anthropic"))).toBeInTheDocument();

    clickRow("claude-opus-5");
    expect(h.onConfigOpen).toHaveBeenCalledTimes(1);
    expect(h.onChange).not.toHaveBeenCalled();

    clickRow("claude-sonnet-5");
    expect(h.onChange).toHaveBeenCalledTimes(1);
    expect(h.onChange.mock.calls[0][0].target.value).toBe("claude-sonnet-5");
  });

  it("leaves requireUserApiKey={false} callers untouched: no hint, no payer, plain select", () => {
    const h = renderMenu({
      rows: [legacyRow({ is_available: false }), managedRow()],
      requireUserApiKey: false,
    });

    expect(screen.queryByText(/an api key for/i)).toBeNull();
    expect(screen.queryByText(MANAGED_PAYER_LABEL)).toBeNull();
    clickRow("gpt-4o");

    expect(h.onConfigOpen).not.toHaveBeenCalled();
    expect(h.onChange.mock.calls[0][0].target.value).toBe("gpt-4o");
  });
});

// A model without a key read "(Configure an api key for openai ai)", "(... for
// azure ai)", "(... for openrouter ai)": a doubled "ai" and a lower-case
// provider. The hint names the provider the way the provider cards do.
describe("ShowModelDropdown: the missing-key hint names the provider", () => {
  it.each([
    ["openai", "OpenAI", "gpt-4o"],
    ["azure", "Azure OpenAI", "azure-gpt-4o"],
    ["openrouter", "OpenRouter", "openrouter-llama"],
  ])("a %s model reads (Add an API key for %s)", (provider, name, model) => {
    renderMenu({
      rows: [legacyRow({ model_name: model, providers: provider })],
    });

    const hint = `(Add an API key for ${name})`;
    expect(screen.getByText(hint)).toBeInTheDocument();
    expect(screen.queryByText(/ ai\)$/)).toBeNull();
    expect(screen.queryByText(/Configure an api key/)).toBeNull();
  });
});
