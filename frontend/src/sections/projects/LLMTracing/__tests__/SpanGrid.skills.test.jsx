import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { RanAfterLoadingCell, SkillLoadNameCell } from "../SpanGrid";

vi.mock("src/components/iconify", () => ({ default: (props) => <span aria-label={props["aria-label"]} /> }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/styles/clean-data-table.css", () => ({}));

describe("span list skill cells (SKILL-TRACES §7.1)", () => {
  it("renders Ran after loading from fi.skills.refs, three chips then +N", () => {
    render(<RanAfterLoadingCell value={JSON.stringify(["refund-policy@v3", "tone@v1", "shipping@v2", "escalation@v5"])} />);
    expect(screen.getByText("refund-policy v3")).toBeInTheDocument();
    expect(screen.getByText("shipping v2")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("renders nothing for a span without the list", () => {
    const { container } = render(<RanAfterLoadingCell value={null} />);
    expect(container.textContent).toBe("");
  });

  it("marks a skill.load row with the skill glyph", () => {
    render(
      <SkillLoadNameCell
        value="skill.load"
        data={{ span_name: "skill.load", status: "ERROR" }}
        colDef={{ context: { sourceColumn: { id: "span_name" } } }}
        applyQuickFilters={vi.fn()}
      />,
    );
    expect(screen.getAllByLabelText("Skill load").length).toBeGreaterThan(0);
    expect(screen.getByText("skill.load")).toBeInTheDocument();
  });
});
