import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import EvalFilterPanel from "./EvalFilterPanel";

const panel = vi.hoisted(() => ({ props: null }));

// The shared panel is a double: the test reads the fields it is given.
vi.mock("src/components/filter", () => ({
  FilterPanel: function FilterPanel(props) {
    panel.props = props;
    return null;
  },
}));

// Both typed types have a real editor here, so the filter offers them.
vi.mock("../types/typedViews", () => ({
  typedTabsOf: (types) => types,
}));

const renderPanel = (props = {}) =>
  render(
    <EvalFilterPanel open onClose={vi.fn()} onApply={vi.fn()} {...props} />,
  );

const fieldOf = (id) => panel.props.fields.find((field) => field.id === id);

describe("EvalFilterPanel option labels", () => {
  it("labels typed eval types the way the Eval Type column does", () => {
    renderPanel();
    expect(fieldOf("eval_type").options).toEqual([
      { value: "llm", label: "LLM" },
      { value: "code", label: "Code" },
      { value: "agent", label: "Agent" },
      { value: "dag", label: "Decision tree" },
      { value: "remote", label: "Remote scorer" },
    ]);
  });

  it("drops a locked field and keeps the other labels", () => {
    renderPanel({ lockedFilters: { output_type: ["pass_fail"] } });
    expect(fieldOf("output_type")).toBeUndefined();
    expect(fieldOf("owner").options).toEqual([
      { value: "user", label: "User" },
      { value: "system", label: "System" },
    ]);
  });

  it("applies the raw API values of the picked options", () => {
    const onApply = vi.fn();
    renderPanel({ onApply });
    panel.props.onChange([
      { field: "eval_type", operator: "in", value: ["dag", "llm"] },
      { field: "owner", operator: "equals", value: ["user"] },
    ]);
    expect(onApply).toHaveBeenCalledWith({
      eval_type: ["dag", "llm"],
      owner: "user",
    });
  });
});
