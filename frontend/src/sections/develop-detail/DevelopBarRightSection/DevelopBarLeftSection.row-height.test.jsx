import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRef } from "react";
import { expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { DevelopDetailContext } from "../Context/DevelopDetailContext";
import DevelopBarLeftSection from "./DevelopBarLeftSection";

vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: () => ({
    queryKey: ["dataset-detail"],
    enabled: false,
    initialData: { data: { result: { table: [{}, {}] } } },
  }),
  useGetColumns: () => [],
  useDevelopDatasetList: () => ({ data: [] }),
}));

it("resizes the loaded rows and notifies the grid when a height is selected", async () => {
  const nodes = [{ setRowHeight: vi.fn() }, { setRowHeight: vi.fn() }];
  const api = {
    forEachNode: (visit) => nodes.forEach(visit),
    onRowHeightChanged: vi.fn(),
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DevelopDetailContext.Provider value={{ gridApi: { current: api } }}>
        <DevelopBarLeftSection resizerRef={createRef()} />
      </DevelopDetailContext.Provider>
    </QueryClientProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "Column Size" }));
  await userEvent.click(screen.getByRole("button", { name: "Medium" }));

  for (const node of nodes) expect(node.setRowHeight).toHaveBeenCalledWith(140);
  expect(api.onRowHeightChanged).toHaveBeenCalledTimes(1);
});
