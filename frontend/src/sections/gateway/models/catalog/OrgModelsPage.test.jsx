import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";

import ModelsSection from "../ModelsSection";
import OrgModelsPage from "./OrgModelsPage";
import {
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const role = vi.hoisted(() => ({ canEdit: true }));
const context = vi.hoisted(() => ({
  gatewayId: "default",
  organizationId: "org-a",
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: context.organizationId }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: context.gatewayId }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => role,
}));
// Exercise the real query, frame, filter bar, empty states and cell renderers.
// AG Grid's virtualized layout is outside this page's behavior tests.
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(
    // eslint-disable-next-line react/prop-types
    { rowData = [], columnDefs = [] },
    ref,
  ) {
    return (
      <table ref={ref} aria-label="Your models">
        <tbody>
          {rowData.map((data) => (
            <tr key={data.id}>
              {columnDefs.map((column) => {
                const value = data[column.field];
                const Cell = column.cellRenderer;
                return (
                  <td key={column.colId || column.field}>
                    {Cell ? <Cell data={data} value={value} /> : value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }),
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));

const PROVIDERS = [
  {
    id: "north",
    display_name: "North team",
    models: ["shared-model"],
    pushed: true,
  },
  {
    id: "south",
    display_name: "South team",
    models: ["shared-model", "family/embed:v2"],
    pushed: false,
    status_reason: "Configuration has not reached the gateway",
  },
];
const gw = endpoints.gateway;
const providerReply = (providers) => ({ status: true, result: { providers } });
const responses = (overrides = {}) =>
  demoResponses(endpoints, {
    [gw.managedModels]: {
      status: true,
      result: { available: false, enabled: false },
    },
    [gw.providers("default")]: providerReply(PROVIDERS),
    [gw.config("default")]: { status: true, result: { routing: {} } },
    ...overrides,
  });
const open = (overrides, options) => {
  api.get.mockImplementation(answerFrom(responses(overrides)));
  return renderModelsAt(
    "/dashboard/gateway/models",
    <ModelsSection />,
    options,
  );
};
const rowFor = (id) => screen.getByText(id).closest("tr");
const rows = () =>
  within(screen.getByRole("table", { name: "Your models" })).getAllByRole(
    "row",
  );

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  role.canEdit = true;
  context.gatewayId = "default";
  context.organizationId = "org-a";
});
afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
  expect(api.post).not.toHaveBeenCalled();
});

describe("own-provider model discovery", () => {
  it.each(["light", "dark"])(
    "lists distinct qualified routes without a managed offer (%s)",
    async (mode) => {
      const { location } = open({}, { mode });
      expect(
        await screen.findByRole("heading", { level: 1, name: "Your models" }),
      ).toBeInTheDocument();
      await screen.findByText("north/shared-model");
      expect(rows()).toHaveLength(3);
      expect(location.pathname).toBe("/dashboard/gateway/models");
      for (const [id, path] of [
        ["north/shared-model", "/dashboard/gateway/models/north/shared-model"],
        ["south/shared-model", "/dashboard/gateway/models/south/shared-model"],
        [
          "south/family/embed:v2",
          "/dashboard/gateway/models/south/family%2Fembed%3Av2",
        ],
      ]) {
        const row = within(rowFor(id));
        expect(row.getByRole("link", { name: "Overview" })).toHaveAttribute(
          "href",
          path,
        );
        expect(row.getByRole("link", { name: "Try it" })).toHaveAttribute(
          "href",
          `${path}?tab=try`,
        );
        expect(row.getByRole("link", { name: "API" })).toHaveAttribute(
          "href",
          `${path}?tab=api`,
        );
      }
      expect(
        screen.getByRole("link", { name: "Auto routing" }),
      ).toHaveAttribute("href", "/dashboard/gateway/models/auto");
      expect(screen.queryByLabelText("Future AGI credits")).toBeNull();
      expect(
        screen.queryByText(
          /credit balance|add credits|enable credits|provider \+/i,
        ),
      ).toBeNull();
      expect(
        api.get.mock.calls.some(([url]) => url === gw.managedModelsCatalog),
      ).toBe(false);
    },
  );

  it("opens the own-provider index alongside an available managed catalog without fetching catalog metadata", async () => {
    api.get.mockImplementation(answerFrom(responses({
      [gw.managedModels]: { status: true, result: { available: true, enabled: true } },
    })));
    renderModelsAt("/dashboard/gateway/models/your", <ModelsSection />);
    await screen.findByText("north/shared-model");
    expect(rows()).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Catalog" })).toHaveAttribute("href", "/dashboard/gateway/models");
    expect(api.get.mock.calls.some(([url]) => url === gw.managedModelsCatalog)).toBe(false);
    expect(screen.queryByLabelText("Future AGI credits")).not.toBeInTheDocument();

    await userEvent.click(within(rowFor("north/shared-model")).getByRole("link", { name: "Overview" }));
    const back = await screen.findByRole("link", { name: "Your models" });
    expect(back).toHaveAttribute("href", "/dashboard/gateway/models/your");
    await userEvent.click(back);
    await screen.findByText("north/shared-model");
    expect(rows()).toHaveLength(3);
  });

  it("combines case-insensitive multiword search and the provider filter, then clears both", async () => {
    open();
    await screen.findByText("north/shared-model");
    const user = userEvent.setup();
    await user.type(
      screen.getByRole("textbox", { name: "Search your models" }),
      "  SHARED  south ",
    );
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("south/shared-model")).toBeInTheDocument();
    await user.clear(
      screen.getByRole("textbox", { name: "Search your models" }),
    );
    await user.click(screen.getByRole("combobox", { name: "Provider" }));
    await user.click(
      screen.getByRole("option", { name: "North team (north)" }),
    );
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("north/shared-model")).toBeInTheDocument();
    await user.type(
      screen.getByRole("textbox", { name: "Search your models" }),
      "embed",
    );
    expect(await screen.findByText("No models match")).toBeInTheDocument();
    expect(screen.queryByText("No models configured")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(rows()).toHaveLength(3);
    expect(
      screen.getByRole("textbox", { name: "Search your models" }),
    ).toHaveValue("");
    expect(
      screen.getByRole("combobox", { name: "Provider" }),
    ).toHaveTextContent("All providers");
  });

  it("labels unsynced rows not ready and never claims verified capabilities", async () => {
    open();
    await screen.findByText("south/shared-model");
    const row = within(rowFor("south/shared-model"));
    expect(row.getByText("Not ready")).toBeInTheDocument();
    expect(
      row.getByLabelText("Configuration has not reached the gateway"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("north/shared-model")).getByText("Configured"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Model type, pricing and context limits are not reported/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/chat supported|chat capable|ready to use/i),
    ).toBeNull();
  });

  it("shows loading rather than an empty list while providers are pending, then recovers", async () => {
    let resolve;
    open({
      [gw.providers("default")]: () =>
        new Promise((done) => {
          resolve = done;
        }),
    });
    expect(
      await screen.findByRole("status", { name: "Loading your models" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    await act(async () => resolve(providerReply(PROVIDERS)));
    expect(await screen.findByText("north/shared-model")).toBeInTheDocument();
  });

  it("offers Retry for a provider read failure and recovers in place", async () => {
    const { location } = open({
      [gw.providers("default")]: new Error("Provider read failed"),
    });
    expect(
      await screen.findByText("Could not load your models"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    api.get.mockImplementation(answerFrom(responses()));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("north/shared-model")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
  });

  it("does not present a missing providers result as a successful empty read", async () => {
    open({ [gw.providers("default")]: { status: true, result: {} } });
    expect(
      await screen.findByText("Could not load your models"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
  });

  it.each([[], [{ id: "empty", models: [] }]])(
    "offers Add provider only after an empty successful read (%j)",
    async (providers) => {
      const { location } = open({
        [gw.providers("default")]: providerReply(providers),
      });
      expect(
        await screen.findByText("No models configured"),
      ).toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "Add provider" }),
      );
      await waitFor(() =>
        expect(location.pathname).toBe("/dashboard/gateway/providers"),
      );
    },
  );

  it("retains the qualified rows, filters and scroll after a failed refresh, then updates in place", async () => {
    const { client } = open();
    await screen.findByText("south/family/embed:v2");
    await userEvent.click(screen.getByRole("combobox", { name: "Provider" }));
    await userEvent.click(
      screen.getByRole("option", { name: "South team (south)" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search your models" }),
      "embed",
    );
    const table = screen.getByRole("table", { name: "Your models" });
    table.scrollTop = 120;
    api.get.mockImplementation(
      answerFrom(
        responses({
          [gw.providers("default")]: new Error("Refresh failed"),
        }),
      ),
    );
    await act(async () => {
      await client.refetchQueries({ queryKey: ["agentcc-provider-health"] });
    });
    expect(screen.getByRole("table", { name: "Your models" })).toBe(table);
    expect(table.scrollTop).toBe(120);
    expect(
      screen.getByRole("textbox", { name: "Search your models" }),
    ).toHaveValue("embed");
    expect(
      screen.getByRole("combobox", { name: "Provider" }),
    ).toHaveTextContent("South team (south)");
    expect(rows()).toHaveLength(1);
    expect(rowFor("south/family/embed:v2")).toHaveTextContent(
      "Access not verified",
    );
    expect(
      within(rowFor("south/family/embed:v2")).getByRole("link", {
        name: "API",
      }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/south/family%2Fembed%3Av2?tab=api",
    );
    expect(
      screen.getByText(
        /Model type, pricing and context limits are not reported/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    const calls = api.get.mock.calls.length;
    api.get.mockImplementation(
      answerFrom(
        responses({
          [gw.providers("default")]: providerReply(
            PROVIDERS.map((provider) => ({ ...provider, pushed: true })),
          ),
        }),
      ),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Retry provider access" }),
    );
    await waitFor(() =>
      expect(rowFor("south/family/embed:v2")).toHaveTextContent("Configured"),
    );
    expect(screen.getByRole("table", { name: "Your models" })).toBe(table);
    expect(api.get.mock.calls.slice(calls).map(([url]) => url)).toEqual([
      gw.providers("default"),
    ]);
  });

  it("does not show setup copy for a stale empty provider read", async () => {
    const { client } = open({ [gw.providers("default")]: providerReply([]) });
    await screen.findByText("No models configured");
    api.get.mockImplementation(
      answerFrom(
        responses({
          [gw.providers("default")]: new Error("Refresh failed"),
        }),
      ),
    );
    await act(async () => {
      await client.refetchQueries({ queryKey: ["agentcc-provider-health"] });
    });
    expect(screen.queryByText("No models configured")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Retry provider access" }),
    ).toBeInTheDocument();
  });

  it.each(["gateway", "organization"])(
    "clears old rows immediately when the %s changes",
    async (changed) => {
      function ChangingContext() {
        const [, update] = React.useState(0);
        return (
          <>
            <button
              onClick={() => {
                if (changed === "gateway") context.gatewayId = "second";
                else context.organizationId = "org-b";
                update((value) => value + 1);
              }}
            >
              Change context
            </button>
            <OrgModelsPage />
          </>
        );
      }
      api.get.mockImplementation(answerFrom(responses()));
      renderModelsAt("/dashboard/gateway/models", <ChangingContext />);
      await screen.findByText("north/shared-model");
      api.get.mockImplementation(
        answerFrom(
          responses({
            [gw.providers("default")]: new Error("New context unavailable"),
            [gw.providers("second")]: new Error("New context unavailable"),
          }),
        ),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Change context" }),
      );
      expect(screen.queryByText("north/shared-model")).toBeNull();
      expect(
        await screen.findByText("Could not load your models"),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    },
  );

  it("gives read-only members a provider link, not an add action", async () => {
    role.canEdit = false;
    const { location } = open({ [gw.providers("default")]: providerReply([]) });
    expect(await screen.findByText("No models configured")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    expect(screen.getByText(/Ask an Owner or Admin/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "View providers" }),
    );
    expect(location.pathname).toBe("/dashboard/gateway/providers");
  });

  it("keeps the managed-status loading behavior without assuming an own-only deployment", async () => {
    open({ [gw.managedModels]: () => new Promise(() => {}) });
    expect(
      await screen.findByRole("heading", { level: 1, name: "Models" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your models" })).toBeNull();
    expect(screen.queryByLabelText("Future AGI credits")).toBeNull();
    expect(
      api.get.mock.calls.some(([url]) => url === gw.managedModelsCatalog),
    ).toBe(false);
  });
});
