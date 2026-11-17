/* eslint-disable react/prop-types */
import React, { useState } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import PreviewPane from "../PreviewPane";
import SelectedItemPreview from "../SelectedItemPreview";

const theme = createTheme({ palette: palette("light") });
const wrap = (ui) => render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);

const item = (n, extra = {}) => ({
  temp_id: `t${n}`,
  status: "new",
  needs_review: true,
  kind: "task",
  label: `chat-10${n}`,
  warnings: ["Checks drafted by Falcon: review before running"],
  cells: {
    title: `Session ${n}`,
    source_conversation: {
      turns: [
        { role: "user", content: `Cancel the kettle ${n}` },
        {
          role: "assistant",
          content: "Address updated.",
          tool_calls: [
            { name: "update_address", args: { order_id: "A-8402" } },
          ],
        },
        { role: "user", content: "And the kettle?" },
      ],
      resolution: "Fail",
      resolution_reason: "the kettle was not cancelled",
      failing_turn: 2,
    },
    checks: [
      {
        key: "order_found",
        name: "Order found",
        description: "lookup_order returned A-8402",
        draft: true,
      },
    ],
  },
  ...extra,
});

function Harness({ items }) {
  const [selected, setSelected] = useState(null);
  return (
    <>
      <PreviewPane
        items={items}
        counts={null}
        unchecked={new Set()}
        edits={{}}
        sourceKind="sessions"
        selectedId={selected}
        onSelect={setSelected}
        onReviewDrafts={() => setSelected(items[0].temp_id)}
      />
      <SelectedItemPreview
        item={items.find((i) => i.temp_id === selected) ?? null}
      />
    </>
  );
}

describe("import preview (IT-R4 G5 P3)", () => {
  it("shows one draft notice instead of a warning on every card", () => {
    wrap(<Harness items={[item(1), item(2)]} />);
    expect(screen.getByTestId("add-data-draft-notice")).toHaveTextContent(
      "2 drafts need a quick review: expected replies are empty and checks are drafted.",
    );
    expect(
      screen.queryByLabelText(
        "Checks drafted by Falcon: review before running",
      ),
    ).toBeNull();
  });

  it("clicking a card shows its conversation and drafted checks", () => {
    wrap(<Harness items={[item(1), item(2)]} />);
    expect(screen.queryByTestId("add-data-selected-preview")).toBeNull();
    fireEvent.click(screen.getAllByTestId("add-data-item")[1]);
    const preview = screen.getByTestId("add-data-selected-preview");
    expect(
      within(preview).getByText("Cancel the kettle 2"),
    ).toBeInTheDocument();
    expect(within(preview).getByTestId("tool-call-chip")).toHaveTextContent(
      'update_address(order_id: "A-8402")',
    );
    const checks = within(preview).getByTestId("add-data-selected-checks");
    expect(checks).toHaveTextContent(
      "Order found · lookup_order returned A-8402",
    );
    expect(within(checks).getByText("Draft")).toBeInTheDocument();
  });

  it("arrow keys move the selection; Review drafted checks selects the first", () => {
    wrap(<Harness items={[item(1), item(2)]} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Review drafted checks" }),
    );
    const cards = () => screen.getAllByTestId("add-data-item");
    expect(cards()[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(cards()[0], { key: "ArrowDown" });
    expect(cards()[1]).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByTestId("add-data-selected-preview")).getByText(
        "Cancel the kettle 2",
      ),
    ).toBeInTheDocument();
  });
});
