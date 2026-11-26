import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import EvalResultChips from "../Renderers/EvalResultChips";
import CustomTraceHeaderRenderer from "../Renderers/CustomTraceHeaderRenderer";
import { evalColumnMinWidth } from "../evalCellModel";
import { getTraceListColumnDefs } from "../common";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// jsdom has no layout: report every element as overflowing.
const mockOverflow = () => {
  const spies = [
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(200),
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(80),
  ];
  return () => spies.forEach((spy) => spy.mockRestore());
};

const hoverShowsTooltip = async (target, text) => {
  fireEvent.mouseEnter(target);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(text);
  fireEvent.mouseLeave(target);
  await waitFor(() =>
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument(),
  );
};

describe("eval tiles in narrow columns", () => {
  let restore = () => {};
  afterEach(() => restore());

  it("show a cut-off choice label in full on hover", async () => {
    restore = mockOverflow();
    render(
      <EvalResultChips
        value={{ gap_acknowledged: 1, unsupported_answer: 0 }}
        outputType="choices"
      />,
    );
    await hoverShowsTooltip(
      screen.getByText("1 gap_acknowledged"),
      "1 gap_acknowledged",
    );
  });

  it("show a cut-off eval header in full on hover", async () => {
    restore = mockOverflow();
    render(
      <CustomTraceHeaderRenderer
        displayName="Knowledge gap handling"
        column={{
          colDef: {
            context: {
              sourceColumn: {
                groupBy: "Evaluation Metrics",
                targetType: "trace",
              },
            },
          },
        }}
      />,
    );
    await hoverShowsTooltip(
      screen.getByText("Knowledge gap handling"),
      "Knowledge gap handling",
    );
  });

  it("start eval columns wide enough for their header and labels", () => {
    expect(evalColumnMinWidth({ name: "Tone", choices: ["joy"] }, 150)).toBe(
      150,
    );
    const knowledge = evalColumnMinWidth(
      {
        name: "Knowledge gap handling",
        choices: ["answered_from_knowledge", "gap_acknowledged"],
      },
      150,
    );
    expect(knowledge).toBeGreaterThan(200);
    expect(knowledge).toBeLessThanOrEqual(280);
    expect(
      evalColumnMinWidth({ name: "x", choices: ["a".repeat(200)] }, 150),
    ).toBe(280);

    const column = getTraceListColumnDefs({
      id: "gap",
      name: "Knowledge gap handling",
      groupBy: "Evaluation Metrics",
      outputType: "choices",
      choices: ["answered_from_knowledge"],
      isVisible: true,
    });
    expect(column.minWidth).toBe(
      evalColumnMinWidth(
        {
          name: "Knowledge gap handling",
          choices: ["answered_from_knowledge"],
        },
        150,
      ),
    );
    expect(column.minWidth).toBeGreaterThan(200);
  });
});
