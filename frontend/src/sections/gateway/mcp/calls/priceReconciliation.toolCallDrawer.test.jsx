import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp, demoToolCall } from "../testing/renderMcp";
import ToolCallDrawer from "./ToolCallDrawer";
import {
  CREDITS,
  OWN_KEY,
} from "src/sections/gateway/logs/__tests__/priceFixtures";

// Price reconciliation, MCP tool-call drawer: the Billing block shows the one
// amount the org pays on the call's route (the Cost the calls list shows),
// never the upstream cost or the margin beside it.

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { tool_call_explorer: true } } }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));

function billing(call) {
  renderMcp(
    <ToolCallDrawer gatewayId="gw-1" call={call} onClose={() => {}} />,
  );
  // The Billing section: the heading's nearest ancestor holding its list.
  let section = screen.getByRole("heading", { name: "Billing" });
  while (section && !section.querySelector("dl")) section = section.parentElement;
  const list = section.querySelector("dl");
  const terms = [...list.querySelectorAll("dt")];
  const labels = terms.map((dt) => dt.textContent);
  const value = (label) =>
    terms.find((dt) => dt.textContent === label)?.nextElementSibling
      ?.textContent;
  return { labels, value, text: list.textContent };
}

describe("MCP tool-call drawer price reconciliation", () => {
  it("shows a credits call's charged amount only", () => {
    const { labels, value, text } = billing(
      demoToolCall({
        billing_mode: "managed",
        credential_source: "managed",
        upstream_cost_micros: CREDITS.upstreamMicros,
        billed_cost_micros: CREDITS.chargedMicros,
        markup_micros: CREDITS.marginMicros,
      }),
    );
    expect(value("Cost")).toBe(CREDITS.charged);
    expect(labels).not.toContain("Upstream cost");
    expect(labels).not.toContain("Markup");
    expect(text).not.toContain(`${CREDITS.margin}`);
    expect(labels.filter((label) => /cost|billed|markup/i.test(label))).toEqual(
      ["Cost"],
    );
  });

  it("shows an own-key call's provider price", () => {
    const { value, labels } = billing(
      demoToolCall({
        billing_mode: "byok",
        upstream_cost_micros: OWN_KEY.upstreamMicros,
        billed_cost_micros: OWN_KEY.chargedMicros,
        markup_micros: 0,
      }),
    );
    expect(value("Cost")).toBe(OWN_KEY.charged);
    expect(labels.filter((label) => /cost|billed|markup/i.test(label))).toEqual(
      ["Cost"],
    );
  });
});
