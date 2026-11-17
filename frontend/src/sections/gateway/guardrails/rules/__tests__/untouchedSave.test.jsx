import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DEMO_FEEDBACK_SUMMARY,
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "../../__tests__/helpers";
import { catalogEntry } from "../../catalog";
import { STORED_ONLY_CHECKS } from "../../../constants/storedOnlyChecks";
import RulesView from "../RulesView";

// A rule opened in Rules and saved with nothing changed leaves every value it
// is stored with exactly as stored, whatever shape the section is stored in:
// a threshold off the slider's 0.05 grid, a stage the editor does not show,
// and keys and settings the form never reads. A check the console keeps only
// as stored (constants/storedOnlyChecks.js) has no save: opening it and
// closing it writes nothing, and turning it off sends its name and enabled
// false, nothing else of it.

const { get, post, snack } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snack: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

// Prompt injection, a graded check (it has a threshold control), stored with
// a threshold the slider's 0.05 steps cannot reach, a stage, and a key and a
// setting the form never shows.
const RULE = {
  name: "injection-detector",
  enabled: true,
  action: "warn",
  threshold: 0.33,
  stage: "post",
  mode: "sync",
  future_rule_key: " untouched ",
  config: { future_setting: { keep: " exact " } },
};

// The same check in each shape a section is stored in: a rules list (console
// saves), the policy sync's checks list, and a checks map (older editor saves,
// which name the threshold confidence_threshold).
const SHAPES = {
  "rules list": () => ({ rules: [structuredClone(RULE)] }),
  "checks list": () => ({ checks: [structuredClone(RULE)] }),
  "checks map": () => ({
    checks: {
      "prompt-injection": {
        enabled: true,
        action: "warn",
        confidence_threshold: 0.33,
        stage: "post",
        future_rule_key: " untouched ",
        config: { future_setting: { keep: " exact " } },
      },
    },
  }),
};

let guardrails;

function answer(url) {
  if (url.endsWith("/gateways/gw-1/config/")) {
    return Promise.resolve({
      data: {
        result: {
          guardrail_pipeline: {
            enabled: true,
            fail_open: false,
            mode: "sequential",
          },
        },
      },
    });
  }
  if (url.includes("org-configs/active")) {
    return Promise.resolve({ data: { result: { guardrails } } });
  }
  if (url.includes("guardrail-rules")) {
    return Promise.resolve({ data: { result: DEMO_RULE_STATS } });
  }
  if (url.includes("guardrail-overview")) {
    return Promise.resolve({ data: { result: DEMO_OVERVIEW } });
  }
  if (url.includes("guardrail-feedback/summary")) {
    return Promise.resolve({ data: { result: DEMO_FEEDBACK_SUMMARY } });
  }
  return Promise.reject(new Error(`unexpected GET ${url}`));
}

async function openRow(user, id) {
  const label = catalogEntry(id).label;
  const row = await screen.findByTestId(`row-${id}`);
  await user.click(within(row).getByText(label));
  return screen.findByRole("dialog", { name: label });
}

const writes = () =>
  post.mock.calls.filter(([url]) => !url.includes("test-playground"));

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  snack.mockReset();
  get.mockImplementation(answer);
  post.mockResolvedValue({
    data: { result: { version: 3, gateway_synced: true } },
  });
});

describe("a rule saved with nothing changed keeps every stored value", () => {
  it.each(Object.keys(SHAPES))(
    "%s: the threshold 0.33, the stage and the unshown keys stay as stored",
    async (shape) => {
      guardrails = SHAPES[shape]();
      const stored = JSON.stringify(guardrails);
      const user = userEvent.setup();
      renderPage(<RulesView gatewayId="gw-1" />);
      const drawer = await openRow(user, "prompt-injection");
      expect(
        within(drawer).getByText("Match threshold: 0.33"),
      ).toBeInTheDocument();

      await user.click(within(drawer).getByRole("button", { name: "Save" }));

      if (shape === "rules list") {
        // update-guardrail stores a rules list's rule as sent, so the
        // untouched rule is sent whole, exactly as stored.
        await waitFor(() => expect(writes()).toHaveLength(1));
        const [url, body] = writes()[0];
        expect(url).toBe("/agentcc/gateways/gw-1/update-guardrail/");
        expect(body.name).toBe(RULE.name);
        expect(JSON.stringify(body.config)).toBe(JSON.stringify(RULE));
      } else {
        // A checks list or map is edited key by key: nothing changed, so
        // nothing is sent.
        await waitFor(() =>
          expect(snack).toHaveBeenCalledWith("No changes to save", {
            variant: "info",
          }),
        );
        expect(writes()).toEqual([]);
      }
      expect(JSON.stringify(guardrails)).toBe(stored);
    },
  );
});

// Each stored-only check, stored with values the console has no control for.
const storedOnly = (name) => ({
  name,
  enabled: true,
  action: "warn",
  threshold: 0.33,
  stage: "post",
  mode: "sync",
  config: { endpoint: `https://${name}.partner.invalid/check`, keep: " x " },
  note: "a key the console knows nothing of",
});

describe("a stored-only check keeps its whole stored body", () => {
  it.each([...STORED_ONLY_CHECKS])(
    "%s: opened and closed, nothing is written",
    async (name) => {
      guardrails = { rules: [storedOnly(name)] };
      const stored = JSON.stringify(guardrails);
      const user = userEvent.setup();
      renderPage(<RulesView gatewayId="gw-1" />);
      const drawer = await openRow(user, name);
      expect(
        within(drawer).queryByRole("button", { name: /Save|Apply|Edit/ }),
      ).toBeNull();
      const close = within(drawer)
        .getAllByRole("button", { name: "Close" })
        .find((button) => button.textContent === "Close");
      await user.click(close);
      await waitFor(() =>
        expect(
          screen.queryByRole("dialog", { name: catalogEntry(name).label }),
        ).toBeNull(),
      );
      expect(writes()).toEqual([]);
      expect(JSON.stringify(guardrails)).toBe(stored);
    },
  );

  it.each([...STORED_ONLY_CHECKS])(
    "%s: turned off, only its name and enabled false are sent",
    async (name) => {
      guardrails = { rules: [storedOnly(name)] };
      const user = userEvent.setup();
      renderPage(<RulesView gatewayId="gw-1" />);
      const drawer = await openRow(user, name);
      await user.click(
        within(drawer).getByRole("button", { name: "Turn off" }),
      );
      const confirmation = await screen.findByRole("dialog", {
        name: "Turn off check",
      });
      await user.click(
        within(confirmation).getByRole("button", { name: "Turn off" }),
      );
      await waitFor(() => expect(writes()).toHaveLength(1));
      const [url, body] = writes()[0];
      expect(url).toBe("/agentcc/gateways/gw-1/toggle-guardrail/");
      expect(body).toEqual({ name, enabled: false });
    },
  );
});
