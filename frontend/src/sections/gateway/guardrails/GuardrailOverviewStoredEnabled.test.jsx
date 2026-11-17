// The release enabled-state matrix on the v2 Rules view (which replaces Overview).
import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { MockAgGrid, renderPage } from "./__tests__/helpers";
import RulesView from "./rules/RulesView";
import { catalogEntry } from "./catalog";
const h = vi.hoisted(() => ({ section: {}, get: vi.fn(), post: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("src/utils/axios", async (original) => {
  const actual = /** @type {typeof import("src/utils/axios")} */ (
    await original()
  );
  return {
    ...actual,
    default: { ...actual.default, get: h.get, post: h.post },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));
const NAMES = [
  "llama-guard",
  "azure-content-safety",
  "presidio-pii",
  "bedrock-guardrails",
  "hiddenlayer-guard",
  "aporia-guard",
  "pangea-guard",
  "dynamoai-guard",
  "enkrypt-guard",
  "ibm-ai-detector",
  "grayswan-guard",
  "lasso-guard",
  "crowdstrike-aidr",
  "zscaler-guard",
  "tool-permissions",
];
function section(shape, name, fields) {
  const entries = [
    { name, action: "block", config: {}, ...fields },
    { name: "pii-detection", enabled: true, action: "block", config: {} },
  ];
  if (shape === "rules") return { rules: entries };
  if (shape === "list") return { checks: entries };
  return {
    checks: Object.fromEntries(
      entries.map(({ name: id, ...value }) => [id, value]),
    ),
  };
}
beforeEach(() => {
  h.post.mockReset();
  h.get.mockReset().mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url.includes("org-configs/active")
          ? { guardrails: h.section }
          : [],
      },
    }),
  );
});
async function check(shape, name, fields, on) {
  h.section = section(shape, name, fields);
  const before = JSON.stringify(h.section);
  renderPage(<RulesView gatewayId="gateway-1" />);
  const row = await screen.findByTestId(`row-${name}`);
  expect(within(row).getByText(on ? "On" : "Off")).toBeInTheDocument();
  expect(within(row).queryByRole("checkbox")).toBeNull();
  // The v2 strip replaces the old Active/Disabled tiles, with the same count.
  expect(screen.getByText(on ? "2 of 2" : "1 of 2")).toBeInTheDocument();
  expect(
    within(screen.getByTestId("row-pii-detection")).getByRole("checkbox"),
  ).toBeChecked();
  fireEvent.click(within(row).getByText(catalogEntry(name).label));
  const drawer = await screen.findByRole("dialog", {
    name: catalogEntry(name).label,
  });
  expect(drawer).toHaveTextContent("Read-only");
  const off = within(drawer).queryByRole("button", { name: "Turn off" });
  if (on) expect(off).toBeEnabled();
  else expect(off).toBeNull();
  expect(within(drawer).queryByRole("checkbox")).toBeNull();
  expect(JSON.stringify(h.section)).toBe(before);
  expect(h.post).not.toHaveBeenCalled();
}
describe("null enabled is Off in every stored-only check and shape", () => {
  it.each(
    ["rules", "list", "map"].flatMap((shape) =>
      NAMES.map((name) => [shape, name]),
    ),
  )("%s: %s", async (shape, name) => {
    await check(shape, name, { enabled: null }, false);
  });
});
describe("absent, true, false and null", () => {
  it.each([
    ["rules", {}, true],
    ["rules", { enabled: true }, true],
    ["rules", { enabled: false }, false],
    ["rules", { enabled: null }, false],
    ["list", {}, true],
    ["list", { enabled: true }, true],
    ["list", { enabled: false }, false],
    ["list", { enabled: null }, false],
    ["map", {}, false],
    ["map", { enabled: true }, true],
    ["map", { enabled: false }, false],
    ["map", { enabled: null }, false],
  ])("%s %j: On=%s", async (shape, fields, on) => {
    await check(shape, "grayswan-guard", fields, on);
  });
});
