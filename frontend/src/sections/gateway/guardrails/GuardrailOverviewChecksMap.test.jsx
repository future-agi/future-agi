import React, { forwardRef } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { MockAgGrid, renderPage } from "./__tests__/helpers";
import RulesView from "./rules/RulesView";

// The switch and Save are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. RulesView.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// The Rules view's switch and rule drawer on a section stored as a checks
// map. The gateway runs a map check stored without an action as log, and
// without a threshold at 0, and update-guardrail edits such a section in the
// map (futureagi/agentcc/views/gateway.py; its contract takes only enabled,
// action, confidence_threshold and config on a check). So the drawer opens on
// what the gateway runs, and sends the map's shape: the keys the admin
// changed, by the map's names. A section stored as rules is edited as rules,
// and no stage is chosen or added: the gateway never receives one.

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
let mockGuardrails;

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

function answer(url) {
  if (url.includes("org-configs/active")) {
    return Promise.resolve({
      data: { result: { guardrails: mockGuardrails } },
    });
  }
  return Promise.resolve({ data: { result: [] } });
}

const renderRules = () => renderPage(<RulesView gatewayId="gateway-1" />);
const row = (id) => screen.findByTestId(`row-${id}`);

async function openRule(id, label) {
  fireEvent.click(within(await row(id)).getByText(label));
  return screen.findByRole("dialog", { name: label });
}

async function pickAction(dialog, name) {
  fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /Action/ }));
  fireEvent.click(await screen.findByRole("option", { name }));
}

const updateGuardrailCalls = () =>
  post.mock.calls.filter(([url]) => url.endsWith("/update-guardrail/"));

describe("Guardrails rules, a section stored as a checks map", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    get.mockImplementation(answer);
    post.mockResolvedValue({ data: { result: { version: 2 } } });
    mockGuardrails = {
      checks: {
        "data-leakage-prevention": { enabled: true },
        "pii-detection": {
          enabled: true,
          action: "mask",
          confidence_threshold: 0.6,
        },
      },
    };
  });

  it("opens a check stored without an action or a threshold on log and 0", async () => {
    renderRules();
    const leak = await row("data-leakage-prevention");
    expect(within(leak).getByText("Log")).toBeInTheDocument();
    expect(within(leak).getByText("0.00")).toBeInTheDocument();
    const dialog = await openRule(
      "data-leakage-prevention",
      "Data Leakage Prevention",
    );

    expect(
      within(dialog).getByRole("combobox", { name: /Action/ }),
    ).toHaveTextContent("Log");
    expect(
      within(dialog).getByText("Match threshold: 0.00"),
    ).toBeInTheDocument();
  });

  it("sends the map's shape: only the key the admin changed", async () => {
    renderRules();
    const dialog = await openRule(
      "data-leakage-prevention",
      "Data Leakage Prevention",
    );
    await pickAction(dialog, /^Block/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateGuardrailCalls()).toHaveLength(1));
    expect(updateGuardrailCalls()[0][1]).toEqual({
      name: "data-leakage-prevention",
      config: { action: "block" },
    });
  });

  it("sends a changed threshold as the map's confidence_threshold when the score varies", async () => {
    mockGuardrails.checks["pii-detection"].config = { max_entities: 10 };
    renderRules();
    const dialog = await openRule("pii-detection", "PII Detection");

    expect(
      within(dialog).getByText("Match threshold: 0.60"),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole("slider"), {
      target: { value: "0.4" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateGuardrailCalls()).toHaveLength(1));
    expect(updateGuardrailCalls()[0][1]).toEqual({
      name: "pii-detection",
      config: { confidence_threshold: 0.4 },
    });
  });

  it("sends nothing when the admin changed nothing", async () => {
    renderRules();
    const dialog = await openRule("pii-detection", "PII Detection");
    // pii-detection can mask, so its stored mask is offered as itself.
    expect(
      within(dialog).getByRole("combobox", { name: /Action/ }),
    ).toHaveTextContent("Mask");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "PII Detection" }),
      ).toBeNull(),
    );
    expect(updateGuardrailCalls()).toHaveLength(0);
  });

  it("toggles a check by its name in the map", async () => {
    renderRules();
    fireEvent.click(
      within(await row("data-leakage-prevention")).getByRole("checkbox"),
    );

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gateway-1/toggle-guardrail/",
    );
    expect(post.mock.calls[0][1]).toEqual({
      name: "data-leakage-prevention",
      enabled: false,
    });
  });

  it("control: a section stored as rules is edited as a rule, with no stage chosen or added", async () => {
    mockGuardrails = {
      rules: [{ name: "pii-detector", enabled: true, action: "mask" }],
    };
    renderRules();
    const dialog = await openRule("pii-detection", "PII Detection");
    expect(within(dialog).queryByLabelText(/Stage/)).toBeNull();
    expect(within(dialog).queryByText("post")).toBeNull();
    expect(within(dialog).queryByText("both")).toBeNull();
    await pickAction(dialog, /^Block/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateGuardrailCalls()).toHaveLength(1));
    // Only Action changed. The threshold, mode and config stay absent;
    // displaying their defaults is not an instruction to store them.
    expect(updateGuardrailCalls()[0][1]).toEqual({
      name: "pii-detector",
      config: {
        name: "pii-detector",
        enabled: true,
        action: "block",
      },
    });
  });
});
