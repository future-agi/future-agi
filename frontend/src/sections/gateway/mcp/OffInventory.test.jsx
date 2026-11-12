import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { SavedTools } from "./OffInventory";
import { renderMcp } from "./testing/renderMcp";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: api.get, post: api.post },
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("./testing/agGridMock")).MockAgGrid,
}));
const clients = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.clearAllMocks();
});

async function openCached(tool) {
  const view = renderMcp(<SavedTools gatewayId="gw-cached" />);
  clients.push(view.client);
  await act(async () => {
    view.client.setQueryData(["agentcc-mcp-tools", "gw-cached"], [tool]);
  });
  return view;
}

const TOOL = {
  name: "alpha__read",
  server_id: "alpha",
  upstream_name: "read",
  description: "Read a saved record",
  annotations: { readOnlyHint: true, destructiveHint: false },
};

describe("RL-1009 cached tool inspection", () => {
  it("opens read-only cached annotations with an accessible control and no live requests", async () => {
    await openCached(TOOL);
    const inspect = await screen.findByRole("button", {
      name: "Inspect alpha__read",
    });
    expect(inspect).toBeEnabled();
    act(() => inspect.focus());
    expect(inspect).toHaveFocus();
    fireEvent.click(inspect);
    const detail = await screen.findByRole("dialog", { name: "alpha__read" });
    expect(within(detail).getByText(TOOL.description)).toBeInTheDocument();
    expect(
      within(detail).getByText(/"readOnlyHint": true/),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(/"destructiveHint": false/),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(/not a current live list/),
    ).toBeInTheDocument();
    expect(within(detail).queryByText("Input schema")).toBeNull();
    expect(
      within(detail).queryByRole("button", { name: /Run|Execute|Refresh/ }),
    ).toBeNull();
    fireEvent.click(within(detail).getByRole("button", { name: "Close" }));
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it.each(["inputSchema", "input_schema"])(
    "displays a %s only when present in the cached row",
    async (field) => {
      await openCached({
        ...TOOL,
        [field]: { type: "object", properties: { query: { type: "string" } } },
      });
      fireEvent.click(
        await screen.findByRole("button", { name: "Inspect alpha__read" }),
      );
      const detail = await screen.findByRole("dialog", { name: "alpha__read" });
      expect(within(detail).getByText("Input schema")).toBeInTheDocument();
      expect(within(detail).getByText(/"query":/)).toBeInTheDocument();
      expect(api.get).not.toHaveBeenCalled();
      expect(api.post).not.toHaveBeenCalled();
    },
  );
});
