import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent, fireEvent } from "src/utils/test-utils";
import CompositeAxisTabs, {
  axisToLockedFilters,
  COMPOSITE_AXIS_OPTIONS,
} from "../CompositeAxisTabs";

describe("CompositeAxisTabs", () => {
  it("renders a tab for every composite axis option", () => {
    render(<CompositeAxisTabs value="pass_fail" onChange={vi.fn()} />);

    COMPOSITE_AXIS_OPTIONS.forEach((opt) => {
      expect(screen.getByText(opt.label)).toBeInTheDocument();
    });
  });

  it("calls onChange with the clicked tab's value", async () => {
    const onChange = vi.fn();
    render(<CompositeAxisTabs value="pass_fail" onChange={onChange} />);

    await userEvent.click(screen.getByText("Score"));

    expect(onChange).toHaveBeenCalledWith("percentage");
  });

  it("does not call onChange when disabled", () => {
    const onChange = vi.fn();
    render(
      <CompositeAxisTabs value="pass_fail" onChange={onChange} disabled />,
    );

    fireEvent.click(screen.getByText("Score"));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("defaults to the pass_fail tab when no value is provided", () => {
    render(<CompositeAxisTabs onChange={vi.fn()} />);

    const passFailTab = screen.getByRole("tab", { name: /Pass \/ Fail/ });
    expect(passFailTab).toHaveAttribute("aria-selected", "true");
  });
});

describe("axisToLockedFilters", () => {
  it("locks pass_fail children to pass_fail output_type + single template_type", () => {
    expect(axisToLockedFilters("pass_fail")).toEqual({
      output_type: ["pass_fail"],
      template_type: ["single"],
    });
  });

  it("locks percentage children to percentage output_type + single template_type", () => {
    expect(axisToLockedFilters("percentage")).toEqual({
      output_type: ["percentage"],
      template_type: ["single"],
    });
  });

  it("locks choices children to deterministic output_type + single template_type", () => {
    expect(axisToLockedFilters("choices")).toEqual({
      output_type: ["deterministic"],
      template_type: ["single"],
    });
  });

  it("locks code children to the code eval_type + single template_type", () => {
    expect(axisToLockedFilters("code")).toEqual({
      eval_type: ["code"],
      template_type: ["single"],
    });
  });

  it("returns null for an unrecognized axis", () => {
    expect(axisToLockedFilters("unknown")).toBeNull();
    expect(axisToLockedFilters(undefined)).toBeNull();
  });
});
