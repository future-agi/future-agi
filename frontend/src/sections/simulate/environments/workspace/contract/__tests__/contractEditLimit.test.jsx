import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import ContractEditDialog from "../ContractEditDialog";

const renderAdd = (onConfirm = vi.fn()) => {
  render(
    <ContractEditDialog
      edit={{ target: "rule", op: "add", index: 0 }}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );
  return onConfirm;
};

describe("ContractEditDialog length limit", () => {
  it("counts a rule's characters against the limit", () => {
    renderAdd();
    fireEvent.change(screen.getByLabelText("Rule"), {
      target: { value: "Be brief" },
    });
    expect(screen.getByText("8 / 2,000 characters")).toBeInTheDocument();
  });

  it("says a rule over the limit is too long and keeps it from being sent", () => {
    const onConfirm = renderAdd();
    fireEvent.change(screen.getByLabelText("Rule"), {
      target: { value: "x".repeat(2001) },
    });
    expect(screen.getByText("Up to 2,000 characters")).toBeInTheDocument();
    expect(screen.getByLabelText("Rule")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
