import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import WidgetGroupByControl, { buildWidgetGroupBy } from "../WidgetGroupByControl";

const attribute = { id: " customer.tier ", type: "custom_attribute", source: "traces" };

describe("widget group by", () => {
  it("uses the existing catalog picker and emits an attribute config", () => {
    const onSelect = vi.fn();
    const onChange = vi.fn();
    render(<WidgetGroupByControl breakdowns={[attribute]} limit={null} onSelect={onSelect} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Group by" }));
    expect(onSelect).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("checkbox", { name: "Top values + other" }));
    expect(onChange).toHaveBeenCalledWith(5);
    expect(buildWidgetGroupBy([attribute], 5)).toEqual({ type: "attribute", key: " customer.tier ", limit: 5 });
  });

  it("emits tag configuration and bounds the requested series count", () => {
    const onChange = vi.fn();
    render(<WidgetGroupByControl breakdowns={[{ id: "tag", type: "system_metric", source: "traces" }]} limit={5} onSelect={vi.fn()} onChange={onChange} />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "Top values" }), { target: { value: "10" } });
    expect(onChange).toHaveBeenCalledWith(10);
    fireEvent.change(screen.getByRole("spinbutton", { name: "Top values" }), { target: { value: "11" } });
    expect(onChange).not.toHaveBeenCalledWith(11);
    expect(buildWidgetGroupBy([{ id: "tag", type: "system_metric" }], 10)).toEqual({ type: "tag", key: "tag", limit: 10 });
  });

  it("preserves existing breakdowns unless explicitly enabled", () => {
    expect(buildWidgetGroupBy([attribute], null)).toBeUndefined();
    expect(buildWidgetGroupBy([attribute, attribute], 5)).toBeUndefined();
    expect(buildWidgetGroupBy([{ id: "model", type: "system_metric" }], 5)).toBeUndefined();
    expect(buildWidgetGroupBy([{ ...attribute, source: "datasets" }], 5)).toBeUndefined();
  });
});
