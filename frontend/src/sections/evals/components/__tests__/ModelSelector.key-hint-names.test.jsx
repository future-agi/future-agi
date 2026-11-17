/* eslint-disable react/prop-types */
// The evaluator's model picker kept its own provider names, so a model
// without a key read "Add your Openrouter key" or "Add your Vertex_ai key".
// It names the provider as the provider's key card does; Google models and
// custom models read as before.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ModelSelector from "../ModelSelector";

const mockGet = vi.fn();

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    develop: {
      modelList: "/model-hub/api/models_list/",
      apiKey: { status: "/model-hub/develops/provider-status/" },
      eval: {
        summaryTemplates: "/eval/summary-templates/",
        summaryTemplate: (id) => `/eval/summary-templates/${id}/`,
      },
    },
    falconAI: { connectors: "/falcon-ai/connectors/" },
    knowledge: { list: "/knowledge/list/" },
  },
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));

// Your Models rows the workspace cannot call: no provider key is saved.
const MODELS = [
  { model_name: "or-llama", providers: "openrouter", is_available: false },
  { model_name: "vx-gemini", providers: "vertex_ai", is_available: false },
  { model_name: "cc-command", providers: "cohere_chat", is_available: false },
  { model_name: "gpt-5.5", providers: "openai", is_available: false },
  { model_name: "g-flash", providers: "google", is_available: false },
  { model_name: "local-model", providers: "custom", is_available: false },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
  mockGet.mockImplementation(async (url) => {
    if (url === "/api/capabilities/") {
      return {
        data: {
          features: {
            turing_models: { allowed: true },
            jev_models: { allowed: true },
            falcon_ai: { allowed: true },
          },
        },
      };
    }
    if (url === "/model-hub/develops/provider-status/") {
      return {
        data: {
          result: { providers: [{ provider: "typesafe", has_key: true }] },
        },
      };
    }
    if (url === "/model-hub/api/models_list/") {
      return { data: { results: MODELS, next: null, current_page: 1 } };
    }
    return { data: { results: [] } };
  });
});
afterEach(() => vi.unstubAllGlobals());

function renderSelector() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelSelector model="" onModelChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("ModelSelector: a Your Models row without a key names its provider", () => {
  it.each([
    ["or-llama", "Add your OpenRouter key"],
    ["vx-gemini", "Add your Google Vertex AI key"],
    ["cc-command", "Add your Cohere Chat key"],
    ["gpt-5.5", "Add your OpenAI key"],
    ["g-flash", "Add your Google Gemini key"],
    ["local-model", "Add the custom model's base URL"],
  ])("%s reads %s", async (model, hint) => {
    renderSelector();
    await userEvent.click(screen.getByText("Select model"));

    const row = (await screen.findByText(model)).closest("li");
    expect(row).toHaveTextContent(hint);
  });
});
