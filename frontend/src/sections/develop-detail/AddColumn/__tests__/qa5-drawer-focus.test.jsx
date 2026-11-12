import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "src/utils/test-utils";
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", async (original) => ({
  ...(await original()),
  useDatasetColumnConfig: () => [],
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: () => new Promise(() => {}), post: vi.fn() },
}));
import {
  useAddColumnApiCallStore,
  useRetrievalStore,
  useExtractEntitiesStore,
  useClassificationStore,
  useConditionalNodeStore,
} from "../../states";
import AddColumnApiCall from "../AddColumnApiCall/AddColumnApiCall";
import Retrieval from "../Retrieval/Retrieval";
import ExtractEntities from "../ExtractEntities/ExtractEntities";
import Classification from "../Classification/Classification";
import ConditionalNode from "../ConditionalNode/ConditionalNodeV2";

const cases = [
  [
    "API calls",
    AddColumnApiCall,
    useAddColumnApiCallStore,
    "openAddColumnApiCall",
  ],
  ["Retrieval", Retrieval, useRetrievalStore, "openRetrieval"],
  [
    "Extract entities",
    ExtractEntities,
    useExtractEntitiesStore,
    "openExtractEntities",
  ],
  [
    "Classification",
    Classification,
    useClassificationStore,
    "openClassification",
  ],
  [
    "Conditional node",
    ConditionalNode,
    useConditionalNodeStore,
    "openConditionalNode",
  ],
];
afterEach(() =>
  act(() =>
    cases.forEach(([, , store, key]) => store.setState({ [key]: false })),
  ),
);

it.each(cases)(
  "%s enters a named drawer even while loading, and Escape closes from its sentinel",
  async (name, Component, store, key) => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <button
          type="button"
          onClick={() =>
            store.setState({ [key]: { editId: "offline-fixture" } })
          }
        >
          Open form
        </button>
        <Component />
      </QueryClientProvider>,
    );
    const trigger = screen.getByRole("button", { name: "Open form" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name });
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true),
    );
    // Covers the original Conditional Node evidence at sentinelEnd, not only inputs.
    const end = dialog
      .closest(".MuiModal-root")
      .querySelector('[data-testid="sentinelEnd"]');
    if (end) end.focus();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name })).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  },
);
