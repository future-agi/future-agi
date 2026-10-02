import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ValueSelector from "../ValueSelector";

const { autocompleteProps } = vi.hoisted(() => ({
  autocompleteProps: vi.fn(),
}));

vi.mock("../AutocompleteTextValueSelector", () => ({
  default: (props) => {
    autocompleteProps(props);
    return <div>attribute-value-selector</div>;
  },
}));

describe("ValueSelector", () => {
  it("forwards the selected project to async attribute value lookup", () => {
    render(
      <ValueSelector
        projectId="selected-project"
        definition={{
          propertyId: "final_status",
          asyncOptions: true,
          filterType: { type: "text" },
        }}
        filter={{ filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
    );

    expect(screen.getByText("attribute-value-selector")).toBeInTheDocument();
    expect(autocompleteProps).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: "selected-project" }),
    );
  });
});
