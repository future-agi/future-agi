import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { endpoints } from "src/utils/axios";
import ModelCatalogPage from "../catalog/ModelCatalogPage";
import { answerFrom, demoResponses, renderModelsAt } from "../test-fixtures/harness";
import { parseCompareQuery } from "./compareQuery";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const grid = vi.hoisted(() => ({ props: null }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()), default: { get: api.get, post: api.post },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Owner" }) }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", gateway: { id: "default" } }),
}));
vi.mock("src/hooks/use-responsive", () => ({ useResponsive: () => false }));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  return { AgGridReact: forwardRef(function Grid(props, _ref) {
    grid.props = props;
    return <MockAgGrid {...props} />;
  }) };
});

const ids = ["openai/alpha", "openai/beta", "openai/gamma", "openai/delta"];
const models = ids.map((id, index) => ({
  id, vendor: "openai", name: `Example ${index + 1}`, state: "active",
  prices: { prompt: 0.000002, completion: 0.000004 },
  billed_input_per_mtok: 2.1, billed_output_per_mtok: 4.2,
  context_length: index === 1 ? null : 128000,
}));
const renderPage = (search = "", overrides = {}) => {
  api.get.mockImplementation(answerFrom(demoResponses(endpoints, {
    [endpoints.gateway.managedModelsCatalog]: {
      status: true, result: { markup_percent: 5, providers: [], models },
    },
    [endpoints.gateway.providers("default")]: {
      status: true, result: { providers: [] },
    },
    ...overrides,
  })));
  return renderModelsAt(`/dashboard/gateway/models${search}`, <Page />);
};
function Page() {
  const navigate = useNavigate();
  return <>
    <button onClick={() => navigate(-1)}>History back</button>
    <button onClick={() => navigate(1)}>History forward</button>
    <ModelCatalogPage />
  </>;
}
const checkbox = (id) => screen.getByRole("checkbox", { name: `Compare ${id}` });
const waitForRows = () => screen.findByRole("checkbox", { name: `Compare ${ids[0]}` });

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  window.localStorage.clear();
});

describe("catalog model comparison", () => {
  it("selects up to three rows without navigating and allows replacement", async () => {
    const page = renderPage();
    await waitForRows();
    expect(screen.getByRole("button", { name: "Compare (0/3)" })).toBeDisabled();
    await userEvent.click(checkbox(ids[0]));
    expect(screen.getByRole("button", { name: "Compare (1/3)" })).toBeDisabled();
    await userEvent.click(checkbox(ids[1]));
    await userEvent.click(checkbox(ids[2]));
    expect(checkbox(ids[3])).toBeDisabled();
    // Native AG Grid events also fire on a disabled checkbox's wrapper/icon.
    const target = checkbox(ids[3]).closest("[data-model-compare]");
    act(() => grid.props.onRowClicked({ data: grid.props.rowData[3], event: { target } }));
    expect(page.location.pathname).toBe("/dashboard/gateway/models");
    await userEvent.click(checkbox(ids[1]));
    expect(checkbox(ids[3])).not.toBeDisabled();
    await userEvent.click(checkbox(ids[3]));
    await userEvent.click(screen.getByRole("button", { name: "Compare (3/3)" }));
    expect(await screen.findByRole("dialog", { name: "Compare models" })).toBeInTheDocument();
    expect(parseCompareQuery(page.location.search)).toEqual([ids[0], ids[2], ids[3]]);
  });

  it("keeps selected models across filters and shares/reloads the comparison without new requests", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    const page = renderPage("?sort=newest");
    await waitForRows();
    await userEvent.click(checkbox(ids[0]));
    await userEvent.click(checkbox(ids[1]));
    await userEvent.type(screen.getByRole("textbox", { name: "Search models" }), "Example 4");
    expect(checkbox(ids[3])).toBeInTheDocument();
    const before = api.get.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Compare (2/3)" }));
    const dialog = await screen.findByRole("dialog", { name: "Compare models" });
    expect(within(dialog).getByText("Example 1")).toBeInTheDocument();
    expect(within(dialog).getByText("Example 2")).toBeInTheDocument();
    expect(new URLSearchParams(page.location.search).get("q")).toBe("Example 4");
    expect(new URLSearchParams(page.location.search).get("sort")).toBe("newest");
    await user.click(within(dialog).getByRole("button", { name: "Copy comparison link" }));
    expect(writeText).toHaveBeenCalledWith(new URL(
      page.location.pathname + page.location.search, window.location.origin,
    ).href);
    writeText.mockRestore();
    expect(api.get.mock.calls.length).toBe(before);
    expect(api.post).not.toHaveBeenCalled();
    const url = page.location.search;
    page.unmount();
    renderPage(url);
    const restored = await screen.findByRole("dialog", { name: "Compare models" });
    await waitFor(() => expect(within(restored).getByText("Example 1")).toBeInTheDocument());
    expect(within(restored).getByRole("link", { name: "Try Example 1" })).toHaveAttribute(
      "href", "/dashboard/gateway/models/openai/alpha?tab=try",
    );
    expect(within(restored).getByRole("link", { name: "API Example 2" })).toHaveAttribute(
      "href", "/dashboard/gateway/models/openai/beta?tab=api",
    );
  });

  it("uses catalog prices, reasons for missing facts, and a removable missing model", async () => {
    renderPage(`?compare=${ids[0]},${ids[1]},missing/model`);
    const dialog = await screen.findByRole("dialog", { name: "Compare models" });
    await waitFor(() => expect(within(dialog).getByText("Example 1")).toBeInTheDocument());
    // One price per model: what credits are charged, never the provider's
    // quote ($2.00) or the margin beside it.
    expect(within(dialog).getAllByText("$2.10")).toHaveLength(2);
    expect(within(dialog).getAllByText("$4.20")).toHaveLength(2);
    expect(within(dialog).queryAllByText("$2.00")).toHaveLength(0);
    expect(dialog).not.toHaveTextContent(/markup|provider \+|%/i);
    expect(within(dialog).getByText(/context window is not reported/i)).toBeInTheDocument();
    expect(within(dialog).getAllByText(/not in the loaded catalog/i).length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText(/health is not included in the catalog/i).length).toBeGreaterThan(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove missing/model" }));
    expect(within(dialog).queryByText("missing/model")).toBeNull();
  });

  it("closes with Escape and restores the URL on browser back", async () => {
    const page = renderPage(`?sort=newest&compare=${ids[0]},${ids[1]}`);
    await screen.findByRole("dialog", { name: "Compare models" });
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(parseCompareQuery(page.location.search)).toEqual([]);
    expect(new URLSearchParams(page.location.search).get("sort")).toBe("newest");
    await userEvent.click(screen.getByRole("button", { name: "History back" }));
    expect(await screen.findByRole("dialog", { name: "Compare models" })).toBeInTheDocument();
    expect(parseCompareQuery(page.location.search)).toEqual(ids.slice(0, 2));
  });

  it("shows catalog and access warnings inside the drawer instead of claiming freshness", async () => {
    renderPage(`?compare=${ids[0]},${ids[1]}`, {
      [endpoints.gateway.providers("default")]: new Error("Provider read failed"),
    });
    const dialog = await screen.findByRole("dialog", { name: "Compare models" });
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent(/own-key access is not verified/i));
  });
});
