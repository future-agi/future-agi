import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import InsightEvals from "./insight-evals";
import FailedEvaluationsDialog from "./FailedEvaluationDialog";
import dialogSource from "./FailedEvaluationDialog.jsx?raw";
import insightSource from "./insight-evals.jsx?raw";
import ErrorCellRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/ErrorCellRenderer";
import BillingNoAccess from "src/sections/settings/BillingV2/BillingNoAccess";

// PROPOSED copy: site gate supplies the final words. Keep wire-text expectations
// independent of the production constants so a copy regression fails this suite.
const MESSAGE =
  "This request could not be completed. Try again later; if it keeps happening, contact your administrator.";
const LEGACY_MESSAGE = "Insufficient credits. Please recharge your account.";
const RATE_MESSAGE =
  "Rate limit reached. Please try again later or upgrade your plan.";
const SUPPORT_MESSAGE =
  "Required action could not be performed, please validate the input data. In case of query, please reach out to us at support@futureagi.com";
const state = vi.hoisted(() => ({ mutate: vi.fn() }));

// This existing regression pins the hosted CTA; self-host has a dedicated suite.
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/rate-limit-modal/UpgradeNowModal", () => ({
  default: () => null,
}));
vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({ mutate: state.mutate }),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    post: () => {
      throw new Error("Unexpected API request");
    },
  },
  endpoints: { project: { reRunTracerEvalutation: "/unused" } },
}));
vi.mock(
  "src/components/run-insights/traces-tab/CustomRenderers/CustomChipRenderer",
  () => ({
    default: ({ data, handleErrorClick }) => (
      <button
        type="button"
        onClick={() => handleErrorClick({ ...data, showIcon: true })}
      >
        Inspect failure
      </button>
    ),
  }),
);
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children, title }) => (
    <div>
      <span data-testid="reason">{title}</span>
      {children}
    </div>
  ),
}));
vi.mock(
  "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper",
  () => ({
    tooltipSlotProp: {},
  }),
);
vi.mock("src/utils/constants", () => ({
  ORIGIN_OF_COLUMNS: { EXPERIMENT: "experiment" },
}));

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function mount(element) {
  return render(
    <MemoryRouter initialEntries={["/insights"]}>
      {element}
      <Location />
    </MemoryRouter>,
  );
}

function openError(errorMessage) {
  mount(
    <InsightEvals
      evalMetrics={{ evaluation: { name: "Check", errorMessage } }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Inspect failure" }));
}

beforeEach(() => {
  state.mutate.mockClear();
  vi.stubGlobal("fetch", () => {
    throw new Error("Network disabled for copy tests");
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("proposed usage copy and legacy sentinel compatibility", () => {
  it.each([MESSAGE, LEGACY_MESSAGE])(
    "keeps the Billing CTA and suppresses rerun for %s",
    (message) => {
      openError(message);
      expect(screen.getByText(message)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Add Credits" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Upgrade Plan" })).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Re-run Evaluation" }),
      ).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Open Billing" }));
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/settings/billing",
      );
      expect(state.mutate).not.toHaveBeenCalled();
    },
  );

  it("uses one constants module for both copies of the sentinel", () => {
    for (const source of [dialogSource, insightSource]) {
      expect(source).toContain('from "src/constants/usageCopy"');
      expect(source).not.toContain(LEGACY_MESSAGE);
      expect(source).not.toContain(MESSAGE);
    }
  });

  it.each([
    [RATE_MESSAGE, true, false],
    [SUPPORT_MESSAGE, false, false],
    ["A validation error", false, true],
    ["Insufficient credits", false, true],
    ["", false, false],
  ])(
    "does not change non-credit classification for %s",
    (message, rate, rerun) => {
      openError(message);
      expect(
        Boolean(screen.queryByRole("button", { name: "Upgrade Plan" })),
      ).toBe(rate);
      expect(
        Boolean(screen.queryByRole("button", { name: "Re-run Evaluation" })),
      ).toBe(rerun);
      expect(screen.queryByRole("button", { name: "Open Billing" })).toBeNull();
    },
  );

  it("keeps the dialog CTA controlled by its supplied flag, not message text", () => {
    const view = mount(
      <FailedEvaluationsDialog
        open
        onClose={vi.fn()}
        data={{ errorMessage: MESSAGE, showCreditLimitMessage: false }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Open Billing" })).toBeNull();
    view.unmount();
    mount(
      <FailedEvaluationsDialog
        open
        onClose={vi.fn()}
        data={{ errorMessage: "Other error", showCreditLimitMessage: true }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open Billing" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/settings/billing",
    );
  });

  it("gives Billing access advice that is true in either deployment", () => {
    mount(<BillingNoAccess />);
    expect(
      screen.getByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Ask an organization Owner or Admin for access to Billing.",
      ),
    ).toBeInTheDocument();
  });
});

describe("error-cell usage classification is code-based, not copy-based", () => {
  it.each([MESSAGE, LEGACY_MESSAGE])(
    "preserves the FREE_TIER_LIMIT CTA and pricing destination for %s",
    (reason) => {
      for (const encode of [(value) => value, JSON.stringify]) {
        const onRerun = vi.fn();
        const view = mount(
          <ErrorCellRenderer
            valueReason={reason}
            formattedValueReason={() => reason}
            onRerun={onRerun}
            props={{
              column: { colId: "score" },
              data: {
                score: {
                  value_infos: encode({
                    error_code: "FREE_TIER_LIMIT",
                    reason,
                  }),
                },
              },
            }}
          />,
        );
        expect(screen.getByTestId("reason")).toHaveTextContent(reason);
        expect(screen.getByText("Upgrade for more usage")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Upgrade" }));
        expect(screen.getByTestId("location")).toHaveTextContent(
          "/dashboard/settings/pricing",
        );
        expect(onRerun).not.toHaveBeenCalled();
        view.unmount();
      }
    },
  );

  it.each([MESSAGE, LEGACY_MESSAGE])(
    "does not invent an upgrade CTA from message text alone: %s",
    (reason) => {
      mount(
        <ErrorCellRenderer
          valueReason={reason}
          formattedValueReason={() => reason}
          props={{}}
        />,
      );
      expect(screen.queryByRole("button", { name: "Upgrade" })).toBeNull();
      expect(screen.getByText("Error")).toBeInTheDocument();
    },
  );
});
