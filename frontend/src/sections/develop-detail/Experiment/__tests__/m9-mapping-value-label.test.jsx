// M9 (merge round 3, W6 integration integration H-V): a variable mapped to a path no
// option lists (a conversation turn path such as `conversation[-1]`) shows
// the column's name plus the path in the column picker, never the raw
// column id ("c7000000-0000-40…" in integration's a4 walk).
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { toLegacyMapping } from "src/sections/common/Values/faPath";
import VariableColumnSelect from "../VariableColumnSelect";
import { mappingValueLabel } from "../variableMapping";

const CONV = "c7000000-0000-4000-8000-000000000001";
const OPTIONS = [
  { value: CONV, label: "conversation", kind: "json" },
  { value: "c2", label: "customer_tier", kind: "text" },
  {
    value: toLegacyMapping(CONV, "$.turns"),
    label: "conversation.turns",
    path: true,
  },
];

afterEach(cleanup);

describe("mappingValueLabel", () => {
  it("is the listed option's label", () => {
    expect(mappingValueLabel("c2", OPTIONS)).toBe("customer_tier");
    expect(mappingValueLabel(toLegacyMapping(CONV, "$.turns"), OPTIONS)).toBe(
      "conversation.turns",
    );
  });

  it("names the column and the path for a turn path no option lists", () => {
    expect(mappingValueLabel(toLegacyMapping(CONV, "[-1]"), OPTIONS)).toBe(
      "conversation[-1]",
    );
    expect(
      mappingValueLabel(toLegacyMapping(CONV, "[-1:agent]"), OPTIONS),
    ).toBe("conversation[-1:agent]");
    expect(
      mappingValueLabel(toLegacyMapping(CONV, "$.meta.channel"), OPTIONS),
    ).toBe("conversation.meta.channel");
  });

  it("keeps the value's own text when its column is not listed", () => {
    expect(mappingValueLabel("gone::$[-1]", OPTIONS)).toBe("gone::$[-1]");
    expect(mappingValueLabel(null, OPTIONS)).toBe("");
  });
});

describe("VariableColumnSelect with a conversation turn path", () => {
  it("shows conversation[-1], not the column id", () => {
    render(
      <VariableColumnSelect
        variable="user_message"
        value={toLegacyMapping(CONV, "[-1]")}
        ok
        options={OPTIONS}
        onChange={() => {}}
      />,
    );
    const select = screen.getByTestId("column-select-user_message");
    expect(select.textContent).toContain("conversation[-1]");
    expect(select.textContent).not.toContain("c7000000");
  });
});
