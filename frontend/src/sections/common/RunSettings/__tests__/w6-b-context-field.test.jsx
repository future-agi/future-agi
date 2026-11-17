// W6 B5: the retrieved-variable field renders what contextSourceModel
// computes and is not shown without knowledge bases.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        result: {
          table_data: [{ id: "kb1", name: "Refund policy" }],
          total_rows: 1,
        },
      },
    }),
  },
  endpoints: { knowledge: { list: "/model-hub/knowledge-base/get/" } },
}));

const { default: ContextSourceField, ContextSourceFieldView } = await import(
  "../ContextSourceField"
);

const columns = [{ id: "c1", name: "customer_message" }];
afterEach(cleanup);

describe("ContextSourceField", () => {
  it("is not shown without knowledge bases", () => {
    const { container } = render(
      <ContextSourceFieldView
        value={{ variable: "context" }}
        onChange={() => {}}
        variables={["context"]}
        columns={columns}
        kbs={[]}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("names what is missing, then summarizes a complete source", () => {
    const { rerender } = render(
      <ContextSourceFieldView
        value={{ variable: "context" }}
        onChange={() => {}}
        variables={["context"]}
        columns={columns}
        kbs={[{ id: "kb1", name: "Refund policy" }]}
      />,
    );
    expect(screen.getByTestId("context-source-issues").textContent).toBe(
      "Pick a knowledge base. Top must be a whole number of 1 or more. Pick the column the query comes from.",
    );
    rerender(
      <ContextSourceFieldView
        value={{
          variable: "context",
          kb_id: "kb1",
          top_k: "3",
          query_column: "c1",
        }}
        onChange={() => {}}
        variables={["context"]}
        columns={columns}
        kbs={[{ id: "kb1", name: "Refund policy" }]}
      />,
    );
    expect(screen.getByTestId("context-source-summary").textContent).toBe(
      "{{context}} ← Refund policy · top 3 · query from customer_message · each try stores the chunks it sent",
    );
  });

  it("reads the knowledge bases from the KB list", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ContextSourceField
          value={{
            variable: "context",
            kb_id: "kb1",
            top_k: 2,
            query_column: "c1",
          }}
          onChange={() => {}}
          variables={["context"]}
          columns={columns}
        />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("context-source-summary").textContent).toMatch(
        /^\{\{context\}\} ← Refund policy · top 2/,
      ),
    );
  });
});
