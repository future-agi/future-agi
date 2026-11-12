// OK-B (R1-03, R1-27, R2-13, R2-24, R2-27): the full picker as Classification,
// Extract Entities, both dataset optimizers and the simulation optimizer mount
// it (with or without onModelConfigOpen). RED on base 651ab5bb: a managed
// model whose legacy is_available is still false is handed to the caller's
// provider-key dialog / KeysDrawer instead of being selected. The payload and
// request-shape cases are BYOK regression guards (pass on base and after).
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";

const state = vi.hoisted(() => ({ get: vi.fn() }));

// Provider-logo loading is not part of eligibility. jsdom does not supply
// the full IntersectionObserver/IntersectionObserverEntry browser API.
vi.mock("react-lazy-load-image-component", () => ({
  // eslint-disable-next-line react/prop-types
  LazyLoadImage: ({ alt, src, className, style }) => (
    <img alt={alt} src={src} className={className} style={style} />
  ),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args) },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));
// The provider-key drawer is a stub that only reports whether it was opened
// and for which model; no key storage or provider status is involved.
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: ({ open, selectedModel }) =>
    open ? (
      <div data-testid="provider-setup-drawer">
        {selectedModel?.model_name ?? ""}
      </div>
    ) : null,
}));
vi.mock("src/pages/dashboard/settings/AddCustomModal", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-dropdown/EachCustomVoiceModel", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/RunPrompt/common", () => ({
  MODEL_TYPES: { LLM: "llm", STT: "stt", TTS: "tts", IMAGE: "image" },
}));

import CustomModelDropdown from "../CustomModelDropdown";
import { legacyRow, managedRow } from "./modelListContractFixtures";

const MODEL_LIST_URL = "/model-hub/api/models_list/";

function serveModelList(rows) {
  state.get.mockImplementation((url) =>
    Promise.resolve(
      url === MODEL_LIST_URL
        ? { data: { results: rows, next: null, current_page: 1 } }
        : { data: { results: [] } },
    ),
  );
}

function mountPicker(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <CustomModelDropdown
        openSelectModel
        setOpenSelectModel={vi.fn()}
        hideCreateLabel
        customTrigger={<span>Pick a model</span>}
        onChange={onChange}
        value=""
        modelDetail={{}}
        extraParams={{ model_type: "llm" }}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onChange };
}

describe("CustomModelDropdown: managed-or-BYOK eligibility", () => {
  beforeEach(() => {
    state.get.mockReset();
  });

  // The picked value keeps the server's verdict (credential_mode and payer)
  // next to the fields it always carried.
  it("selects a managed model and emits the single-select payload with its verdict", async () => {
    serveModelList([managedRow()]);
    const { onChange } = mountPicker();

    fireEvent.click(await screen.findByText("gpt-5.5-mini"));

    expect(screen.queryByTestId("provider-setup-drawer")).toBeNull();
    expect(onChange).toHaveBeenCalledTimes(1);
    const { value } = onChange.mock.calls[0][0].target;
    expect(Object.keys(value).sort()).toEqual([
      "credential_mode",
      "id",
      "isAvailable",
      "logoUrl",
      "model_name",
      "payer",
      "providers",
      "type",
    ]);
    expect(value).toMatchObject({
      model_name: "gpt-5.5-mini",
      providers: "openai",
      isAvailable: true,
      type: "llm",
      credential_mode: "managed",
      payer: "future_agi_credits",
    });
  });

  it("does not open the built-in provider-key drawer for a managed row the legacy flag still marks unavailable", async () => {
    serveModelList([managedRow()]);
    const { onChange } = mountPicker();

    fireEvent.click(await screen.findByText("gpt-5.5-mini"));

    expect(screen.queryByTestId("provider-setup-drawer")).toBeNull();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].target.value.model_name).toBe(
      "gpt-5.5-mini",
    );
  });

  it("never hands a managed model to the caller's provider-key dialog (Classification / optimizer pattern)", async () => {
    serveModelList([managedRow()]);
    const onModelConfigOpen = vi.fn();
    const { onChange } = mountPicker({ onModelConfigOpen });

    fireEvent.click(await screen.findByText("gpt-5.5-mini"));

    expect(onModelConfigOpen).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("still hands an unavailable BYOK row to the caller's provider-key dialog", async () => {
    serveModelList([legacyRow({ is_available: false })]);
    const onModelConfigOpen = vi.fn();
    const { onChange } = mountPicker({ onModelConfigOpen });

    fireEvent.click(await screen.findByText("gpt-4o"));

    expect(onModelConfigOpen).toHaveBeenCalledTimes(1);
    expect(onModelConfigOpen.mock.calls[0][0]).toMatchObject({
      model_name: "gpt-4o",
      providers: "openai",
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("still opens the built-in provider-key drawer for an unavailable BYOK row", async () => {
    serveModelList([legacyRow({ is_available: false })]);
    const { onChange } = mountPicker();

    fireEvent.click(await screen.findByText("gpt-4o"));

    expect(
      await screen.findByTestId("provider-setup-drawer"),
    ).toHaveTextContent("gpt-4o");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("requests the model list exactly as before (the client sends no funding parameters)", async () => {
    serveModelList([managedRow()]);
    mountPicker();

    await screen.findByText("gpt-5.5-mini");

    expect(state.get).toHaveBeenCalledWith(MODEL_LIST_URL, {
      params: { page: 1, search: "", model_type: "llm" },
    });
  });
});
