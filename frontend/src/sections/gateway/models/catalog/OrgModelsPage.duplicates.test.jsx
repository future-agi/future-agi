// A model id two of the org's providers list, on Your models.
import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { endpoints } from "src/utils/axios";

import ModelsSection from "../ModelsSection";
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

const BOTH_PUSHED = [
  {
    id: "north",
    display_name: "North team",
    models: ["shared-model"],
    pushed: true,
  },
  {
    id: "south",
    display_name: "South team",
    models: ["shared-model"],
    pushed: true,
  },
];

// Your models lists one route per provider; when two providers list the same
// id, the page says which one a plain id reaches and how to pick.
describe("Your models: a model id more than one provider lists", () => {
  it("names the providers, where a plain id goes and how to pick", async () => {
    open({ [gw.providers("default")]: providerReply(BOTH_PUSHED) });
    await screen.findByText("north/shared-model");
    const notice = await screen.findByTestId("duplicate-model-notice");
    expect(notice).toHaveTextContent(
      '"shared-model" is listed by north and south.',
    );
    expect(notice).toHaveTextContent(
      'A plain "shared-model" reaches north, the provider whose id sorts first.',
    );
    expect(notice).toHaveTextContent('for example "south/shared-model"');
    // Both routes stay listed.
    expect(screen.getByText("south/shared-model")).toBeInTheDocument();
  });

  it("follows the org's model map entry", async () => {
    open({
      [gw.providers("default")]: providerReply(BOTH_PUSHED),
      [gw.config("default")]: {
        status: true,
        result: { routing: {}, model_map: { "shared-model": "south" } },
      },
    });
    expect(
      await screen.findByText(
        /Your Model Map entry sends a plain "shared-model" to south\./,
      ),
    ).toBeInTheDocument();
  });

  it("says nothing when the second provider is not on the gateway", async () => {
    open();
    await screen.findByText("north/shared-model");
    expect(screen.queryByTestId("duplicate-model-notice")).toBeNull();
  });
});
