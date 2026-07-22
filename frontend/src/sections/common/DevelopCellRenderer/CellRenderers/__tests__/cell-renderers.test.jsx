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
