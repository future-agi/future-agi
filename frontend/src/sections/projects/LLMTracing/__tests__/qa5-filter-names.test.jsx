import React from "react";
import { expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import TraceFilterPanel from "../TraceFilterPanel";

it("names each condition's operator and removal button", () => {
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  const view = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TraceFilterPanel
        open
        anchorEl={anchor}
        onClose={vi.fn()}
        onApply={vi.fn()}
        source="dataset"
        showAi={false}
        showQueryTab={false}
        properties={[
          {
            id: "question",
            name: "Question",
            category: "system",
            type: "string",
          },
        ]}
        currentFilters={[
          {
            field: "question",
            fieldCategory: "system",
            operator: "equals",
            value: "",
            valueTypes: [],
          },
        ]}
        ValuePickerOverride={() => <input aria-label="Condition value" />}
      />
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("combobox", { name: "Condition 1 operator" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Remove condition 1" }),
  ).toBeInTheDocument();
  view.unmount();
  anchor.remove();
});
