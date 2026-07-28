import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "src/utils/test-utils";

import EvalModelParams from "./EvalModelParams";

const { capturedProps } = vi.hoisted(() => ({ capturedProps: { cmo: null } }));

vi.mock("src/components/custom-model-options/CustomModelOptions", () => ({
  default: (props) => {
    capturedProps.cmo = props;
    return <div data-testid="custom-model-options" />;
  },
}));

const { axiosGet } = vi.hoisted(() => ({ axiosGet: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: axiosGet },
  };
});

// Same shape get_llm_parameters returns (sliders keyed by snake_case label);
// transformModelParams turns labels into camelCase slider ids.
const LLM_PARAM_DEFS = {
  sliders: [
    { label: "temperature", min: 0, max: 2, step: 0.1, default: null },
    { label: "max_tokens", min: 1, max: 65536, step: 1, default: null },
    { label: "top_p", min: 0, max: 1, step: 0.1, default: null },
  ],
  responseFormat: [{ value: "json" }, { value: "text" }],
};

const renderParams = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EvalModelParams
        model="turing_large"
        value={null}
        onChange={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );
};

describe("EvalModelParams", () => {
  beforeEach(() => {
    capturedProps.cmo = null;
    axiosGet.mockReset();
    axiosGet.mockResolvedValue({ data: { result: LLM_PARAM_DEFS } });
  });

  it("fetches llm definitions for the model and passes transformed sliders", async () => {
    renderParams();

    await waitFor(() => {
      expect(capturedProps.cmo?.modelParams?.sliders?.length).toBe(3);
    });

    expect(axiosGet).toHaveBeenCalledWith(
      expect.stringContaining("model_parameters"),
      expect.objectContaining({
        params: expect.objectContaining({
          model: "turing_large",
          model_type: "llm",
        }),
      }),
    );
    const ids = capturedProps.cmo.modelParams.sliders.map((s) => s.id);
    expect(ids).toEqual(["temperature", "maxTokens", "topP"]);
    // Judge output parsing owns the response contract — never expose it here.
    expect(capturedProps.cmo.modelParams.responseFormat).toBeUndefined();
  });

  it("applies edited values to onChange as snake_case without nullish keys", async () => {
    const onChange = vi.fn();
    renderParams({ onChange });

    await waitFor(() => {
      expect(capturedProps.cmo?.modelParams?.sliders?.length).toBe(3);
    });

    capturedProps.cmo.setValue("config", {
      temperature: 0,
      maxTokens: 256,
      topP: null,
    });
    capturedProps.cmo.handleApply();

    // temperature 0 preserved; topP null dropped; keys snake_cased.
    expect(onChange).toHaveBeenCalledWith({ temperature: 0, max_tokens: 256 });
  });

  it("emits null when applying with no values set", async () => {
    const onChange = vi.fn();
    renderParams({ onChange });

    await waitFor(() => {
      expect(capturedProps.cmo?.modelParams?.sliders?.length).toBe(3);
    });

    capturedProps.cmo.setValue("config", {
      temperature: null,
      maxTokens: undefined,
    });
    capturedProps.cmo.handleApply();

    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("seeds saved snake_case values back into the form, including zero", async () => {
    renderParams({ value: { temperature: 0, max_tokens: 512 } });

    await waitFor(() => {
      expect(capturedProps.cmo?.modelConfig?.temperature).toBe(0);
    });
    expect(capturedProps.cmo.modelConfig.maxTokens).toBe(512);
  });

  it("disables the control when the definitions fetch fails", async () => {
    axiosGet.mockRejectedValue(new Error("boom"));
    const onChange = vi.fn();
    renderParams({ onChange });

    await waitFor(() => {
      expect(capturedProps.cmo?.disabledClick).toBe(true);
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});
