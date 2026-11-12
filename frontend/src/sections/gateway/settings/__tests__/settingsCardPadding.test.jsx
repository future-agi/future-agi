import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import BatchJobsTab from "../batch/BatchJobsTab";
import OrgConfigSection from "../OrgConfigSection";
import { SettingsPanel } from "../ui";

vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid() {
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: { id: "cfg-1", version: 3, created_at: "2026-10-20T09:00:00Z" },
    isLoading: false,
    error: null,
  }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ data: { providers: [] } }),
  useGatewayConfig: () => ({ isLoading: false, data: {} }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));
vi.mock("../OrgConfigEditor", () => ({ default: () => null }));
vi.mock("../ConfigHistoryDrawer", () => ({ default: () => null }));
vi.mock("../hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: undefined, isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

const SIDES = ["top", "right", "bottom", "left"];

// A rule's value for `prop`, in declaration order. The style sheet keeps a
// shorthand as written ("padding: 24px"), so a side is read from the
// longhand or from its side of the shorthand, whichever comes last.
function declared(style, prop) {
  const [shorthand, side] = prop.split("-");
  let value = "";
  for (let i = 0; i < style.length; i += 1) {
    const name = style[i];
    if (name === prop) {
      value = style.getPropertyValue(name);
    } else if (name === shorthand && SIDES.includes(side)) {
      const parts = style.getPropertyValue(name).trim().split(/\s+/);
      const [top, right = top, bottom = top, left = right] = parts;
      value = { top, right, bottom, left }[side];
    }
  }
  return value;
}

// jsdom applies no min-width media query, so MUI's responsive sx never shows
// in getComputedStyle. This reads the style sheets instead: the value of
// `prop` on `el` in a window `width` px wide, the most specific matching rule
// winning and then the last (enough for the class and pseudo-class rules
// emotion writes).
function styleAt(el, prop, width) {
  let best = { specificity: -1, value: "" };
  const specificityOf = (selector) =>
    (selector.match(/#/g) || []).length * 100 +
    (selector.match(/[.:[]/g) || []).length;
  const visit = (rules) => {
    Array.from(rules).forEach((rule) => {
      if (rule.media && rule.cssRules) {
        const min = /min-width:\s*([\d.]+)px/.exec(rule.media.mediaText);
        const max = /max-width:\s*([\d.]+)px/.exec(rule.media.mediaText);
        const low = min ? Number(min[1]) : 0;
        const high = max ? Number(max[1]) : Infinity;
        if (width >= low && width <= high) visit(rule.cssRules);
        return;
      }
      const value = rule.style ? declared(rule.style, prop) : "";
      if (!rule.selectorText || !value) return;
      rule.selectorText.split(",").forEach((part) => {
        let matches = false;
        try {
          matches = el.matches(part.trim());
        } catch {
          matches = false;
        }
        const specificity = specificityOf(part);
        if (matches && specificity >= best.specificity) {
          best = { specificity, value };
        }
      });
    });
  };
  Array.from(document.styleSheets).forEach((sheet) => visit(sheet.cssRules));
  return best.value;
}

const WIDTHS = [390, 1024];
const BATCH_CARDS = [
  "Batch API",
  "Open job by ID",
  "Jobs submitted in this view",
];

// The padding SettingsPanel gives the General and Full config tabs' panels.
function referencePadding() {
  render(<SettingsPanel title="Reference panel">Text</SettingsPanel>);
  const panel = screen.getByRole("region", { name: "Reference panel" });
  expect(styleAt(panel, "padding-left", 1024)).not.toBe("");
  return (side, width) => styleAt(panel, `padding-${side}`, width);
}

function expectPaddedLike(reference, el, label) {
  WIDTHS.forEach((width) => {
    SIDES.forEach((side) => {
      expect(
        styleAt(el, `padding-${side}`, width),
        `${label} padding-${side} at ${width}`,
      ).toBe(reference(side, width));
    });
  });
}

// C-04: the four Gateway settings tabs padded their cards 24, 16 and 12 px,
// so the content shifted sideways when switching tabs.
describe("Gateway settings card padding", () => {
  it("pads the Org config panels as SettingsPanel pads the other tabs", () => {
    const reference = referencePadding();
    render(<OrgConfigSection />);
    ["Organization config", "Sections"].forEach((name) => {
      const panel = screen.getByRole("region", { name });
      expectPaddedLike(reference, panel, name);
    });
  });

  it("pads the Batch jobs cards as SettingsPanel pads the other tabs", () => {
    const reference = referencePadding();
    render(<BatchJobsTab config={{ batch: {} }} gatewayId="gateway-1" />);
    BATCH_CARDS.forEach((title) => {
      const content = screen.getByText(title).closest(".MuiCardContent-root");
      expectPaddedLike(reference, content, title);
    });
  });
});

// C-03: the Open job button lined up with the Batch job ID label, about
// 24 px above the field it submits.
describe("Open job by ID form", () => {
  it("puts the Open job button on the field's row from 600 px up, and keeps it full width on a phone", () => {
    render(<BatchJobsTab config={{ batch: {} }} gatewayId="gateway-1" />);
    const form = screen.getByLabelText("Batch job ID").closest("form");
    expect(styleAt(form, "align-items", 1024)).toBe("flex-end");
    expect(styleAt(form, "align-items", 390)).not.toBe("flex-end");
  });
});
