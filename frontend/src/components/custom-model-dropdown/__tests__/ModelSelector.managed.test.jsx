// OK-B (R2-15): the evaluator model picker's "Your Models" rows. RED on base
// 651ab5bb: ModelSelector.jsx:1348 admits a row only when is_available !==
// false and otherwise opens the provider-key drawer with "Add your <X> key",
// so a managed model is blocked, no payer is shown, and a contract
// "unavailable" row can be picked. The pre-contract cases are BYOK regression
// guards (pass on base and after).
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";

const state = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args) },
  endpoints: {
    develop: {
      modelList: "/model-hub/api/models_list/",
      eval: {
        summaryTemplates: "/eval/summary-templates/",
        summaryTemplate: (id) => `/eval/summary-templates/${id}/`,
      },
    },
    falconAI: { connectors: "/falcon-ai/connectors/" },
    knowledge: { list: "/knowledge/list/" },
  },
}));
// Provider-key drawer stub: reports only whether it opened and for which row.
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: ({ open, selectedModel }) =>
    open ? (
      <div data-testid="provider-setup-drawer">
        {selectedModel?.model_name ?? ""}
      </div>
    ) : null,
}));

import ModelSelector from "src/sections/evals/components/ModelSelector";
import {
  BYOK_PAYER_LABEL,
  MANAGED_PAYER_LABEL,
  byokRow,
  legacyRow,
  managedRow,
  unavailableRow,
} from "./modelListContractFixtures";

function serveModelList(rows) {
  state.get.mockImplementation((url) =>
    Promise.resolve(
      url === "/model-hub/api/models_list/"
        ? { data: { results: rows, next: null, current_page: 1 } }
        : { data: { results: [] } },
    ),
  );
}

async function openYourModels(rows) {
  serveModelList(rows);
  const onModelChange = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ModelSelector model="" onModelChange={onModelChange} />
    </QueryClientProvider>,
  );
  // The model-list query is only enabled once the menu opens.
  await userEvent.click(screen.getByText("Select model"));
  return onModelChange;
}

describe("ModelSelector: managed models in Your Models", () => {
  beforeEach(() => {
    state.get.mockReset();
  });

  it("selects a managed model without asking for a provider key", async () => {
    const onModelChange = await openYourModels([
      managedRow(),
    ]);

    await userEvent.click(await screen.findByText("gpt-5.5-mini"));

    expect(onModelChange).toHaveBeenCalledWith("gpt-5.5-mini");
    expect(screen.queryByTestId("provider-setup-drawer")).toBeNull();
  });

  it("labels a managed model as paid by Future AGI credits", async () => {
    await openYourModels([managedRow()]);

    expect(
      await screen.findByText(`openai · ${MANAGED_PAYER_LABEL}`),
    ).toBeInTheDocument();
    expect(screen.queryByText("Add your OpenAI key")).toBeNull();
  });

  it("labels a byok model as paid by the user's provider key and keeps it selectable", async () => {
    const onModelChange = await openYourModels([byokRow()]);

    expect(
      await screen.findByText(`anthropic · ${BYOK_PAYER_LABEL}`),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByText("claude-sonnet-5"));

    expect(onModelChange).toHaveBeenCalledWith("claude-sonnet-5");
  });

  it("fails closed for a row the contract marks unavailable", async () => {
    const onModelChange = await openYourModels([
      unavailableRow({ is_available: true }),
    ]);

    await userEvent.click(await screen.findByText("mistral-large-3"));

    expect(onModelChange).not.toHaveBeenCalled();
    expect(
      await screen.findByTestId("provider-setup-drawer"),
    ).toHaveTextContent("mistral-large-3");
  });
});

describe("ModelSelector: BYOK and pre-contract rows are unchanged", () => {
  beforeEach(() => {
    state.get.mockReset();
  });

  it("asks for the provider key of a pre-contract row without one", async () => {
    const onModelChange = await openYourModels([
      legacyRow({ is_available: false }),
    ]);

    expect(await screen.findByText("Add your OpenAI key")).toBeInTheDocument();
    await userEvent.click(screen.getByText("gpt-4o"));

    expect(onModelChange).not.toHaveBeenCalled();
    expect(
      await screen.findByTestId("provider-setup-drawer"),
    ).toHaveTextContent("gpt-4o");
  });

  it("keeps a pre-contract row with no availability flag selectable and shows only its provider", async () => {
    const onModelChange = await openYourModels([
      { model_name: "gpt-4o", providers: "openai" },
    ]);

    expect(await screen.findByText("openai")).toBeInTheDocument();
    expect(
      screen.queryByText(MANAGED_PAYER_LABEL, { exact: false }),
    ).toBeNull();
    await userEvent.click(screen.getByText("gpt-4o"));

    expect(onModelChange).toHaveBeenCalledWith("gpt-4o");
  });
});
