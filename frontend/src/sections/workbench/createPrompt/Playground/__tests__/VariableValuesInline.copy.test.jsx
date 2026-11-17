import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import VariableValuesInline from "../VariableValuesInline";

// Staging walk (FINDINGS item 8): the Variables line said a variable without
// a value is sent "as written", but the run fills it blank ({{7*7}} came
// back as "CALC END"). The count stays; only the false half changes.
describe("VariableValuesInline: a variable without a value", () => {
  it("says the run leaves it blank, not that it is sent as written", () => {
    render(
      <VariableValuesInline
        names={["7*7"]}
        variableData={{}}
        setVariableData={vi.fn()}
      />,
    );
    const line = screen.getByTestId("prompt-variables-line");
    expect(line).toHaveTextContent(/1 without a value/);
    expect(line).not.toHaveTextContent(/as written/);
    expect(line).toHaveTextContent(/blank/);
  });
});
