// The release saved-policy contract exercised through v2's real Rules view.
import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MockAgGrid, renderPage } from "./__tests__/helpers";
import RulesView from "./rules/RulesView";
import { catalogEntry } from "./catalog";
const h = vi.hoisted(() => ({
  section: {},
  node: {},
  level: 8,
  get: vi.fn(),
  post: vi.fn(),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: h.level }),
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
const rule = (name, enabled) => ({
  name,
  enabled,
  action: "block",
  mode: "sync",
  config: {},
});
const show = () => renderPage(<RulesView gatewayId="gateway-1" />);
async function open(name) {
  const row = await screen.findByTestId(`row-${name}`);
  fireEvent.click(within(row).getByText(catalogEntry(name).label));
  return screen.findByRole("dialog", { name: catalogEntry(name).label });
}
const toggles = () =>
  h.post.mock.calls.filter(([url]) => url.endsWith("/toggle-guardrail/"));
beforeEach(() => {
  h.level = 8;
  h.node = {};
  h.section = {};
  h.post.mockReset().mockResolvedValue({ data: { result: { version: 2 } } });
  h.get.mockReset().mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url.includes("org-configs/active")
          ? { guardrails: h.section }
          : url.endsWith("/config/")
            ? h.node
            : [],
      },
    }),
  );
});
describe("saved-only checks", () => {
  it.each(NAMES)("%s on: inspect but never edit or enable", async (name) => {
    h.section = { rules: [rule(name, true), rule("pii-detection", true)] };
    show();
    const row = await screen.findByTestId(`row-${name}`);
    expect(within(row).getByText("On")).toBeInTheDocument();
    expect(within(row).queryByRole("checkbox")).toBeNull();
    const drawer = await open(name);
    expect(drawer).toHaveTextContent("Read-only");
    expect(
      within(drawer).getByRole("button", { name: "Turn off" }),
    ).toBeEnabled();
    expect(
      within(drawer).queryByRole("button", {
        name: /Save|Apply|Edit|Add check/,
      }),
    ).toBeNull();
  });
  it.each(NAMES)("%s off: no enable or Turn off", async (name) => {
    h.section = { rules: [rule(name, false)] };
    show();
    expect(
      within(await screen.findByTestId(`row-${name}`)).getByText("Off"),
    ).toBeInTheDocument();
    const drawer = await open(name);
    expect(within(drawer).queryByRole("checkbox")).toBeNull();
    expect(
      within(drawer).queryByRole("button", {
        name: /Turn off|Save|Apply|Edit|Add check/,
      }),
    ).toBeNull();
  });
  it.each(["rules", "list", "map"])(
    "Tool Permissions retains mode and every pattern in %s",
    async (shape) => {
      const tools = ["db_*", "file_write", "a_very_long_pattern_".repeat(5)];
      const entry = {
        ...rule("tool-permissions", true),
        config: { provider: "tool_permission", mode: "allowlist", tools },
      };
      h.section =
        shape === "rules"
          ? { rules: [entry] }
          : shape === "list"
            ? { checks: [entry] }
            : { checks: { "tool-permissions": entry } };
      const before = JSON.stringify(h.section);
      show();
      const drawer = await open("tool-permissions");
      expect(within(drawer).getByText("Mode: allowlist")).toBeInTheDocument();
      expect(
        within(drawer).getByText(`Tool Patterns: ${tools.join(", ")}`),
      ).toBeInTheDocument();
      expect(within(drawer).queryByText("Mode: sync")).toBeNull();
      expect(JSON.stringify(h.section)).toBe(before);
    },
  );
  it("explains why the saved check is read-only", async () => {
    h.section = { rules: [rule("tool-permissions", true)] };
    show();
    expect(await open("tool-permissions")).toHaveTextContent(
      "Read-only rows are checks your organization saved before",
    );
  });
  it("a map entry without enabled is Off", async () => {
    h.section = {
      checks: {
        "tool-permissions": {
          action: "block",
          config: { mode: "denylist", tools: "x" },
        },
      },
    };
    show();
    const drawer = await open("tool-permissions");
    expect(within(drawer).getByText("Off")).toBeInTheDocument();
    expect(
      within(drawer).queryByRole("button", { name: "Turn off" }),
    ).toBeNull();
  });
  it("other supported checks retain their switch and editable drawer", async () => {
    h.section = {
      rules: [
        rule("pii-detection", true),
        {
          ...rule("lakera-guard", false),
          config: {
            api_key: "__encrypted__",
            endpoint: "https://fixture.invalid",
          },
        },
      ],
    };
    show();
    for (const name of ["pii-detection", "lakera-guard"])
      expect(
        within(await screen.findByTestId(`row-${name}`)).getByRole("checkbox"),
      ).toBeInTheDocument();
    const drawer = await open("pii-detection");
    expect(
      within(drawer).getByRole("button", { name: "Save" }),
    ).toBeInTheDocument();
  });
});
describe("850(a): stored editing while off respects organization roles", () => {
  it.each([true, false])(
    "keeps the empty Add check control visible (admin=%s)",
    async (admin) => {
      h.level = admin ? 8 : 3;
      h.node = { guardrail_pipeline: { enabled: false } };
      show();
      await screen.findByText("No guardrails");
      const add = screen.getByRole("button", { name: "Add check" });
      expect(add.disabled).toBe(!admin);
      fireEvent.click(add);
      if (admin)
        expect(
          await screen.findByRole("dialog", { name: "Add a check" }),
        ).toBeInTheDocument();
      else expect(screen.queryByRole("dialog")).toBeNull();
      expect(h.post).not.toHaveBeenCalled();
    },
  );

  it("keeps a member's saved switch and edit form read-only while off", async () => {
    h.level = 3;
    h.node = { guardrail_pipeline: { enabled: false } };
    h.section = { rules: [rule("pii-detection", true)] };
    show();
    const row = await screen.findByTestId("row-pii-detection");
    expect(within(row).getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add check" })).toBeDisabled();
    const drawer = await open("pii-detection");
    expect(
      within(drawer).getByRole("checkbox", { name: "On in draft" }),
    ).toBeDisabled();
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(h.post).not.toHaveBeenCalled();
  });
});

describe("confirmed mutations only", () => {
  it("cancel sends nothing; confirm sends only enabled false", async () => {
    h.section = { rules: [rule("tool-permissions", true)] };
    show();
    const drawer = await open("tool-permissions");
    fireEvent.click(within(drawer).getByRole("button", { name: "Turn off" }));
    let confirmation = await screen.findByRole("dialog", {
      name: "Turn off check",
    });
    expect(confirmation).toHaveTextContent("no way to turn it back on");
    fireEvent.click(
      within(confirmation).getByRole("button", { name: "Cancel" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Turn off check" }),
      ).toBeNull(),
    );
    // MUI hides the underlying drawer until the confirmation transition ends.
    await waitFor(() => expect(confirmation).not.toBeInTheDocument());
    expect(toggles()).toEqual([]);
    fireEvent.click(within(drawer).getByRole("button", { name: "Turn off" }));
    confirmation = await screen.findByRole("dialog", {
      name: "Turn off check",
    });
    fireEvent.click(
      within(confirmation).getByRole("button", { name: "Turn off" }),
    );
    await waitFor(() => expect(toggles()).toHaveLength(1));
    expect(toggles()[0][1]).toEqual({
      name: "tool-permissions",
      enabled: false,
    });
  });
  it("Member cannot turn off or remove", async () => {
    h.level = 3;
    h.section = { rules: [rule("llama-guard", true)] };
    show();
    const drawer = await open("llama-guard");
    for (const name of ["Turn off", "Remove"])
      expect(within(drawer).getByRole("button", { name })).toBeDisabled();
    expect(h.post).not.toHaveBeenCalled();
  });
  it("850(a): keeps the stored Turn off action while guardrails are off", async () => {
    h.node = { guardrail_pipeline: { enabled: false } };
    h.section = { rules: [rule("zscaler-guard", true)] };
    show();
    const drawer = await open("zscaler-guard");
    expect(within(drawer).getByText("Not enforced")).toBeInTheDocument();
    expect(
      within(drawer).getByRole("button", { name: "Turn off" }),
    ).toBeEnabled();
    expect(h.post).not.toHaveBeenCalled();
  });
});
