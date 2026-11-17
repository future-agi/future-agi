import React from "react";
import { expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: { current: null } }),
}));
import RowViewBody from "../RowViewBody";

it("replaces the endless row skeleton with retry and back navigation after a denied/failed read", () => {
  const refetch = vi.fn();
  const close = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RowViewBody
        datasetId="dataset-a"
        rowId="row-a"
        row={null}
        nav={{ isError: true, refetch }}
        onClose={close}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Could not load this row",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  fireEvent.click(screen.getByRole("button", { name: "Back to dataset" }));
  expect(refetch).toHaveBeenCalledOnce();
  expect(close).toHaveBeenCalledOnce();
});
