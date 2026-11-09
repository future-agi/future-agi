/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import ModelSelector from "../ModelSelector";

const state = vi.hoisted(() => ({
  save: { mutate: vi.fn(), isPending: false },
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  // No saved templates are returned, so only the Save mutation is exercised.
  useMutation: () => state.save,
}));
// Reuse the hosted selector's capability and HTTP seams; no provider is called.
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { FALCON_AI: "falcon_ai", TURING_MODELS: "turing_models" },
  useFeatureAllowed: () => ({
    allowed: false,
    isLoading: false,
    isError: false,
  }),
  useFeatureLocked: () => ({ locked: true, isLoading: false }),
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: { results: [] } }),
    post: vi.fn(() => {
      throw new Error("Unexpected HTTP call in summary pending test");
    }),
  },
  endpoints: {
    falconAI: { connectors: "/falcon-ai/connectors/" },
    knowledge: { list: "/knowledge/list/" },
    develop: {
      modelList: "/model-hub/api/models_list/",
      eval: { summaryTemplates: "/eval/summary-templates/" },
    },
  },
}));

beforeEach(() => {
  state.save.isPending = false;
  state.save.mutate.mockClear();
});
afterEach(cleanup);

describe("summary-template pending state", () => {
  it("disables Save only for pending or invalid input and restores valid idle saving", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const selector = () => (
      <QueryClientProvider client={client}>
        <ModelSelector model="" onModelChange={vi.fn()} />
      </QueryClientProvider>
    );
    const { rerender } = render(selector());
    fireEvent.click(screen.getByRole("button", { name: "Evaluation options" }));
    fireEvent.click(await screen.findByText("Summary"));
    fireEvent.click(screen.getByText("Create custom template"));
    const name = screen.getByPlaceholderText("Template name");
    const criteria = screen.getByPlaceholderText(/^Summary criteria/);
    // The existing check-mark IconButton has no text label. Identify its icon
    // through the test-only Iconify seam without altering production markup.
    const save = screen.getByTestId("mdi:check").closest("button");
    expect(save).toBeDisabled();
    fireEvent.change(name, { target: { value: "Release summary" } });
    expect(save).toBeDisabled();
    fireEvent.change(criteria, {
      target: { value: "Summarize accuracy issues." },
    });
    expect(save).toBeEnabled();
    expect(state.save).not.toHaveProperty("isLoading");

    state.save.isPending = true;
    rerender(selector());
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(state.save.mutate).not.toHaveBeenCalled();

    state.save.isPending = false;
    rerender(selector());
    expect(save).toBeEnabled();
    fireEvent.change(name, { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.change(name, { target: { value: "Release summary" } });
    fireEvent.change(criteria, { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.change(criteria, {
      target: { value: "Summarize accuracy issues." },
    });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(state.save.mutate).toHaveBeenCalledExactlyOnceWith({
      name: "Release summary",
      criteria: "Summarize accuracy issues.",
    });
  });
});
