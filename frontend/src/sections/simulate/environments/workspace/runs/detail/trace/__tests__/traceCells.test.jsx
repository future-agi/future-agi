import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Field } from "../traceCells";

describe("Field (persona cell attribute)", () => {
  it("wraps a long value instead of cutting it off", () => {
    const traits = "Detail-oriented, Questioning, Anxious, Impatient, Talks over the agent";
    render(<Field icon="solar:tag-linear" label="Traits" value={traits} />);
    const value = screen.getByText(traits);
    expect(getComputedStyle(value).whiteSpace).not.toBe("nowrap");
    expect(getComputedStyle(value).textOverflow).not.toBe("ellipsis");
  });

  it("renders nothing for an empty value", () => {
    const { container } = render(<Field icon="solar:tag-linear" label="Traits" value="" />);
    expect(container).toBeEmptyDOMElement();
  });
});
