import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DEMO_FEEDBACK_SUMMARY,
  DEMO_GUARDRAILS,
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "./helpers";
import GuardrailManagementSection from "../GuardrailManagementSection";
import GuardrailAnalyticsTab from "../GuardrailAnalyticsTab";
import RulesView from "../rules/RulesView";
import RuleDrawer from "../rules/RuleDrawer";
import StoredRuleDrawer from "../rules/StoredRuleDrawer";
import { ruleColumns } from "../rules/rule-columns";
import { catalogEntry } from "../catalog";
import { ruleAsCheck } from "../catalog/rule-model";
import GuardrailTestTab from "../test/GuardrailTestTab";
import {
  chipNeeds,
  gridBudget1440,
  minimumWidth,
} from "../../logs/__tests__/gridFit";

const { get, post, params } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  params: { tab: undefined },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = /** @type {typeof import("src/utils/axios")} */ (
    await importOriginal()
  );
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));
vi.mock("react-apexcharts", () => ({
  default: () => <div data-testid="chart" />,
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => params,
  useNavigate: () => vi.fn(),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));

const BANNER =
  "Guardrails are off on this gateway, so no check runs. Rules you save are stored and run when guardrails are turned on in the gateway config.";

let nodePipeline;

function answer(url) {
  if (url.endsWith("/gateways/gw-1/config/")) {
    return Promise.resolve({
      data: { result: { guardrail_pipeline: nodePipeline } },
    });
  }
  if (url.includes("org-configs/active")) {
    return Promise.resolve({
      data: { result: { guardrails: structuredClone(DEMO_GUARDRAILS) } },
    });
  }
  if (url.includes("guardrail-rules")) {
    return Promise.resolve({ data: { result: DEMO_RULE_STATS } });
  }
  if (url.includes("guardrail-overview")) {
    return Promise.resolve({ data: { result: DEMO_OVERVIEW } });
  }
  if (url.includes("guardrail-trends")) {
    return Promise.resolve({ data: { result: { series: [] } } });
  }
  if (url.includes("guardrail-feedback/summary")) {
    return Promise.resolve({ data: { result: DEMO_FEEDBACK_SUMMARY } });
  }
  return Promise.resolve({ data: { result: {} } });
}

const OFF = { enabled: false, fail_open: false, mode: "sequential" };
const ON = { enabled: true, fail_open: false, mode: "sequential" };

describe("Guardrails on a gateway that runs no check", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    get.mockImplementation(answer);
    params.tab = undefined;
  });

  it("says so at the top of every Guardrails view", async () => {
    nodePipeline = OFF;
    params.tab = "feedback";
    renderPage(<GuardrailManagementSection />);
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
  });

  it("shows no banner while guardrails are on", async () => {
    nodePipeline = ON;
    renderPage(<GuardrailManagementSection />);
    await waitFor(() =>
      expect(get).toHaveBeenCalledWith(
        expect.stringMatching(/\/gateways\/gw-1\/config\/$/),
        expect.anything(),
      ),
    );
    await screen.findAllByText("PII Detection");
    expect(screen.queryByText(BANNER)).toBeNull();
  });

  it("850(a): keeps stored rule switches and Add check enabled without claiming enforcement", async () => {
    nodePipeline = OFF;
    renderPage(<RulesView gatewayId="gw-1" />);
    const pii = await screen.findByTestId("row-pii-detection");
    await waitFor(() =>
      expect(within(pii).getByText("Not enforced")).toBeInTheDocument(),
    );
    const toggle = within(pii).getByRole("checkbox", {
      name: "PII Detection saved on",
    });
    expect(toggle).toBeEnabled();
    expect(toggle).toBeChecked();
    post.mockResolvedValue({ data: { result: { gateway_synced: true } } });
    await userEvent.click(toggle);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        expect.stringMatching(/toggle-guardrail\/$/),
        { name: "pii-detection", enabled: false },
        expect.anything(),
      ),
    );
    const strip = screen.getByRole("group", { name: "Last 7 days" });
    expect(within(strip).queryByText("3 of 4")).toBeNull();
    expect(within(strip).getByText("Not enforced")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add check" })).toBeEnabled();
  });

  it("850(a): creates and saves a rule from the empty off state", async () => {
    nodePipeline = OFF;
    get.mockImplementation((url) =>
      url.includes("org-configs/active")
        ? Promise.resolve({ data: { result: { guardrails: {} } } })
        : answer(url),
    );
    post.mockResolvedValue({ data: { result: { gateway_synced: true } } });
    const user = userEvent.setup();
    renderPage(<RulesView gatewayId="gw-1" />);
    await screen.findByText("No guardrails");
    await user.click(screen.getByRole("button", { name: "Add check" }));
    const catalog = await screen.findByRole("dialog", { name: "Add a check" });
    await user.click(within(catalog).getByText("Prompt Injection"));
    const drawer = await screen.findByRole("dialog", {
      name: "Prompt Injection",
    });
    await user.click(within(drawer).getByRole("button", { name: "Add check" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        expect.stringMatching(/update-guardrail\/$/),
        expect.objectContaining({
          name: "prompt-injection",
          config: expect.objectContaining({ enabled: true }),
        }),
        expect.anything(),
      ),
    );
  });

  it("850(a): keeps the saved-only check turn-off action while the pipeline is off", async () => {
    const onTurnOff = vi.fn().mockResolvedValue({ gateway_synced: true });
    const user = userEvent.setup();
    renderPage(
      <StoredRuleDrawer
        open
        row={{
          id: "mcp-security",
          label: "MCP Security",
          enabled: true,
          check: {},
        }}
        canEdit
        enforced={false}
        onClose={() => {}}
        onTurnOff={onTurnOff}
        onRemove={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    const confirmation = await screen.findByRole("dialog", {
      name: "Turn off check",
    });
    await user.click(
      within(confirmation).getByRole("button", { name: "Turn off" }),
    );
    expect(onTurnOff).toHaveBeenCalledTimes(1);
  });

  it("opens a rule with no promise of a 403", async () => {
    nodePipeline = OFF;
    const user = userEvent.setup();
    renderPage(<RulesView gatewayId="gw-1" />);
    const pii = await screen.findByTestId("row-pii-detection");
    await waitFor(() =>
      expect(within(pii).getByText("Not enforced")).toBeInTheDocument(),
    );
    await user.click(within(pii).getByText("PII Detection"));
    const drawer = await screen.findByRole("presentation");
    expect(within(drawer).getByText(BANNER)).toBeInTheDocument();
    expect(within(drawer).queryByText(/403/)).toBeNull();
    expect(
      within(drawer).getByText(
        "When guardrails are on in the gateway config, refuse the request with a guardrail error.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps each rule's switch and the 403 wording while guardrails are on", async () => {
    nodePipeline = ON;
    renderPage(<RulesView gatewayId="gw-1" />);
    const pii = await screen.findByTestId("row-pii-detection");
    await waitFor(() =>
      expect(within(pii).getByRole("checkbox")).toBeInTheDocument(),
    );
    expect(within(pii).queryByText("Not enforced")).toBeNull();
    expect(screen.getByRole("button", { name: "Add check" })).toBeEnabled();
  });

  it("disables Run test while guardrails are off", async () => {
    nodePipeline = OFF;
    const user = userEvent.setup();
    renderPage(<GuardrailTestTab gatewayId="gw-1" />);
    await user.type(
      screen.getByLabelText(/Prompt/, { selector: "textarea" }),
      "hello",
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run test" })).toBeDisabled(),
    );
  });

  // With history recorded in the window, the analytics stay readable under a
  // line that says no check runs now (GuardrailAnalyticsTab hasHistory).
  it("says guardrails are off and keeps the recorded analytics readable", async () => {
    nodePipeline = OFF;
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText(
        "Guardrails are off on this gateway. No check runs now; recorded history remains available.",
      ),
    ).toBeInTheDocument();
    const strip = await screen.findByRole("group", { name: "Last 7 days" });
    expect(await within(strip).findByText("592")).toBeInTheDocument();
    expect(screen.queryByText(/once requests/)).toBeNull();
  });

  // 850(a): stored-enabled is editable independently of current enforcement.
  it.each([
    ["on", true],
    ["off", false],
  ])(
    "850(a): edits the saved switch of a rule stored %s while still showing Not enforced",
    async (_stored, enabled) => {
      const user = userEvent.setup();
      const onSave = vi.fn((_value) => Promise.resolve());
      renderPage(
        <RuleDrawer
          open
          meta={catalogEntry("prompt-injection")}
          row={{
            id: "prompt-injection",
            label: "Prompt Injection",
            stage: "request",
            // The stored rule as RulesView hands it to the drawer
            // (normalizeGuardrails reads each rule with ruleAsCheck).
            check: ruleAsCheck({
              name: "prompt-injection",
              action: "warn",
              enabled,
              threshold: 0.6,
              config: {},
            }),
            triggers: 0,
            blocked: 0,
            accuracy: null,
          }}
          onClose={() => {}}
          onSave={onSave}
          enforced={false}
        />,
      );
      const drawer = await screen.findByRole("dialog", {
        name: "Prompt Injection",
      });
      const toggle = within(drawer).getByRole("checkbox", {
        name: enabled ? "On in draft" : "Off in draft",
      });
      expect(toggle).toBeEnabled();
      expect(toggle.checked).toBe(enabled);
      await user.click(toggle);
      expect(
        within(drawer).getByRole("checkbox", {
          name: enabled ? "Off in draft" : "On in draft",
        }).checked,
      ).toBe(!enabled);
      expect(within(drawer).queryByText(/^On$|^Off$/)).toBeNull();
      expect(within(drawer).getByText("Not enforced")).toBeInTheDocument();

      await user.click(
        within(drawer).getByRole("combobox", { name: /^Action/ }),
      );
      await user.click(await screen.findByRole("option", { name: /^Log/ }));
      await user.click(within(drawer).getByRole("button", { name: "Save" }));
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onSave.mock.calls[0][0]).toEqual(
        expect.objectContaining({
          enabled: !enabled,
          action: "log",
          threshold: 0.6,
        }),
      );
    },
  );

  it("keeps the rule drawer's on switch while guardrails are on", async () => {
    renderPage(
      <RuleDrawer
        open
        meta={catalogEntry("prompt-injection")}
        row={null}
        onClose={() => {}}
        onSave={() => {}}
      />,
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Prompt Injection",
    });
    expect(within(drawer).getByRole("checkbox")).toBeInTheDocument();
    expect(within(drawer).queryByText("Not enforced")).toBeNull();
  });

  it("says what a block does once guardrails are on, with no promise of a 403", async () => {
    nodePipeline = OFF;
    renderPage(<GuardrailTestTab gatewayId="gw-1" />);
    expect(
      await screen.findByText(
        /^When guardrails are on in the gateway config, a block refuses the request with a guardrail error/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/403/)).toBeNull();
  });

  it("says a block answers HTTP 403 while guardrails are on", async () => {
    nodePipeline = ON;
    renderPage(<GuardrailTestTab gatewayId="gw-1" />);
    await waitFor(() =>
      expect(get).toHaveBeenCalledWith(
        expect.stringMatching(/\/gateways\/gw-1\/config\/$/),
        expect.anything(),
      ),
    );
    expect(
      screen.getByText(
        /^A block answers HTTP 403 with error type guardrail_error/,
      ),
    ).toBeInTheDocument();
  });

  it("shows Not enforced whole in the Status column, and the grid still fits at 1440 px", () => {
    const columns = ruleColumns({
      onToggle: () => {},
      onSetUp: () => {},
      busy: false,
      enforced: false,
    });
    const status = columns.find(
      (col) => "colId" in col && col.colId === "status",
    );
    expect(minimumWidth(status)).toBeGreaterThanOrEqual(
      chipNeeds("Not enforced"),
    );
    const total = columns.reduce((sum, col) => sum + minimumWidth(col), 0);
    expect(total).toBeLessThanOrEqual(gridBudget1440());
  });
});
