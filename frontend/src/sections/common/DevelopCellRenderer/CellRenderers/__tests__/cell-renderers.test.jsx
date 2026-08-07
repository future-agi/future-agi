/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import DatetimeCellRenderer from "../DatetimeCellRenderer";
import JsonCellRenderer from "../JsonCellRenderer";

vi.mock("@textea/json-viewer", () => ({
  defineDataType: (config) => config,
  JsonViewer: ({ value }) => (
    <pre data-testid="json-viewer">{JSON.stringify(value)}</pre>
  ),
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

const rendererProps = {
  formattedValueReason: () => "",
  valueReason: [],
};

describe("DatetimeCellRenderer", () => {
  it("renders blank date values as blank cells instead of Invalid Date", () => {
    const { rerender } = render(
      <DatetimeCellRenderer value="" {...rendererProps} />,
    );
    expect(screen.queryByText("Invalid Date")).not.toBeInTheDocument();

    rerender(<DatetimeCellRenderer value={null} {...rendererProps} />);
    expect(screen.queryByText("Invalid Date")).not.toBeInTheDocument();
  });

  it("keeps showing Invalid Date for non-empty malformed date values", () => {
    render(<DatetimeCellRenderer value="not-a-date" {...rendererProps} />);

    expect(screen.getByText("Invalid Date")).toBeInTheDocument();
  });

  it("drops the phantom 00:00 clock for a date-only value (#1766)", () => {
    // A date-only string carries no time, so the cell must not invent one.
    // The bug rendered "29/01/2026 00:00" for an uploaded "2026-01-29".
    const { container } = render(
      <DatetimeCellRenderer value="2026-01-29" {...rendererProps} />,
    );

    expect(container.textContent).toMatch(/\d{2}\/\d{2}\/\d{4}/);
    expect(container.textContent).not.toMatch(/\d{1,2}:\d{2}/);
  });

  it("keeps the clock when the value carries a time", () => {
    const { container } = render(
      <DatetimeCellRenderer value="2026-01-29T14:30" {...rendererProps} />,
    );

    expect(container.textContent).toContain("29/01/2026 14:30");
  });

  it("preserves a genuine midnight that was explicitly provided", () => {
    const { container } = render(
      <DatetimeCellRenderer value="2026-01-29T00:00" {...rendererProps} />,
    );

    expect(container.textContent).toContain("29/01/2026 00:00");
  });
});

describe("JsonCellRenderer", () => {
  it("parses valid JSON strings before rendering the JSON viewer", () => {
    render(
      <JsonCellRenderer
        value='{"notes":"hello","count":2}'
        {...rendererProps}
      />,
    );

    expect(screen.getByTestId("json-viewer")).toHaveTextContent(
      '{"notes":"hello","count":2}',
    );
  });

  it("renders blank JSON strings as blank cells", () => {
    render(<JsonCellRenderer value="  " {...rendererProps} />);

    expect(screen.queryByTestId("json-viewer")).not.toBeInTheDocument();
  });

  it("renders non-JSON strings as plain text instead of treating them as viewer errors", () => {
    render(
      <JsonCellRenderer value="plain annotation note" {...rendererProps} />,
    );

    expect(screen.getByText("plain annotation note")).toBeInTheDocument();
    expect(screen.queryByTestId("json-viewer")).not.toBeInTheDocument();
  });
});
